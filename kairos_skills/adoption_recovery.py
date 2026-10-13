"""Reconciliação de adoções preparadas sem reconstruir instalações."""

from kairos_domain.ownership import Provenance
from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionRecord
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.removal_origin import resolve_removal_origin


def _conflict(repository, record: SkillAdoptionRecord) -> None:
    repository.finish(record.operation_id, SkillMutationState.CONFLICT)
    raise SkillMutationError(
        "conflict",
        "Instalação diverge da adoção preparada; provas preservadas.",
        operation_id=record.operation_id,
    )


def _new_veto(evidence, origin) -> bool:
    return (
        origin.identity_valid is True
        and origin.provenance in (Provenance.BUNDLED, Provenance.SEDIMENT)
    ) or (evidence.current is None and origin.provenance is Provenance.BUNDLED)


def _attest_prepared(repository, files, record: SkillAdoptionRecord) -> str:
    operation_id = record.operation_id
    if repository.get(operation_id) != record or repository.pending(record.name) != (record,):
        _conflict(repository, record)
    snapshot = repository.snapshot(operation_id)
    installed = files.capture_installed(record.name)
    if installed is None:
        return "absent"
    if (
        snapshot.contents is None
        or installed != snapshot
        or (installed.entries[0].device, installed.entries[0].inode)
        != (record.draft.device, record.draft.inode)
    ):
        _conflict(repository, record)
    evidence = repository.installation_evidence(record.name)
    if evidence.pending_operation_ids != (operation_id,):
        _conflict(repository, record)
    origin = resolve_removal_origin(files.home, record.name, evidence.current, installed)
    if _new_veto(evidence, origin):
        return "veto"

    # Include the origin check in the last stable observation before the
    # journal transition. A newly matching bundle vetoes this prepared A.
    recaptured = files.capture_installed(record.name)
    latest = repository.installation_evidence(record.name)
    if recaptured != snapshot or latest != evidence:
        _conflict(repository, record)
    latest_origin = resolve_removal_origin(files.home, record.name, latest.current, recaptured)
    return "veto" if _new_veto(latest, latest_origin) else "ready"


def reconcile_adoption_operation(repository, files, record: SkillAdoptionRecord):
    """Conclui A só quando a mesma raiz e inventário continuam autorizados.

    O chamador deve manter o lock comum de mutações durante a reconciliação.
    A operação nunca cria, restaura ou altera arquivos da skill.
    """
    if (
        type(record) is not SkillAdoptionRecord
        or record.action is not SkillAdoptionAction.ADOPT
        or record.state is not SkillMutationState.PREPARED
        or record.draft.home_id != repository.home_id
    ):
        raise SkillMutationError("input", "Informe uma adoção preparada deste ledger.")
    operation_id = record.operation_id
    try:
        outcome = _attest_prepared(repository, files, record)
        state = SkillMutationState.COMMITTED if outcome == "ready" else SkillMutationState.ABORTED
        return repository.finish(operation_id, state)
    except SkillMutationError as error:
        if error.kind in ("unavailable", "io"):
            raise SkillMutationError(
                error.kind,
                str(error),
                operation_id=operation_id,
            ) from None
        if error.kind in ("input", "conflict"):
            _conflict(repository, record)
        raise SkillMutationError(error.kind, str(error), operation_id=operation_id) from None
    except OSError:
        raise SkillMutationError(
            "io",
            "Não foi possível atestar a adoção; operação permanece preparada.",
            operation_id=operation_id,
        ) from None
