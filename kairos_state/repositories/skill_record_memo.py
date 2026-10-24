"""Reuso privado de registros imutáveis, sem autoridade sobre política ou arquivos."""

from __future__ import annotations

import errno
import os
import sqlite3
import stat
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from kairos_skills.mutation_contract import SkillMutationError, SkillMutationRecord, validate_id

if TYPE_CHECKING:
    from kairos_skills.removal_contract import CommonRecord

T = TypeVar("T")


@dataclass(frozen=True)
class DatabaseStamp:
    sql_epoch: tuple
    files: tuple | None
    eligible: bool


def _file_stamps(filename):
    if not filename:
        return None
    entries = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            info = os.stat(filename + suffix, follow_symlinks=False)
        except OSError as error:
            if error.errno == errno.ENOENT:
                if not suffix:
                    return None
                entries.append(None)
                continue
            if error.errno in (errno.EACCES, errno.ENOTDIR, errno.ELOOP):
                return None
            raise SkillMutationError(
                "io", "Não foi possível observar o ledger com segurança."
            ) from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        entries.append(
            (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
        )
    return tuple(entries)


def observe_database_stamp(connection: sqlite3.Connection) -> DatabaseStamp:
    databases = tuple(tuple(row) for row in connection.execute("PRAGMA database_list"))
    schema = connection.execute("PRAGMA main.schema_version").fetchone()[0]
    data = connection.execute("PRAGMA data_version").fetchone()[0]
    uncommitted = connection.execute("PRAGMA read_uncommitted").fetchone()[0]
    epoch = (
        connection.in_transaction,
        connection.total_changes,
        schema,
        data,
        databases,
        uncommitted,
    )
    supported = (
        connection.in_transaction
        and not uncommitted
        and len(databases) == 1
        and databases[0][1] == "main"
    )
    files = _file_stamps(databases[0][2]) if supported else None
    return DatabaseStamp(epoch, files, supported and files is not None)


class RecordMemo:
    def __init__(
        self,
        connection: sqlite3.Connection,
        home_id: str,
        *,
        owner_token: object,
        limit: int = 1024,
    ):
        self._connection = connection
        self._home_id = home_id
        self._owner_token = owner_token
        self._limit = limit
        self._records = {}
        self._enabled = True
        self._stamp = None
        self._batch = None
        self._generation = 0

    @property
    def entry_count(self) -> int:
        return len(self._records)

    def _disable(self):
        self._records.clear()
        self._enabled = False
        self._stamp = None

    def close(self) -> None:
        self._disable()
        self._owner_token = None

    def _refresh(self):
        observed = observe_database_stamp(self._connection)
        if not observed.eligible:
            self._disable()
            return False
        if self._stamp is not None:
            if self._stamp.files != observed.files:
                self._disable()
                return False
            if self._stamp.sql_epoch != observed.sql_epoch:
                self._records.clear()
                self._generation += 1
        self._stamp = observed
        return True

    def _assert_batch_sql_unchanged(self):
        if (
            not self._connection.in_transaction
            or self._connection.total_changes != self._batch.sql_epoch[1]
        ):
            raise SkillMutationError("conflict", "Observação do ledger mudou; operação recusada.")

    def _check_after_load(self, before, loader, record):
        after = observe_database_stamp(self._connection)
        if after.sql_epoch != before.sql_epoch:
            raise SkillMutationError("conflict", "Observação do ledger mudou; operação recusada.")
        if not after.eligible or after.files != before.files:
            self._disable()
            return loader()
        self._stamp = after
        return record

    def load(
        self, operation_id: str, *, modern: bool, loader: Callable[[], CommonRecord | None]
    ) -> CommonRecord | None:
        validate_id(operation_id)
        if not self._enabled or self._owner_token is None:
            return loader()
        try:
            return self._load(operation_id, modern, loader)
        except BaseException:
            self._disable()
            raise

    def _load(self, operation_id, modern, loader):
        if self._batch is None:
            if not self._refresh():
                return loader()
        else:
            self._assert_batch_sql_unchanged()
        key = (
            self._owner_token,
            id(self._connection),
            self._home_id,
            operation_id,
            modern,
            self._generation,
        )
        if key in self._records:
            return self._records[key]
        before = self._stamp
        record = loader()
        if self._batch is None:
            record = self._check_after_load(before, loader, record)
        if (
            self._enabled
            and type(record) is SkillMutationRecord
            and record.draft.home_id == self._home_id
            and self.entry_count < self._limit
        ):
            self._records[key] = record
        return record

    def run_batch(self, operation: Callable[[], T], *, uncached: Callable[[], T]) -> T:
        if not self._enabled or self._owner_token is None or self._batch is not None:
            return uncached()
        try:
            if not self._refresh():
                return uncached()
            before = self._stamp
            self._batch = before
            try:
                result = operation()
                after = observe_database_stamp(self._connection)
            finally:
                self._batch = None
            if after.sql_epoch != before.sql_epoch:
                raise SkillMutationError(
                    "conflict", "Observação do ledger mudou; operação recusada."
                )
            if not self._enabled or not after.eligible or after.files != before.files:
                self._disable()
                return uncached()
            self._stamp = after
            return result
        except BaseException:
            self._disable()
            raise
