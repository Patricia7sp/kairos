"""Executa três pares AB/BA/AB e recusa evidências incompatíveis ou ROI insuficiente."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

PREFIXES = (0, 40, 120)
ORDERS = (("baseline", "candidate"), ("candidate", "baseline"), ("baseline", "candidate"))
REPOSITORY = "kairos_state/repositories/skill_mutations.py"
TIMELINE = REPOSITORY + ":validate_skill_timeline"
LEGACY = REPOSITORY + ":_legacy_get"
ENVIRONMENT = ("python", "python_executable", "platform", "sqlite", "cpu_affinity_count", "method")


def require(condition):
    if not condition:
        raise ValueError("benchmark_evidence_invalid")


def number(value, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value))
    require(value > 0 if positive else value >= 0)
    return value


def hexadecimal(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value)


def validate_output(output, revision):
    require(hexadecimal(output["script_sha256"], 64))
    require(type(output["cpu_affinity_count"]) is int and output["cpu_affinity_count"] > 0)
    for key in ENVIRONMENT:
        require(bool(output[key]))
    require(len(output["initial_load"]) == 3)
    for value in output["initial_load"]:
        number(value)
    require(len(output["cases"]) == 1)
    case = output["cases"][0]
    require(case["revision"] == revision)
    require(case["requested_schema"] == case["observed_schema"] == 11)
    modules = case["modules_sha256"]
    require(REPOSITORY in modules and "kairos_skills/mutations.py" in modules)
    require(all(hexadecimal(value, 64) for value in modules.values()))
    require(case["module_sha256"] == modules["kairos_skills/mutations.py"])
    require([s["seed_creations"] for s in case["scenarios"]] == list(PREFIXES))
    for scenario in case["scenarios"]:
        prefix = scenario["seed_creations"]
        number(scenario["seed_seconds"])
        require(scenario["warmup_adds"] == 1)
        samples = scenario["samples"]
        require(len(samples) == 7)
        require([s["prior_creations"] for s in samples] == list(range(prefix + 1, prefix + 8)))
        durations = [number(s["elapsed_ms"], positive=True) for s in samples]
        require(number(scenario["median_ms"], positive=True) == statistics.median(durations))
        require(scenario["min_ms"] == min(durations) and scenario["max_ms"] == max(durations))
        require(
            scenario["final_creations"] == scenario["real_installed_trees_verified"] == prefix + 9
        )
        require(scenario["timeline_events"] == 2 * (prefix + 9))
        require(scenario["active_timeline"] == "skill_publication_timeline")
        require(scenario["integrity"] == "ok")
        profiled = scenario["profiled_add"]
        require(profiled["prior_creations"] == prefix + 8)
        number(profiled["elapsed_ms"], positive=True)
        profile = profiled["profile"]
        require(TIMELINE in profile and LEGACY in profile)
        for entry in profile.values():
            require(type(entry["calls"]) is int and entry["calls"] >= 0)
            number(entry["own_s"])
            number(entry["cumulative_s"])
    return case


def _compare_pairs(pairs, baseline_sha, candidate_sha):
    require(hexadecimal(baseline_sha, 40) and hexadecimal(candidate_sha, 40))
    require(baseline_sha != candidate_sha and len(pairs) == 3)
    reference = pairs[0]["baseline"]
    modules_by_role = {}
    gains = {prefix: [] for prefix in PREFIXES}
    regressions_ms = {prefix: [] for prefix in PREFIXES}
    failures = []
    for index, pair in enumerate(pairs, 1):
        require(type(pair["index"]) is int and pair["index"] == index)
        require(pair["order"] == list(ORDERS[index - 1]))
        cases = {}
        for role, revision in (("baseline", baseline_sha), ("candidate", candidate_sha)):
            output = pair[role]
            cases[role] = validate_output(output, revision)
            require(output["script_sha256"] == reference["script_sha256"])
            require(all(output[key] == reference[key] for key in ENVIRONMENT))
            modules = cases[role]["modules_sha256"]
            require(modules == modules_by_role.setdefault(role, modules))
        for base, candidate in zip(
            cases["baseline"]["scenarios"], cases["candidate"]["scenarios"], strict=True
        ):
            prefix = base["seed_creations"]
            b = statistics.median(sample["elapsed_ms"] for sample in base["samples"])
            c = statistics.median(sample["elapsed_ms"] for sample in candidate["samples"])
            gains[prefix].append(100 * (b - c) / b)
            regressions_ms[prefix].append(c - b)
            for role, scenario in (("baseline", base), ("candidate", candidate)):
                if scenario["profiled_add"]["profile"][TIMELINE]["calls"] != 20:
                    failures.append(f"pair_{index}_{role}_{prefix}_timeline_validation_differs")
            if prefix == 120:
                b_loads = base["profiled_add"]["profile"][LEGACY]["calls"]
                c_loads = candidate["profiled_add"]["profile"][LEGACY]["calls"]
                if not 0 < c_loads < b_loads:
                    failures.append(f"pair_{index}_reconstructions_not_reduced")
    median_gain = statistics.median(gains[120])
    if any(gain <= 0 for gain in gains[120]):
        failures.append("scenario_120_did_not_improve_in_every_pair")
    if median_gain < 10:
        failures.append("scenario_120_median_gain_below_10_percent")
    for prefix in (0, 40):
        if (
            statistics.median([-gain for gain in gains[prefix]]) > 5
            and statistics.median(regressions_ms[prefix]) > 5
        ):
            failures.append(f"scenario_{prefix}_regression_above_5_percent_and_5_ms")
    return {
        "accepted": not failures,
        "failures": failures,
        "paired_gain_percent": gains,
        "paired_regression_ms": regressions_ms,
        "median_gain_percent": median_gain,
    }


def compare_pairs(pairs: list[dict], *, baseline_sha: str, candidate_sha: str) -> dict:
    try:
        return _compare_pairs(pairs, baseline_sha, candidate_sha)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError):
        raise ValueError("benchmark_evidence_invalid") from None


def git_bytes(*arguments):
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("benchmark_git_unavailable")
    return subprocess.check_output([git, *arguments])  # noqa: S603 — comandos fixos, sem shell


def resolve_revision(value):
    require(hexadecimal(value, 40))
    resolved = git_bytes("rev-parse", "--verify", value + "^{commit}").decode().strip()
    require(resolved == value)
    return resolved


def verify_modules(output, revision):
    case = output["cases"][0]
    for path, observed in case["modules_sha256"].items():
        require(
            path
            in (
                REPOSITORY,
                "kairos_skills/mutations.py",
                "kairos_state/repositories/skill_record_memo.py",
            )
        )
        require(hashlib.sha256(git_bytes("show", revision + ":" + path)).hexdigest() == observed)


def run_pairs(baseline, candidate, output_dir):
    baseline = resolve_revision(baseline)
    candidate = resolve_revision(candidate)
    require(baseline != candidate)
    harness = Path(__file__).with_name("benchmark-skill-writers.py").resolve()
    harness_hash = hashlib.sha256(harness.read_bytes()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=False)
    pairs = []
    references = []
    for index, order in enumerate(ORDERS, 1):
        directory = output_dir / f"pair-{index}"
        directory.mkdir()
        pair = {"index": index, "order": list(order)}
        for role in order:
            revision = baseline if role == "baseline" else candidate
            output = directory / f"{role}.json"
            logfile = directory / f"{role}.log"
            with logfile.open("w") as log:
                subprocess.run(  # noqa: S603 — intérprete atual, harness local, argumentos validados
                    [
                        sys.executable,
                        str(harness),
                        "--case",
                        revision + ":11",
                        "--samples",
                        "7",
                        "--prefixes",
                        "0",
                        "40",
                        "120",
                        "--output",
                        str(output.resolve()),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            require(hashlib.sha256(harness.read_bytes()).hexdigest() == harness_hash)
            pair[role] = json.loads(output.read_text())
            validate_output(pair[role], revision)
            require(pair[role]["script_sha256"] == harness_hash)
            verify_modules(pair[role], revision)
            references.append(str(output.relative_to(output_dir)))
            print(
                json.dumps({"event": "measurement_complete", "pair": index, "role": role}),
                flush=True,
            )
        pairs.append(pair)
    summary = {
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "script_sha256": harness_hash,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "measurements": references,
        **compare_pairs(pairs, baseline_sha=baseline, candidate_sha=candidate),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        summary = run_pairs(arguments.baseline, arguments.candidate, arguments.output_dir)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(
            json.dumps(
                {
                    "event": "benchmark_failed",
                    "error_class": type(error).__name__,
                    "accepted": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, allow_nan=False))
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
