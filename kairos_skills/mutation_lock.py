"""Adaptador do lock compartilhado para mutações de skills."""

from contextlib import contextmanager
from pathlib import Path

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.lock import filesystem_lock
from kairos_skills.mutation_contract import SkillMutationError


@contextmanager
def skill_mutation_lock(home: Path, *, timeout: float = 5.0):
    try:
        with filesystem_lock(home, ".skills-write.lock", timeout=timeout):
            yield
    except FilesystemError as error:
        raise SkillMutationError(error.kind, str(error)) from None
