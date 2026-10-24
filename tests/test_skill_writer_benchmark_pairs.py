"""O gate rejeita evidência incompleta e ganhos que violam o protocolo."""

import copy
import unittest

from scripts.benchmark_skill_writer_pairs import compare_pairs

TIMELINE = "kairos_state/repositories/skill_mutations.py:validate_skill_timeline"
LEGACY = "kairos_state/repositories/skill_mutations.py:_legacy_get"


class BenchmarkPairsTests(unittest.TestCase):
    def setUp(self):
        self.baseline_sha = "a" * 40
        self.candidate_sha = "b" * 40
        self.pairs = [
            {
                "index": index,
                "order": ["candidate", "baseline"] if index == 2 else ["baseline", "candidate"],
                "baseline": self.output(self.baseline_sha, 100, 903),
                "candidate": self.output(self.candidate_sha, 90, 775),
            }
            for index in (1, 2, 3)
        ]

    @staticmethod
    def output(revision, duration, loads):
        scenarios = []
        for prefix in (0, 40, 120):
            scenarios.append(
                {
                    "seed_creations": prefix,
                    "seed_seconds": 0,
                    "warmup_adds": 1,
                    "samples": [
                        {"prior_creations": prefix + offset, "elapsed_ms": duration}
                        for offset in range(1, 8)
                    ],
                    "median_ms": duration,
                    "min_ms": duration,
                    "max_ms": duration,
                    "profiled_add": {
                        "prior_creations": prefix + 8,
                        "elapsed_ms": 200,
                        "profile": {
                            TIMELINE: {"calls": 20, "own_s": 0.01, "cumulative_s": 0.02},
                            LEGACY: {"calls": loads, "own_s": 0.03, "cumulative_s": 0.04},
                        },
                    },
                    "final_creations": prefix + 9,
                    "active_timeline": "skill_publication_timeline",
                    "timeline_events": 2 * (prefix + 9),
                    "real_installed_trees_verified": prefix + 9,
                    "integrity": "ok",
                }
            )
        return {
            "python": "3.11.16",
            "python_executable": "/fixture/python",
            "platform": "fixture-linux",
            "sqlite": "3.53.1",
            "cpu_affinity_count": 4,
            "initial_load": [0, 0, 0],
            "script_sha256": "c" * 64,
            "method": "fixture; tempos sem profiler; perfil separado",
            "cases": [
                {
                    "revision": revision,
                    "requested_schema": 11,
                    "observed_schema": 11,
                    "module_sha256": "d" * 64,
                    "modules_sha256": {
                        "kairos_skills/mutations.py": "d" * 64,
                        "kairos_state/repositories/skill_mutations.py": "e" * 64,
                    },
                    "scenarios": scenarios,
                }
            ],
        }

    def compare(self, pairs=None):
        return compare_pairs(
            self.pairs if pairs is None else pairs,
            baseline_sha=self.baseline_sha,
            candidate_sha=self.candidate_sha,
        )

    @staticmethod
    def scenarios(pair, role="candidate"):
        return pair[role]["cases"][0]["scenarios"]

    @staticmethod
    def duration(scenario, value):
        for sample in scenario["samples"]:
            sample["elapsed_ms"] = value
        scenario.update(median_ms=value, min_ms=value, max_ms=value)

    def test_complete_paired_gain_is_accepted(self):
        result = self.compare()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["paired_gain_percent"][120], [10, 10, 10])
        self.assertEqual(result["median_gain_percent"], 10)

    def test_requires_three_complete_compatible_pairs(self):
        changes = [
            lambda p: p.pop(),
            lambda p: p.append(copy.deepcopy(p[0])),
            lambda p: p[1].update(index=1),
            lambda p: p[1].update(order=["baseline", "candidate"]),
            lambda p: p[0]["candidate"]["cases"][0].update(revision="a" * 40),
            lambda p: p[0]["candidate"]["cases"][0].update(observed_schema=10),
            lambda p: p[0]["candidate"].update(script_sha256="f" * 64),
            lambda p: p[0]["candidate"].update(python="3.12.0"),
            lambda p: p[0]["candidate"]["cases"].append({}),
            lambda p: self.scenarios(p[0])[0]["samples"].pop(),
            lambda p: self.scenarios(p[0])[0].update(median_ms=1),
            lambda p: self.scenarios(p[0])[0].update(integrity="broken"),
            lambda p: self.scenarios(p[0])[0].update(timeline_events=0),
            lambda p: self.scenarios(p[0])[0].update(real_installed_trees_verified=0),
            lambda p: p[2]["candidate"]["cases"][0]["modules_sha256"].update(
                {"kairos_state/repositories/skill_mutations.py": "f" * 64}
            ),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                pairs = copy.deepcopy(self.pairs)
                change(pairs)
                with self.assertRaises(ValueError):
                    self.compare(pairs)
        for value in (float("nan"), float("inf"), 0, -1, True):
            with self.subTest(value=value):
                pairs = copy.deepcopy(self.pairs)
                self.scenarios(pairs[0])[0]["samples"][0]["elapsed_ms"] = value
                with self.assertRaises(ValueError):
                    self.compare(pairs)

    def test_rejects_gain_that_skips_validation(self):
        for role in ("baseline", "candidate"):
            with self.subTest(role=role):
                pairs = copy.deepcopy(self.pairs)
                self.scenarios(pairs[1], role)[2]["profiled_add"]["profile"][TIMELINE]["calls"] = 19
                result = self.compare(pairs)
                self.assertFalse(result["accepted"])
                self.assertTrue(result["failures"])

    def test_rejects_gain_without_reduction_of_real_reconstructions(self):
        self.scenarios(self.pairs[0])[2]["profiled_add"]["profile"][LEGACY]["calls"] = 903
        self.assertFalse(self.compare()["accepted"])

    def test_applies_paired_thresholds_without_rounding_or_best_run_selection(self):
        for value, accepted in ((90, True), (90.00000001, False)):
            with self.subTest(duration=value):
                pairs = copy.deepcopy(self.pairs)
                for pair in pairs:
                    self.duration(self.scenarios(pair)[2], value)
                self.assertEqual(self.compare(pairs)["accepted"], accepted)
        self.duration(self.scenarios(self.pairs[0])[2], 101)
        for pair in self.pairs[1:]:
            self.duration(self.scenarios(pair)[2], 70)
        self.assertFalse(self.compare()["accepted"])

    def test_small_scenarios_reject_only_combined_regression(self):
        for baseline, candidate, accepted in (
            (100, 106, False),
            (100, 105, True),
            (40, 42.1, True),
            (200, 206, True),
        ):
            for prefix_index in (0, 1):
                with self.subTest(baseline=baseline, candidate=candidate, scenario=prefix_index):
                    pairs = copy.deepcopy(self.pairs)
                    for pair in pairs:
                        self.duration(self.scenarios(pair, "baseline")[prefix_index], baseline)
                        self.duration(self.scenarios(pair)[prefix_index], candidate)
                    self.assertEqual(self.compare(pairs)["accepted"], accepted)

    def test_uses_median_of_raw_samples(self):
        for pair in self.pairs:
            scenario = self.scenarios(pair)[2]
            for sample, duration in zip(
                scenario["samples"], (1, 1, 1, 90, 1000, 1000, 1000), strict=True
            ):
                sample["elapsed_ms"] = duration
            scenario.update(min_ms=1, max_ms=1000)
        self.assertTrue(self.compare()["accepted"])
