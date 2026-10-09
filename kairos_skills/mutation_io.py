"""Descritores, leitura limitada e publicação sem substituição."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillFilesystemEntry,
    SkillMutationError,
    validate_id,
    validate_name,
    validate_skill_creation,
)


@contextmanager
def io_errors(*, kind: str = "io"):
    try:
        yield
    except OSError:
        raise SkillMutationError(
            kind, "Não foi possível acessar os arquivos de skills com segurança."
        ) from None


def open_fd(stack: ExitStack, name, flags: int, *, parent: int | None = None, mode=0o600) -> int:
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
    stack.callback(os.close, fd)
    return fd


def same_entry(parent: int, name: str, fd: int) -> None:
    observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(fd)
    if (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino):
        raise SkillMutationError(
            "conflict", "Entrada alterada durante a operação; arquivos preservados."
        )


def open_directory(stack: ExitStack, path: Path) -> tuple[int, list[tuple[int, str, int]]]:
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise SkillMutationError("input", "O caminho não pode conter escapes.")
    fd = open_fd(stack, path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    chain = []
    for part in path.parts[1:]:
        child = open_fd(stack, part, os.O_RDONLY | os.O_DIRECTORY, parent=fd)
        chain.append((fd, part, child))
        fd = child
    check_chain(chain)
    return fd, chain


def check_chain(chain: list[tuple[int, str, int]]) -> None:
    for parent, name, fd in chain:
        same_entry(parent, name, fd)


def read_regular(parent: int, name: str, limit: int) -> tuple[bytes, os.stat_result]:
    with ExitStack() as stack:
        fd = open_fd(stack, name, os.O_RDONLY | os.O_NONBLOCK, parent=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise SkillMutationError("input", "Use arquivo regular sem links e dentro do limite.")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if (
            len(data) > limit
            or len(data) != before.st_size
            or after.st_nlink != 1
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise SkillMutationError("conflict", "Arquivo alterado durante a leitura.")
        same_entry(parent, name, fd)
        return bytes(data), after


def read_skill_creation(source: Path):
    source = Path(source)
    if str(source) == "-" or "://" in str(source):
        raise SkillMutationError("input", "Informe um arquivo local em --file.")
    with io_errors(kind="input"), ExitStack() as stack:
        parent, chain = open_directory(stack, source.parent)
        data, _ = read_regular(parent, source.name, 65536)
        check_chain(chain)
        try:
            return validate_skill_creation(data.decode("utf-8"))
        except UnicodeError:
            raise SkillMutationError("input", "SKILL.md deve ser UTF-8 válido.") from None


def rename_no_replace(old_parent: int, old_name: str, new_parent: int, new_name: str) -> None:
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError):
        raise SkillMutationError(
            "unavailable", "Publicação segura indisponível nesta plataforma."
        ) from None
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    if function(old_parent, os.fsencode(old_name), new_parent, os.fsencode(new_name), 1) != 0:
        number = ctypes.get_errno()
        if number in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EXDEV):
            raise SkillMutationError(
                "unavailable", "Filesystem não oferece movimento seguro sem substituição."
            )
        if number in (errno.EEXIST, errno.ENOTEMPTY):
            raise SkillMutationError(
                "conflict", "O nome está ocupado; ambas as versões foram preservadas."
            )
        raise SkillMutationError("io", "Não foi possível mover a skill com segurança.")
    os.fsync(old_parent)
    os.fsync(new_parent)


class SkillMutationFiles:
    """Recursos de uma mutação; chamador detém o lock de instalação."""

    def __init__(self, home: Path):
        self.home = home
        self.stack = ExitStack()
        self.chain: list[tuple[int, str, int]] = []

    def _directory(self, parent: int, name: str) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        fd = open_fd(self.stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
        self.chain.append((parent, name, fd))
        return fd

    def __enter__(self):
        try:
            with io_errors():
                self.root, self.chain = open_directory(self.stack, self.home)
                self.installed = self._directory(self.root, "skills")
                private = self._directory(self.root, ".skill-mutations")
                self.staging = self._directory(private, "staging")
                self.retired = self._directory(private, "retired")
                if (
                    len(
                        {
                            os.fstat(fd).st_dev
                            for fd in (self.installed, private, self.staging, self.retired)
                        }
                    )
                    != 1
                ):
                    raise SkillMutationError(
                        "unavailable", "Instalação e área privada devem estar no mesmo filesystem."
                    )
                self.check()
        except (SkillMutationError, OSError):
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def check(self) -> None:
        check_chain(self.chain)

    def _inspect(self, parent: int, name: str) -> SkillFilesystemEntry | None:
        with io_errors(), ExitStack() as stack:
            self.check()
            try:
                fd = open_fd(stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
            except FileNotFoundError:
                return None
            before = os.fstat(fd)
            if set(os.listdir(fd)) != {"SKILL.md"}:
                raise SkillMutationError(
                    "conflict", "A skill possui alterações ou anexos; arquivos preservados."
                )
            data, skill = read_regular(fd, "SKILL.md", 65536)
            try:
                creation = validate_skill_creation(data.decode("utf-8"))
            except (SkillMutationError, UnicodeError):
                raise SkillMutationError(
                    "conflict", "Conteúdo atual não corresponde a uma criação válida; preservado."
                ) from None
            after = os.fstat(fd)
            if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                raise SkillMutationError(
                    "conflict", "Diretório alterado durante leitura; preservado."
                )
            same_entry(parent, name, fd)
            self.check()
            identity = SkillDirectoryIdentity(
                after.st_dev, after.st_ino, skill.st_dev, skill.st_ino
            )
            return SkillFilesystemEntry(identity, creation)

    def inspect_installed(self, name: str) -> SkillFilesystemEntry | None:
        validate_name(name)
        result = self._inspect(self.installed, name)
        if result is not None and result.creation.name != name:
            raise SkillMutationError(
                "conflict", "Nome instalado diverge do conteúdo; arquivos preservados."
            )
        return result

    def inspect_private(
        self, operation_id: str, *, retired: bool = False
    ) -> SkillFilesystemEntry | None:
        validate_id(operation_id)
        return self._inspect(self.retired if retired else self.staging, operation_id)

    def stage(self, operation_id: str, creation) -> SkillFilesystemEntry:
        validate_id(operation_id)
        with io_errors(), ExitStack() as stack:
            self.check()
            try:
                os.mkdir(operation_id, 0o700, dir_fd=self.staging)
            except FileExistsError:
                raise SkillMutationError(
                    "conflict", "ID privado já existe; conteúdo preservado."
                ) from None
            directory = open_fd(
                stack, operation_id, os.O_RDONLY | os.O_DIRECTORY, parent=self.staging
            )
            fd = open_fd(stack, "SKILL.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=directory)
            data = memoryview(creation.text.encode("utf-8"))
            while data:
                written = os.write(fd, data)
                if not written:
                    raise SkillMutationError("io", "Gravação incompleta da skill.")
                data = data[written:]
            os.fsync(fd)
            os.fsync(directory)
            os.fsync(self.staging)
            result = self.inspect_private(operation_id)
            if result is None or result.creation != creation:
                raise SkillMutationError(
                    "conflict", "Preparação da skill divergiu; conteúdo preservado."
                )
            return result

    def publish(self, operation_id: str, name: str) -> None:
        validate_id(operation_id)
        validate_name(name)
        with io_errors():
            self.check()
            rename_no_replace(self.staging, operation_id, self.installed, name)
            self.check()

    def retire(self, name: str, operation_id: str) -> None:
        validate_name(name)
        validate_id(operation_id)
        with io_errors():
            self.check()
            rename_no_replace(self.installed, name, self.retired, operation_id)
            self.check()

    def restore(self, operation_id: str, name: str) -> None:
        validate_id(operation_id)
        validate_name(name)
        with io_errors():
            self.check()
            rename_no_replace(self.retired, operation_id, self.installed, name)
            self.check()

    def discard_staging(self, operation_id: str, expected: SkillFilesystemEntry) -> None:
        validate_id(operation_id)
        with io_errors(), ExitStack() as stack:
            if self.inspect_private(operation_id) != expected:
                raise SkillMutationError("conflict", "Staging divergente; conteúdo preservado.")
            directory = open_fd(
                stack, operation_id, os.O_RDONLY | os.O_DIRECTORY, parent=self.staging
            )
            self.check()
            same_entry(self.staging, operation_id, directory)
            os.unlink("SKILL.md", dir_fd=directory)
            os.fsync(directory)
            os.rmdir(operation_id, dir_fd=self.staging)
            os.fsync(self.staging)

    def assert_name_available(self, name: str) -> None:
        validate_name(name)
        with io_errors(kind="conflict"):
            self.check()
            try:
                os.stat(name, dir_fd=self.installed, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SkillMutationError("conflict", "Nome já instalado; selecione outro nome.")
            try:
                raw, _ = read_regular(self.installed, ".bundled_manifest", 1024 * 1024)
            except FileNotFoundError:
                return
            try:
                lines = raw.decode("utf-8").splitlines()
            except UnicodeError:
                raise SkillMutationError(
                    "conflict", "Manifesto bundled inválido; criação recusada."
                ) from None
            names = set()
            for line in lines:
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                entry, sep, digest = value.partition(":")
                try:
                    validate_name(entry.strip())
                except SkillMutationError:
                    raise SkillMutationError(
                        "conflict", "Manifesto bundled inválido; criação recusada."
                    ) from None
                if (
                    sep and digest.strip() and re.fullmatch(r"[0-9a-f]{32}", digest.strip()) is None
                ) or entry.strip() in names:
                    raise SkillMutationError(
                        "conflict", "Manifesto bundled inválido; criação recusada."
                    )
                names.add(entry.strip())
            if name in names:
                raise SkillMutationError(
                    "conflict", "Nome reservado pelo catálogo bundled, inclusive após exclusão."
                )
            self.check()

    def sync_parents(self) -> None:
        with io_errors():
            self.check()
            for fd in (self.installed, self.staging, self.retired):
                os.fsync(fd)
