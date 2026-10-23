"""Sincronização de skills bundled — manifesto v2.

RF-08 (parcial). `_reversa_sdd/skills/` (Tarefa 09).

**Esta política não é decisão nova do Kairos — é herança.** A pergunta 8
registrou "a edição local vence e a sincronização apenas avisa" como decisão
de produto; ao implementar, verificou-se que `tools/skills_sync.py` do legado
já faz exatamente isso. A spec foi corrigida.

O manifesto guarda, por skill, o **hash de origem no momento do último sync**.
Esse detalhe é o que faz o mecanismo funcionar.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from kairos_domain.ownership import Provenance
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState, require
from kairos_skills.org_policy_contract import OrgPolicyAction
from kairos_skills.org_policy_guard import repository_guard
from kairos_skills.publication_contract import ManifestDelta, PublicationResult, PublicationWriter
from kairos_skills.publication_files import capture_source
from kairos_skills.publication_manifest import (
    apply_manifest_delta,
    observe_manifest,
    reconcile_manifest,
)
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import PublicationContext
from kairos_skills.publications import SkillPublicationService
from kairos_state.repositories.skill_mutations import SkillMutationRepository

__all__ = [
    "MANIFEST_NAME",
    "NO_BUNDLED_SKILLS_MARKER",
    "Outcome",
    "SyncResult",
    "origin_hash",
    "read_manifest",
    "sync_bundled_skills",
    "write_manifest",
]

MANIFEST_NAME = ".bundled_manifest"
_LOGGER = logging.getLogger(__name__)

#: Marcador de opt-out por perfil. Presente ⇒ zero seeding.
NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"


class Outcome(StrEnum):
    COPIED = "copied"  # nova: copiada, hash registrado
    UPDATED = "updated"  # bundled mudou e o usuário nunca tocou
    SKIPPED = "skipped"  # bundled inalterado
    USER_MODIFIED = "user_modified"  # o usuário editou → preservado
    USER_DELETED = "user_deleted"  # o usuário apagou → respeitado
    CLEANED = "cleaned"  # sumiu do bundled → limpa do manifesto


@dataclass
class SyncResult:
    copied: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    user_modified: list[str] = field(default_factory=list)
    user_deleted: list[str] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    total_bundled: int = 0
    #: Distingue "optou por sair" de "sincronizou 0 / falhou".
    skipped_opt_out: bool = False
    blocked: list[str] = field(default_factory=list)
    failed: bool = False
    publications: list[PublicationResult] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    error_kinds: list[str] = field(default_factory=list)


def origin_hash(skill_dir: Path) -> str:
    """Hash do conteúdo de uma skill.

    Sobre **todos** os arquivos, em ordem estável — uma skill é um diretório
    (`SKILL.md` mais `references/`, `scripts/`, `templates/`), e hashear só o
    `SKILL.md` deixaria a edição de um script passar por "inalterada".
    """
    h = hashlib.md5(usedforsecurity=False)
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(skill_dir)).encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _attested_origin_hash(path, *, expected=None):
    snapshot = capture_source(path)
    if expected is not None:
        require(snapshot.entries == expected.entries, "Árvore alterada durante cálculo do hash.")
    digest = hashlib.md5(usedforsecurity=False)
    for relative in sorted(snapshot.contents, key=Path):
        digest.update(relative.encode("utf-8"))
        digest.update(snapshot.contents[relative])
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    """Lê o manifesto. **Migra v1 automaticamente.**

    v1 era só o nome por linha; v2 é `nome:hash`. Uma entrada v1 vira hash
    vazio, e o hash vazio é o que dispara a migração no sync seguinte —
    trata-se a skill como "origem desconhecida", que é conservador: sem saber
    o hash de origem, não dá para afirmar que o usuário não a editou.
    """
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, digest = entry.partition(":")
        out[name.strip()] = digest.strip() if sep else ""
    return out


def write_manifest(path: Path, manifest: dict[str, str]) -> None:
    """Escreve **atomicamente**, em v2.

    Um manifesto meio escrito é pior que nenhum: as skills sem entrada seriam
    tratadas como novas e re-copiadas por cima da edição do usuário.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".bundled-manifest-", delete=False
    ) as output:
        tmp = Path(output.name)
        try:
            output.write("".join(f"{name}:{digest}\n" for name, digest in sorted(manifest.items())))
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            tmp.unlink()
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def check_sync_policy(session, draft):
    if draft.writer is not PublicationWriter.BUNDLED_SYNC:
        return
    if os.path.lexists(session.home / "skills" / NO_BUNDLED_SKILLS_MARKER):
        raise SkillMutationError("conflict", "Opt-out observado; publicação pendente preservada.")
    common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
    if draft.name in common.bundled_tombstones():
        raise SkillMutationError("conflict", "Tombstone observado; publicação pendente preservada.")


def check_sync_intent(session, draft):
    observation = getattr(session, "_manifest_observation", None)
    if observation is not None:
        observation.check()


def prepare_sync_publication(session, name, *, previous, current, delta, source_proof):
    require(delta is not None and delta.name == name, "Sync exige delta canônico de manifesto.")
    manifest, observation = observe_manifest(session.home)
    require(manifest.get(name) == delta.before, "Manifesto não corresponde à preparação.")
    session._manifest_observation = observation
    require(
        delta.after == _attested_origin_hash(session.context._bundled[name], expected=source_proof),
        "Delta não corresponde à fonte bundled.",
    )
    if os.path.lexists(session.home / "skills" / NO_BUNDLED_SKILLS_MARKER):
        raise SkillMutationError("conflict", "Opt-out observado antes da preparação.")
    common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
    require(name not in common.bundled_tombstones(), "Tombstone impede publicação.")
    if previous is None:
        require(name not in manifest, "Exclusão pelo usuário deve ser preservada.")
        return
    require(
        bool(delta.before)
        and _attested_origin_hash(session.home / "skills" / name, expected=previous)
        == delta.before,
        "Edição local impede atualização.",
    )
    if current is not None:
        published = session.publications.get(current.operation_id)
        if published is not None:
            require(published.draft.staged_tree == previous, "Instalação canônica alterada.")
        else:
            require(
                current.provenance is Provenance.BUNDLED
                or current.installation_source == "package_bundle",
                "Instalação não é bundled elegível.",
            )


def _accumulate(result, publication):
    result.publications.append(publication)
    if publication.error_kind is not None and publication.error_kind not in result.error_kinds:
        result.error_kinds.append(publication.error_kind)
    if not publication.complete:
        result.failed = True
        if (
            publication.state
            in (
                SkillMutationState.PREPARED,
                SkillMutationState.CONFLICT,
                SkillMutationState.COMMITTED,
            )
            and publication.operation_id not in result.pending
        ):
            result.pending.append(publication.operation_id)


def _recover_pending(session, result):
    common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
    publication_ids = {p.operation_id for p in session.publications.history()}
    for operation_id in dict.fromkeys(row[3] for row in common._timeline()):
        record = common.get(operation_id)
        if not getattr(
            record,
            "pending",
            record.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT),
        ):
            continue
        if record.operation_id not in publication_ids:
            raise SkillMutationError(
                "conflict",
                "Operação de outra família bloqueia o sync.",
                operation_id=record.operation_id,
            )
        if record.draft.writer is not PublicationWriter.BUNDLED_SYNC:
            result.pending.append(record.operation_id)
            result.failed = True
            continue
        _manifest, session._manifest_observation = observe_manifest(session.home)
        publication = SkillPublicationService(session).recover(record.operation_id)
        _accumulate(result, publication)
    return not result.failed


def retire_manifest_entry(name, *, session):
    require(
        type(session.context) is PublicationContext
        and session.context.writer is PublicationWriter.BUNDLED_SYNC
    )
    session.revalidate()
    require(not session.context.contains(name), "Skill ainda pertence ao pacote.")
    common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
    guard = repository_guard(session.policy, common)
    guard.check(name, OrgPolicyAction.SYNC)
    manifest, session._manifest_observation = observe_manifest(session.home)
    if name not in manifest:
        return None
    delta = ManifestDelta(name, manifest[name], None)
    records = session.publications.history(name=name)
    record = records[-1] if records else None
    if record is None:

        def legacy_guard():
            session.revalidate()
            guard.check(name, OrgPolicyAction.SYNC)
            require(not session.context.contains(name), "Skill voltou ao pacote.")

        apply_manifest_delta(session.home, delta, guard=legacy_guard)
        return None
    require(
        record.state is SkillMutationState.COMMITTED and not record.pending,
        "Publicação pendente impede manutenção.",
    )

    def check():
        session.revalidate()
        guard.check_publication(record.draft)
        require(not session.context.contains(name), "Skill voltou ao pacote.")

    record = session.publications.prepare_manifest(record.operation_id, delta, guard=check)
    reconcile_manifest(session.home, record, guard=check)
    _manifest, session._manifest_observation = observe_manifest(session.home)

    def confirmation_guard():
        check()
        session._manifest_observation.check()

    return session.publications.confirm_manifest(record.operation_id, guard=confirmation_guard)


def _sync_item(session, name, result):
    common = SkillMutationRepository(session.connection, home_id=session.context.home_id)
    repository_guard(session.policy, common).check(name, OrgPolicyAction.SYNC)
    if name in common.bundled_tombstones():
        result.user_deleted.append(name)
        return
    manifest, session._manifest_observation = observe_manifest(session.home)
    source, proof = session.context.resolve(name, repository=session.publications)
    require(proof == session._bundled_preflight_proofs[name], "Fonte alterada após preflight.")
    digest = _attested_origin_hash(source, expected=proof)
    dest = session.home / "skills" / name
    before = manifest.get(name)
    exists = dest.exists() or dest.is_symlink()
    publication = None
    if name not in manifest and exists:
        result.user_modified.append(name)
    elif name in manifest and not exists:
        result.user_deleted.append(name)
    elif before and before == digest:
        result.skipped.append(name)
    elif exists and (not before or _attested_origin_hash(dest) != before):
        result.user_modified.append(name)
    else:
        publication = SkillPublicationService(session).publish(
            name, manifest_delta=ManifestDelta(name, before, digest)
        )
        _accumulate(result, publication)
        if publication.complete:
            (result.updated if exists else result.copied).append(name)
    if publication is None:
        session.context._release_completed_bundle(name)


def _sync_failure(session, name, result, error, *, phase):
    kind = getattr(error, "kind", "io")
    if kind not in result.error_kinds:
        result.error_kinds.append(kind)
    result.failed = True
    if kind == "denied":
        result.blocked.append(name)
    for record in session.publications.pending(name):
        if record.operation_id not in result.pending:
            result.pending.append(record.operation_id)
    _LOGGER.warning(
        "skill_sync_incomplete",
        extra={
            "action": "sync",
            "writer": session.context.writer.value,
            "phase": phase,
            "kind": kind,
            "operation_id": getattr(error, "operation_id", None),
            "reason": "sync_item_refused",
        },
    )


def _sync_discovered(session, result):
    for name in sorted(session.context._bundled):
        try:
            _sync_item(session, name, result)
        except (SkillMutationError, OSError) as error:
            _sync_failure(session, name, result, error, phase="item")
    manifest, session._manifest_observation = observe_manifest(session.home)
    for name in sorted(
        (set(manifest) & session._manifest_cleanup_candidates) - set(session.context._bundled)
    ):
        try:
            record = retire_manifest_entry(name, session=session)
            if record is not None:
                _accumulate(
                    result,
                    PublicationResult(
                        record.operation_id,
                        name,
                        record.state,
                        True,
                        None,
                        record.manifest_complete,
                        record.cleanup_complete,
                    ),
                )
            result.cleaned.append(name)
        except (SkillMutationError, OSError) as error:
            _sync_failure(session, name, result, error, phase="catalog_cleanup")
    return result


def sync_bundled_skills(bundled_dir: Path, user_dir: Path, *, policy, context=None) -> SyncResult:
    require(type(context) is PublicationContext, "Contexto de publicação obrigatório.")
    require(Path(user_dir).name == "skills", "Sync exige o diretório skills do home.")
    require(
        context.writer is PublicationWriter.BUNDLED_SYNC
        and Path(bundled_dir).absolute() == context.root,
        "Raiz bundled não corresponde à composição.",
    )
    require(context.home_id == policy.home_id, "Contexto pertence a outro home.")
    context.revalidate()
    result = SyncResult()
    if os.path.lexists(user_dir / NO_BUNDLED_SKILLS_MARKER):
        result.skipped_opt_out = True
        return result
    with publication_session(user_dir.parent, policy=policy, context=context) as session:
        manifest, session._manifest_observation = observe_manifest(session.home)
        session._manifest_cleanup_candidates = set(manifest)
        result.total_bundled = len(context._bundled)
        if not _recover_pending(session, result):
            return result
        session._bundled_preflight_proofs = {
            name: replace(capture_source(path), contents=None)
            for name, path in sorted(context._bundled.items())
        }
        context.revalidate()
        return _sync_discovered(session, result)
