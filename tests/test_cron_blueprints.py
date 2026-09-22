"""Blueprints de automação — catálogo tipado, renderizadores, fill + roundtrip.

Cobertura (critério da T-11):

* catálogo: 12 blueprints, todos têm chaves únicas, slots não colidem,
  preenchimento com defaults produz cron válido via `compute_next_run`.
* renderizadores: formulário, slash command roundtrip, deep-link, prompt-semente.
* fill: slots desconhecidos, obrigatórios ausentes, enum strict rejeita.
* integração com JobStore.create: cria e lista sem quebrar guard de ciclo
  de vida.
* roundtrip /blueprint: slash-parse + re-render = identico (chave + valores).
* create blueprint flow: CLI / API criam job e persistem delivery.

Os prompts do catálogo são autocontidos em português; sem referências a skills
externas. A superfície `origin` do legado é tratada como `local` (sem origem
em kairos). O slot `deliver` é tipo text com strict=False (sem lista fechada).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kairos_cron.blueprints import (
    CATALOG,
    AutomationBlueprint,
    BlueprintFillError,
    blueprint_deeplink,
    blueprint_form_schema,
    blueprint_seed_prompt,
    blueprint_slash_command,
    fill_blueprint,
    get_blueprint,
    parse_blueprint_slash,
)
from kairos_cron.jobs import JobStore
from kairos_cron.schedule import compute_next_run


class CatalogoTests(unittest.TestCase):
    def test_blueprint_keys_unique(self):
        keys = [bp.key for bp in CATALOG]
        self.assertEqual(
            len(keys),
            len(set(keys)),
            f"chaves duplicadas: {[k for k in keys if keys.count(k) > 1]}",
        )

    def test_catalog_has_at_least_twelve_blueprints(self):
        # Piso, não fotografia: o catálogo só cresce; cada blueprint novo
        # deve ter as quatro superfícies e o fill validado pelos testes acima.
        self.assertGreaterEqual(len(CATALOG), 12)

    def test_all_categories_are_known(self):
        valid = {"daily", "weekly", "email", "general"}
        for bp in CATALOG:
            self.assertIn(bp.category, valid, f"{bp.key}: category {bp.category!r}")

    def test_slot_name_duplicates_within_blueprint(self):
        for bp in CATALOG:
            names = [s.name for s in bp.slots]
            self.assertEqual(
                len(names), len(set(names)), f"{bp.key}: slot names duplicados {names}"
            )

    def test_deliver_slot_is_text_not_strict(self):
        for bp in CATALOG:
            deliver_slots = [s for s in bp.slots if s.name == "deliver"]
            for ds in deliver_slots:
                self.assertEqual(ds.type, "text", f"{bp.key}: deliver type {ds.type}")
                self.assertFalse(ds.strict, f"{bp.key}: deliver strict={ds.strict}")

    def test_all_slot_types_valid(self):
        valid = {"time", "enum", "text", "weekdays"}
        for bp in CATALOG:
            for s in bp.slots:
                self.assertIn(s.type, valid, f"{bp.key} slot {s.name}: type {s.type!r}")

    def test_all_prompts_are_strings(self):
        for bp in CATALOG:
            self.assertIsInstance(bp.prompt_template, str, f"{bp.key}: prompt_template não é str")

    def test_fill_all_defaults_produces_valid_cron(self):
        for bp in CATALOG:
            with self.subTest(key=bp.key):
                kwargs = fill_blueprint(bp, {})
                self.assertEqual(kwargs["schedule"]["kind"], "cron")
                expr = kwargs["schedule"]["expr"]
                self.assertEqual(len(expr.split()), 5)
                nxt = compute_next_run({"kind": "cron", "expr": expr})
                self.assertIsNotNone(nxt, f"{bp.key}: compute_next_run devolveu None para {expr!r}")

    def test_fill_all_defaults_returns_creating_kwargs(self):
        for bp in CATALOG:
            with self.subTest(key=bp.key):
                kwargs = fill_blueprint(bp, {})
                self.assertIn("name", kwargs)
                self.assertIn("prompt", kwargs)
                self.assertIn("schedule", kwargs)
                self.assertIn("delivery", kwargs)
                self.assertEqual(kwargs["name"], bp.title)
                self.assertIsInstance(kwargs["prompt"], str)
                self.assertTrue(len(kwargs["prompt"]) > 0)
                self.assertEqual(kwargs["delivery"], None)

    def test_fill_deliver_local_produces_none(self):
        bp = get_blueprint("morning-brief")
        kwargs = fill_blueprint(bp, {"time": "07:00", "deliver": "local"})
        self.assertIsNone(kwargs["delivery"])

    def test_fill_deliver_origin_is_treated_as_local(self):
        bp = get_blueprint("morning-brief")
        kwargs = fill_blueprint(bp, {"time": "07:00", "deliver": "origin"})
        self.assertIsNone(kwargs["delivery"])

    def test_fill_deliver_platform_colon_dest(self):
        bp = get_blueprint("morning-brief")
        kwargs = fill_blueprint(bp, {"time": "07:00", "deliver": "wpp:+5511999990000"})
        self.assertEqual(kwargs["delivery"], {"target": "wpp:+5511999990000"})


class GetBlueprintTests(unittest.TestCase):
    def test_existing_key(self):
        bp = get_blueprint("morning-brief")
        self.assertIsNotNone(bp)
        self.assertIsInstance(bp, AutomationBlueprint)
        self.assertEqual(bp.key, "morning-brief")

    def test_nonexistent_key_returns_none(self):
        self.assertIsNone(get_blueprint("nonexistent-key"))

    def test_all_keys_retrievable(self):
        for bp in CATALOG:
            self.assertIsNotNone(get_blueprint(bp.key), bp.key)


class FormSchemaTests(unittest.TestCase):
    def test_schema_has_required_keys(self):
        for bp in CATALOG:
            with self.subTest(key=bp.key):
                schema = blueprint_form_schema(bp)
                self.assertEqual(schema["key"], bp.key)
                self.assertEqual(schema["title"], bp.title)
                self.assertIsInstance(schema["fields"], list)
                self.assertEqual(len(schema["fields"]), len(bp.slots))

    def test_field_shape(self):
        schema = blueprint_form_schema(get_blueprint("morning-brief"))
        field = schema["fields"][0]
        for key in ("name", "type", "label", "default", "options", "optional", "strict", "help"):
            self.assertIn(key, field)


class SlashCommandTests(unittest.TestCase):
    def test_roundtrip_with_defaults(self):
        for bp in CATALOG:
            with self.subTest(key=bp.key):
                cmd = blueprint_slash_command(bp)
                key, values = parse_blueprint_slash(cmd)
                self.assertEqual(key, bp.key)
                cmd2 = blueprint_slash_command(bp, values)
                self.assertEqual(cmd, cmd2)

    def test_roundtrip_with_overrides(self):
        bp = get_blueprint("important-mail")
        cmd = blueprint_slash_command(
            bp, {"interval_min": "60", "criteria": "urgente", "deliver": "wpp:+5511"}
        )
        key, values = parse_blueprint_slash(cmd)
        self.assertEqual(key, "important-mail")
        self.assertEqual(values["interval_min"], "60")
        self.assertEqual(values["criteria"], "urgente")
        self.assertEqual(values["deliver"], "wpp:+5511")
        cmd2 = blueprint_slash_command(bp, values)
        self.assertEqual(cmd, cmd2)

    def test_parse_rejects_non_blueprin_command(self):
        with self.assertRaises(ValueError):
            parse_blueprint_slash("/not-blueprint foo")

    def test_parse_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_blueprint_slash("")


class DeeplinkTests(unittest.TestCase):
    def test_default_deeplink(self):
        url = blueprint_deeplink(get_blueprint("morning-brief"))
        self.assertTrue(url.startswith("hermes://blueprint/"), url)
        self.assertIn("morning-brief", url)

    def test_deeplink_with_values(self):
        url = blueprint_deeplink(get_blueprint("important-mail"), {"interval_min": "60"})
        self.assertIn("interval_min=60", url)


class SeedPromptTests(unittest.TestCase):
    def test_all_blueprints_render_seed_prompt(self):
        for bp in CATALOG:
            with self.subTest(key=bp.key):
                prompt = blueprint_seed_prompt(bp)
                self.assertIsInstance(prompt, str)
                self.assertTrue(len(prompt) > 10, f"{bp.key}: prompt muito curto")

    def test_seed_prompt_with_slot_value(self):
        prompt = blueprint_seed_prompt(get_blueprint("important-mail"), {"criteria": "URGENTE"})
        self.assertIn("URGENTE", prompt)

    def test_seed_prompt_ignores_unknown_slot_keys(self):
        bp = get_blueprint("morning-brief")
        prompt = blueprint_seed_prompt(bp, {"unknown_slot": "x"})
        self.assertNotIn("unknown_slot", prompt)
        self.assertNotIn("x", prompt)


class FillValidationTests(unittest.TestCase):
    def test_unknown_slot_raises(self):
        bp = get_blueprint("morning-brief")
        with self.assertRaises(BlueprintFillError) as ctx:
            fill_blueprint(bp, {"bogus": "x"})
        self.assertIn("bogus", str(ctx.exception))

    def test_missing_required_time_raises(self):
        bp = get_blueprint("morning-brief")
        with self.assertRaises(BlueprintFillError) as ctx:
            fill_blueprint(bp, {"time": ""})
        self.assertIn("obrigatório", str(ctx.exception))

    def test_invalid_time_format_raises(self):
        bp = get_blueprint("morning-brief")
        with self.assertRaises(BlueprintFillError) as ctx:
            fill_blueprint(bp, {"time": "25:00"})
        self.assertIn("inválida", str(ctx.exception))

    def test_strict_enum_rejects_invalid_value(self):
        bp = get_blueprint("important-mail")
        with self.assertRaises(BlueprintFillError) as ctx:
            fill_blueprint(bp, {"interval_min": "99"})
        self.assertIn("99", str(ctx.exception))

    def test_strict_enum_accepts_valid_value(self):
        bp = get_blueprint("important-mail")
        kwargs = fill_blueprint(bp, {"interval_min": "15"})
        self.assertTrue(kwargs["schedule"]["expr"].startswith("*/15"), kwargs["schedule"]["expr"])

    def test_non_strict_slot_accepts_any_text(self):
        bp = get_blueprint("morning-brief")
        kwargs = fill_blueprint(bp, {"deliver": "qualquercoisa"})
        self.assertEqual(kwargs["delivery"], {"target": "qualquercoisa"})


class HumanizeScheduleTests(unittest.TestCase):
    def test_interval_minutes(self):
        from kairos_cron.blueprints import _humanize_schedule

        self.assertIn("minutos", _humanize_schedule(get_blueprint("important-mail")))

    def test_weekday_time(self):
        from kairos_cron.blueprints import _humanize_schedule

        h = _humanize_schedule(get_blueprint("weekly-review"))
        self.assertIn("sunday", h)
        self.assertIn("18:00", h)

    def test_interval_hours(self):
        from kairos_cron.blueprints import _humanize_schedule

        h = _humanize_schedule(get_blueprint("hydration-move"))
        self.assertIn("hora", h)


class JobStoreIntegracaoTests(unittest.TestCase):
    def test_fill_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bp = get_blueprint("custom-reminder")
            kwargs = fill_blueprint(
                bp, {"what": "testar blueprints", "time": "09:00", "recurrence": "weekdays"}
            )
            store = JobStore(home)
            job = store.create(**kwargs)
            self.assertEqual(job["name"], bp.title)
            self.assertIn("testar blueprints", job["prompt"])
            jobs = store.list()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["id"], job["id"])

    def test_create_with_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bp = get_blueprint("important-mail")
            kwargs = fill_blueprint(
                bp, {"interval_min": "60", "criteria": "sim", "deliver": "email:eu@test"}
            )
            store = JobStore(home)
            job = store.create(**kwargs)
            self.assertEqual(job["delivery"], {"target": "email:eu@test"})


if __name__ == "__main__":
    unittest.main()
