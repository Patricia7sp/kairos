"""Uma posse do lock, banco atestado e lifetime comum aos efeitos da publicação."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_filesystem.descriptors import check_chain, open_directory, open_fd, same_entry
from kairos_filesystem.tree import observed_mount_id
from kairos_skills.mutation_contract import SkillMutationError, require
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.org_policy_guard import attest_policy_home
from kairos_skills.publication_files import PublicationFiles
from kairos_skills.publication_io import publication_errors
from kairos_skills.publication_sources import PublicationContext
from kairos_skills.removal_state import _database_entry, database_connection
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_publications import SkillPublicationRepository

_SESSION_KEY = object()


class PublicationSession:
    def __init__(self, key, *, home, policy, context, connection, files, directory, chain, lock):
        require(key is _SESSION_KEY, "Sessão exige posse do lock pela composição.")
        self.home = home
        self.policy = policy
        self.context = context
        self.connection = connection
        self.files = files
        self.publications = SkillPublicationRepository(connection, home_id=context.home_id)
        self._directory = directory
        self._chain = chain
        self._lock = lock
        self._lock_mount = observed_mount_id(lock)
        self._database = os.stat("state.db", dir_fd=directory, follow_symlinks=False)
        self._closed = False

    def revalidate(self):
        if self._closed:
            raise SkillMutationError("conflict", "Sessão de publicação encerrada.")
        self.policy.revalidate()
        self.context.revalidate()
        self.files.check()
        with publication_errors():
            check_chain(self._chain)
            same_entry(self._directory, ".skills-write.lock", self._lock)
            lock = os.fstat(self._lock)
            require(
                stat.S_ISREG(lock.st_mode) and lock.st_nlink == 1, "Lock de publicação inseguro."
            )
            with ExitStack() as stack:
                current_lock = open_fd(
                    stack, ".skills-write.lock", os.O_PATH, parent=self._directory
                )
                if observed_mount_id(current_lock) != self._lock_mount:
                    raise SkillMutationError("conflict", "Montagem do lock alterada.")
            _database_entry(self._directory, self._chain)
            current = os.stat("state.db", dir_fd=self._directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (self._database.st_dev, self._database.st_ino):
                raise SkillMutationError("conflict", "Banco de publicação substituído.")
            path = self.connection.execute("PRAGMA database_list").fetchone()[2]
            opened = os.stat(path, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise SkillMutationError("conflict", "Conexão de publicação divergente.")


@contextmanager
def publication_session(home, *, policy, context):
    home = Path(home).absolute()
    attest_policy_home(policy, home)
    require(type(context) is PublicationContext and context.home_id == policy.home_id)
    context.revalidate()
    with publication_errors(), ExitStack() as stack:
        stack.enter_context(skill_mutation_lock(home))
        directory, chain = open_directory(stack, home)
        lock = open_fd(stack, ".skills-write.lock", os.O_RDONLY | os.O_NONBLOCK, parent=directory)
        same_entry(directory, ".skills-write.lock", lock)
        policy.revalidate()
        context.revalidate()
        connection = stack.enter_context(database_connection(home, write=True))
        migrate(connection, target=11)
        files = stack.enter_context(PublicationFiles(home))
        session = PublicationSession(
            _SESSION_KEY,
            home=home,
            policy=policy,
            context=context,
            connection=connection,
            files=files,
            directory=directory,
            chain=chain,
            lock=lock,
        )
        try:
            session.revalidate()
            yield session
        finally:
            session._closed = True
