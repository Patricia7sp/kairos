"""Mede leitores A2 em fixtures SQLite e filesystem reais, fora de produção."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import platform
import pstats
import statistics
import tempfile
import time
import uuid
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import (
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationState,
    validate_skill_creation,
)
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository

TARGET = "benchmark-target"
COUNTED = {"validate_skill_timeline", "validate_adoption_schema", "_get_validated", "_reader_copy"}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def seed_prefix(home, count):
    """Cria prefixo pela API v7 e o importa pelas migrações reais v8/v9."""
    connection = connect(home / "state.db")
    try:
        migrate(connection, target=7)
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        for index in range(count):
            text = (
                f"---\nname: prefix-{index}\ndescription: Fixture local.\n"
                "version: '0.1.0'\n---\nProcedimento fictício.\n"
            )
            creation = validate_skill_creation(text)
            draft = SkillMutationDraft(
                uuid.uuid4().hex,
                repository.home_id,
                SkillMutationAction.CREATE,
                creation.name,
                Actor.USER_FOREGROUND,
                time.time(),
                SkillDirectoryIdentity(1, index + 1, 1, 1),
                creation.sha256,
                creation.size_bytes,
            )
            repository.prepare(draft, text)
            repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        migrate(connection, target=8)
        migrate(connection)
    finally:
        connection.close()


def seed_adoptions(home, cycles):
    target = home / "skills" / TARGET
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"Legacy fixture without semver.\n")
    connection = connect(home / "state.db")
    try:
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        service = SkillAdoptionService(home, repository)
        for _ in range(cycles):
            result = service.adopt(TARGET, actor=Actor.USER_FOREGROUND, confirmed=True)
            if result.record is None:
                raise RuntimeError("Fixture não criou adoção.")
            service.unadopt(
                TARGET,
                actor=Actor.USER_FOREGROUND,
                confirmed=True,
                expect_adoption=result.record.operation_id,
            )
        adopted = service.adopt(TARGET, actor=Actor.USER_FOREGROUND, confirmed=True)
        if adopted.record is None:
            raise RuntimeError("Fixture não criou adoção final.")
        return adopted.record.operation_id
    finally:
        connection.close()


def measure(operation, samples):
    operation()  # aquecimento fora das amostras
    durations = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        operation()
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    profiler = cProfile.Profile()
    profiler.runcall(operation)  # contagem separada, fora dos tempos
    counts = dict.fromkeys(sorted(COUNTED), 0)
    for (_, _, name), (_, calls, _, _, _) in pstats.Stats(profiler).stats.items():
        if name in counts:
            counts[name] += calls
    return {
        "samples_ms": durations,
        "median_ms": statistics.median(durations),
        "min_ms": min(durations),
        "max_ms": max(durations),
        "calls_one_profiled_operation": counts,
    }


def scenario(prefix, cycles, samples):
    with tempfile.TemporaryDirectory(prefix="kairos-ledger-benchmark-") as temporary:
        home = Path(temporary)
        seed_started = time.perf_counter()
        seed_prefix(home, prefix)
        adoption_id = seed_adoptions(home, cycles)
        seed_seconds = time.perf_counter() - seed_started
        before = digest(home / "state.db")
        root_before = (home / "skills" / TARGET).stat()
        connection = connect(home / "state.db")
        try:
            repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
            service = SkillAdoptionService(home, repository)
            metadata = {
                "prefix_operations": prefix,
                "revoked_adoption_cycles": cycles,
                "seed_seconds": seed_seconds,
                "database_bytes": (home / "state.db").stat().st_size,
                "timeline_events": connection.execute(
                    "SELECT COUNT(*) FROM skill_authority_timeline"
                ).fetchone()[0],
            }

            def evidence():
                value = repository.installation_evidence(TARGET)
                if value.adoption is None or not value.adoption.active:
                    raise RuntimeError("Evidência vigente ausente.")

            def history():
                value = repository.history(name=TARGET, limit=20)
                if not value or value[0].operation_id != adoption_id:
                    raise RuntimeError("Histórico não preservou adoção final.")

            def identify():
                value = identify_skill(home, TARGET)
                if (
                    value.adoption_active is not True
                    or value.authorization_basis != "user_adoption"
                ):
                    raise RuntimeError("Identify não preservou autoridade vigente.")

            def idempotent_adopt():
                value = service.adopt(TARGET, actor=Actor.USER_FOREGROUND, confirmed=True)
                if value.record is None or value.record.operation_id != adoption_id:
                    raise RuntimeError("Adopt não foi idempotente.")

            operations = {
                "installation_evidence": evidence,
                "history_limit_20": history,
                "identify_skill": identify,
                "idempotent_adopt": idempotent_adopt,
            }
            metadata["measurements"] = {
                name: measure(operation, samples) for name, operation in operations.items()
            }
        finally:
            connection.close()
        root_after = (home / "skills" / TARGET).stat()
        if digest(home / "state.db") != before or (
            root_before.st_dev,
            root_before.st_ino,
        ) != (root_after.st_dev, root_after.st_ino):
            raise RuntimeError("Operação de leitura/idempotência alterou a fixture.")
        metadata["database_and_root_preserved"] = True
        return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=7)
    arguments = parser.parse_args()
    if arguments.samples < 3:
        parser.error("--samples deve ser pelo menos 3")
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "script_sha256": digest(Path(__file__)),
        "method": "7 amostras por padrão; cache aquecido; perfil separado; fixtures locais reais",
        "scenarios": [],
    }
    for prefix, cycles in ((0, 0), (100, 0), (1000, 0), (0, 20), (0, 100)):
        value = scenario(prefix, cycles, arguments.samples)
        result["scenarios"].append(value)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(value), flush=True)


if __name__ == "__main__":
    main()
