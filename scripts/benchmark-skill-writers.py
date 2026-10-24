"""Compara add real em revisões históricas, somente com homes temporários."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import inspect
import io
import json
import os
import platform
import pstats
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

COUNTED = {
    "validate_skill_timeline",
    "validate_org_schema",
    "validate_publication_schema",
    "validate_adoption_schema",
    "_get_validated",
    "repository_guard",
    "protected_organizations",
    "receipt",
    "revalidate",
    "_check",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile_calls(profiler, root):
    calls = {}
    for (filename, _line, function), (_, count, own, cumulative, _) in pstats.Stats(
        profiler
    ).stats.items():
        path = Path(filename)
        if function in COUNTED and path.is_relative_to(root):
            key = str(path.relative_to(root)) + ":" + function
            entry = calls.setdefault(key, {"calls": 0, "own_s": 0, "cumulative_s": 0})
            entry["calls"] += count
            entry["own_s"] += own
            entry["cumulative_s"] += cumulative
    return calls


def one_add(service, source, home, created, root, *, profiled=False):
    from kairos_domain.ownership import Actor
    from kairos_skills.mutation_contract import SkillMutationState

    count_before = len(created)
    name = f"benchmark-{count_before:06d}"
    text = (
        f"---\nname: {name}\ndescription: Fixture temporária.\n"
        "version: '0.1.0'\n---\nProcedimento de benchmark.\n"
    )
    source.write_text(text)
    profiler = cProfile.Profile() if profiled else None
    started = time.perf_counter_ns()
    record = (
        profiler.runcall(service.add, source, actor=Actor.USER_FOREGROUND)
        if profiler
        else service.add(source, actor=Actor.USER_FOREGROUND)
    )
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    installed = home / "skills" / name / "SKILL.md"
    if record.state is not SkillMutationState.COMMITTED or installed.read_text() != text:
        raise RuntimeError("benchmark_add_did_not_publish")
    if service.repository.current_installation(name).operation_id != record.operation_id:
        raise RuntimeError("benchmark_installation_differs")
    status = installed.parent.stat()
    created.append((name, text, status.st_dev, status.st_ino))
    measurement = {"prior_creations": count_before, "elapsed_ms": elapsed}
    if profiler:
        measurement["profile"] = profile_calls(profiler, root)
    return measurement


def verify_fixture(connection, home, created, version):
    timeline = "skill_publication_timeline" if version == 11 else "skill_authority_timeline"
    query = (
        "SELECT COUNT(*) FROM skill_publication_timeline"
        if version == 11
        else "SELECT COUNT(*) FROM skill_authority_timeline"
    )
    events = connection.execute(query).fetchone()[0]
    if events != len(created) * 2:
        raise RuntimeError("benchmark_timeline_incomplete")
    states = dict(
        connection.execute("SELECT state,COUNT(*) FROM skill_mutation_events GROUP BY state")
    )
    if states != {"prepared": len(created), "committed": len(created)}:
        raise RuntimeError("benchmark_left_pending")
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise RuntimeError("benchmark_database_corrupted")
    for name, text, device, inode in created:
        directory = home / "skills" / name
        status = directory.stat()
        if (status.st_dev, status.st_ino) != (device, inode):
            raise RuntimeError("benchmark_replaced_prior_tree")
        if (directory / "SKILL.md").read_text() != text:
            raise RuntimeError("benchmark_changed_prior_content")
    return {
        "active_timeline": timeline,
        "timeline_events": events,
        "real_installed_trees_verified": len(created),
        "integrity": "ok",
    }


def worker(arguments):
    root = arguments.worker_source.resolve()
    sys.path.insert(0, str(root))
    from kairos_skills.catalog import catalog_home_id
    from kairos_skills.mutations import SkillMutationService
    from kairos_state import connect
    from kairos_state.migrations import migrate
    from kairos_state.repositories.skill_mutations import SkillMutationRepository

    module = Path(inspect.getfile(SkillMutationService)).resolve()
    if not module.is_relative_to(root):
        raise RuntimeError("benchmark_imported_wrong_revision")
    result = {
        "revision": arguments.revision,
        "requested_schema": arguments.schema,
        "module_sha256": digest(module),
        "scenarios": [],
    }
    with tempfile.TemporaryDirectory(prefix="kairos-writer-fixture-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        source = Path(temporary) / "SKILL.md"
        with ExitStack() as stack:
            connection = connect(home / "state.db")
            stack.callback(connection.close)
            migrate(connection, target=arguments.schema)
            version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
            if version != arguments.schema:
                raise RuntimeError("benchmark_schema_differs")
            result["observed_schema"] = version
            repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
            kwargs = {}
            if "policy" in inspect.signature(SkillMutationService).parameters:
                from kairos_skills.org_policy import observe_org_policy
                from kairos_skills.org_policy_contract import OrgPolicyContext

                kwargs["policy"] = stack.enter_context(
                    observe_org_policy(OrgPolicyContext("none", None), home)
                )
            service = SkillMutationService(home, repository, **kwargs)
            created = []

            def add(*, profiled=False):
                return one_add(service, source, home, created, root, profiled=profiled)

            for prefix in arguments.prefixes:
                seeding = time.perf_counter()
                while len(created) < prefix:
                    add()
                if len(created) != prefix:
                    raise RuntimeError("benchmark_scenarios_overlap")
                seed_seconds = time.perf_counter() - seeding
                add()  # aquecimento real: registrado separadamente do prefixo
                samples = [add() for _ in range(arguments.samples)]
                profile = add(profiled=True)
                durations = [sample["elapsed_ms"] for sample in samples]
                verified = verify_fixture(connection, home, created, version)
                scenario = {
                    "seed_creations": prefix,
                    "seed_seconds": seed_seconds,
                    "warmup_adds": 1,
                    "samples": samples,
                    "median_ms": statistics.median(durations),
                    "min_ms": min(durations),
                    "max_ms": max(durations),
                    "profiled_add": profile,
                    "final_creations": len(created),
                    **verified,
                }
                result["scenarios"].append(scenario)
                print(json.dumps({"revision": arguments.revision, **scenario}), flush=True)
    arguments.output.write_text(json.dumps(result, indent=2) + "\n")


def git_output(*arguments):
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("benchmark_git_unavailable")
    return subprocess.check_output([executable, *arguments])  # noqa: S603 — subcomandos fixos; SHA validado; sem shell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--prefixes", type=int, nargs="+", default=[0, 40, 120])
    parser.add_argument("--case", action="append", help="revisão:schema; pode repetir")
    parser.add_argument("--worker-source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--revision", help=argparse.SUPPRESS)
    parser.add_argument("--schema", type=int, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.samples < 3:
        parser.error("--samples deve ser pelo menos 3")
    if arguments.prefixes != sorted(set(arguments.prefixes)) or arguments.prefixes[0] < 0:
        parser.error("prefixes devem ser únicos, crescentes e não negativos")
    if any(
        b - a <= arguments.samples + 2
        for a, b in zip(arguments.prefixes, arguments.prefixes[1:], strict=False)
    ):
        parser.error("prefixes precisam acomodar aquecimento, amostras e perfil")
    if arguments.worker_source:
        worker(arguments)
        return
    cases = arguments.case or ["d730822:9", "f9f9a6d:10", "b7346ad:11"]
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "cpu_affinity_count": len(os.sched_getaffinity(0)),
        "initial_load": os.getloadavg(),
        "script_sha256": digest(Path(__file__)),
        "method": "add real; conexão e política abertas; sem profiler nos tempos; fixtures temporárias; prefixo cresce com cada add medido; perfil separado; sem cobertura",
        "cases": [],
    }
    for case in cases:
        reference, schema = case.split(":")
        if not re.fullmatch(r"[0-9a-f]{7,40}", reference):
            parser.error("a revisão precisa ser um SHA hexadecimal de commit")
        revision = git_output("rev-parse", "--verify", reference + "^{commit}").decode().strip()
        packages = [
            name
            for name in git_output("ls-tree", "-d", "--name-only", revision).decode().splitlines()
            if name.startswith("kairos_")
        ]
        if not packages:
            raise RuntimeError("benchmark_revision_without_packages")
        with tempfile.TemporaryDirectory(prefix="kairos-writer-source-") as temporary:
            root = Path(temporary)
            bundle = git_output("archive", revision, "--", *packages)
            with tarfile.open(fileobj=io.BytesIO(bundle)) as archive:
                archive.extractall(root, filter="data")
            output = root / "measurement.json"
            subprocess.run(  # noqa: S603 — intérprete atual, script local e argumentos sem shell
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker-source",
                    str(root),
                    "--revision",
                    revision,
                    "--schema",
                    schema,
                    "--output",
                    str(output),
                    "--samples",
                    str(arguments.samples),
                    "--prefixes",
                    *map(str, arguments.prefixes),
                ],
                check=True,
            )
            result["cases"].append(json.loads(output.read_text()))
            arguments.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
