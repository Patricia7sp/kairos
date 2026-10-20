"""Provas de árvores e movimentos para escritores de publicação participantes."""

import logging
from contextlib import ExitStack, contextmanager

from kairos_filesystem.contract import FilesystemError
from kairos_filesystem.descriptors import check_chain, open_directory, rename_no_replace
from kairos_filesystem.tree import MAX_LIMITS, capture_tree, delete_verified_tree
from kairos_skills.mutation_contract import SkillMutationError

_LOGGER = logging.getLogger(__name__)


@contextmanager
def publication_errors():
    try:
        yield
    except FilesystemError as error:
        _LOGGER.warning(
            "skill_publication_refused",
            extra={
                "action": "publish",
                "kind": error.kind,
                "reason": "filesystem_attestation_failed",
            },
        )
        raise SkillMutationError(error.kind, str(error)) from None
    except OSError:
        _LOGGER.warning(
            "skill_publication_refused",
            extra={"action": "publish", "kind": "io", "reason": "filesystem_unavailable"},
        )
        raise SkillMutationError(
            "io", "Publicação não pôde ser concluída; versões preservadas."
        ) from None


def capture_publication(path):
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, path.parent)
        result = capture_tree(parent, path.name, limits=MAX_LIMITS, include_contents=False)
        check_chain(chain)
        return result


def move_publication(source, destination, expected, *, guard):
    with publication_errors(), ExitStack() as stack:
        source_parent, source_chain = open_directory(stack, source.parent)
        destination_parent, destination_chain = open_directory(stack, destination.parent)
        actual = capture_tree(source_parent, source.name, limits=MAX_LIMITS, include_contents=False)
        if actual != expected:
            raise SkillMutationError("conflict", "Árvore alterada; versões preservadas.")
        check_chain(source_chain)
        check_chain(destination_chain)
        guard()
        rename_no_replace(source_parent, source.name, destination_parent, destination.name)
        guard()


def delete_publication(path, expected, *, guard):
    with publication_errors(), ExitStack() as stack:
        parent, chain = open_directory(stack, path.parent)

        def check():
            check_chain(chain)
            guard()

        delete_verified_tree(parent, path.name, expected, guard=check)
