"""Tarefa 09 — skills.

Critério de pronto: "Quarentena é etapa **obrigatória** do pipeline, com
contenção de caminho e rejeição de symlink na promoção; skill divergente
preserva edição local (ADR 013)."
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kairos_domain.ownership import (
    Actor,
    CronReferenceIndex,
    Provenance,
    Skill,
    SkillState,
)
from kairos_skills.curator import (
    CuratorConfig,
    apply_automatic_transitions,
    reconcile_classification,
    should_run_now,
)
from kairos_skills.frontmatter import (
    SKILL_PROMPT_DESC_LIMIT,
    Frontmatter,
    FrontmatterError,
    parse_frontmatter,
    validate_frontmatter,
)
from kairos_skills.hub import (
    QuarantineError,
    SkillBundle,
    UnsafeQuarantinePath,
    install_from_quarantine,
    quarantine_bundle,
    scan_quarantined,
)
from kairos_skills.sync import (
    MANIFEST_NAME,
    NO_BUNDLED_SKILLS_MARKER,
    origin_hash,
    read_manifest,
    sync_bundled_skills,
    write_manifest,
)


def fm(**kw) -> Frontmatter:
    base = {"name": "minha-skill", "description": "Faz uma coisa útil.", "version": "1.0.0"}
    return Frontmatter(**{**base, **kw})


# ===========================================================================
class FrontmatterTests(unittest.TestCase):
    """RF-05 — e o limite dos 60 caracteres."""

    def test_frontmatter_valido_passa(self):
        validate_frontmatter(fm(), new_skill=True)

    def test_o_limite_de_60_e_FUNCIONAL_nao_estetico(self):
        """Descrição longa não produz índice feio: produz skill que nunca é
        usada, porque o índice trunca e o modelo roteia pela versão truncada."""
        longa = "A" * (SKILL_PROMPT_DESC_LIMIT + 1) + "."
        with self.assertRaises(FrontmatterError) as ctx:
            validate_frontmatter(fm(description=longa), new_skill=True)
        msg = str(ctx.exception)
        self.assertIn("nunca rotearia", msg)
        self.assertIn("funcional, não estético", msg)
        self.assertIn(str(len(longa)), msg, "a mensagem diz quantos caracteres sobraram")

    def test_exatamente_60_passa(self):
        d = "A" * (SKILL_PROMPT_DESC_LIMIT - 1) + "."
        self.assertEqual(len(d), SKILL_PROMPT_DESC_LIMIT)
        validate_frontmatter(fm(description=d), new_skill=True)

    def test_name_precisa_ser_kebab_case(self):
        for ruim in ("MinhaSkill", "minha_skill", "minha skill", "-x", ""):
            with self.subTest(name=ruim), self.assertRaises(FrontmatterError):
                validate_frontmatter(fm(name=ruim))

    def test_description_precisa_terminar_em_ponto_na_criacao(self):
        with self.assertRaises(FrontmatterError):
            validate_frontmatter(fm(description="sem ponto final"), new_skill=True)
        # Mas não na leitura: skill antiga não vira erro a cada sessão.
        validate_frontmatter(fm(description="sem ponto final"), new_skill=False)

    def test_version_semver_so_e_cobrada_na_criacao(self):
        with self.assertRaises(FrontmatterError):
            validate_frontmatter(fm(version="v1"), new_skill=True)
        validate_frontmatter(fm(version="v1"), new_skill=False)

    def test_author_ausente_e_valido_e_nunca_derivado_do_ambiente(self):
        """Skills são compartilhadas e publicadas; um nome vindo do login ou
        do git config seria vazamento de privacidade não consentido."""
        validate_frontmatter(fm(author=None), new_skill=True)
        with self.assertRaises(FrontmatterError):
            validate_frontmatter(fm(author="   "))

    def test_parse_extrai_frontmatter_e_corpo(self):
        texto = (
            "---\n"
            "name: exemplo\n"
            "description: Uma skill de exemplo.\n"
            "version: 1.2.3\n"
            "metadata:\n"
            "  kairos:\n"
            "    tags: [a, b]\n"
            "---\n"
            "# Exemplo\n\ncorpo\n"
        )
        parsed, corpo = parse_frontmatter(texto)
        self.assertEqual(parsed.name, "exemplo")
        self.assertEqual(parsed.tags, ("a", "b"))
        self.assertTrue(corpo.startswith("# Exemplo"))

    def test_sem_bloco_de_frontmatter_levanta(self):
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("# Só markdown\n")

    def test_frontmatter_com_yaml_invalido_levanta_com_contexto(self):
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("---\nname: [nao\n  fecha\n---\ncorpo\n")


# ===========================================================================
class SyncTests(unittest.TestCase):
    """A política que se revelou HERANÇA, não decisão nova do Kairos."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bundled = self.root / "bundled"
        self.user = self.root / "user"
        self.bundled.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def mk_bundled(self, name, texto="v1"):
        d = self.bundled / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(texto, encoding="utf-8")
        return d

    def user_text(self, name):
        return (self.user / name / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_nova_e_copiada_e_o_hash_registrado(self):
        self.mk_bundled("a")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.copied, ["a"])
        self.assertEqual(self.user_text("a"), "v1")
        self.assertIn("a", read_manifest(self.user / MANIFEST_NAME))

    def test_caso1_bundled_inalterado_pula_sem_ler_a_copia_do_usuario(self):
        self.mk_bundled("a")
        sync_bundled_skills(self.bundled, self.user)
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.skipped, ["a"])

    def test_caso2_usuario_nunca_tocou_entao_e_seguro_atualizar(self):
        self.mk_bundled("a", "v1")
        sync_bundled_skills(self.bundled, self.user)
        self.mk_bundled("a", "v2")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.updated, ["a"])
        self.assertEqual(self.user_text("a"), "v2")

    def test_caso3_o_usuario_EDITOU_entao_a_edicao_local_VENCE(self):
        """ADR 013 — salvage preserva autoria."""
        self.mk_bundled("a", "v1")
        sync_bundled_skills(self.bundled, self.user)
        (self.user / "a" / "SKILL.md").write_text("minha versão", encoding="utf-8")
        self.mk_bundled("a", "v2")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.user_modified, ["a"])
        self.assertEqual(self.user_text("a"), "minha versão", "sem sobrescrita, sem merge")

    def test_a_comparacao_do_caso2_e_contra_o_hash_DE_ORIGEM(self):
        """É isso que distingue 'nunca mexeu' de 'editou' — comparar contra o
        bundled atual não distinguiria nada."""
        self.mk_bundled("a", "v1")
        sync_bundled_skills(self.bundled, self.user)
        # O usuário edita para exatamente o conteúdo do PRÓXIMO bundled.
        (self.user / "a" / "SKILL.md").write_text("v2", encoding="utf-8")
        self.mk_bundled("a", "v2")
        r = sync_bundled_skills(self.bundled, self.user)
        # Contra o bundled atual pareceria "igual"; contra a origem, é edição.
        self.assertEqual(r.user_modified, ["a"])

    def test_exclusao_pelo_usuario_e_RESPEITADA_nao_re_adicionada(self):
        self.mk_bundled("a")
        sync_bundled_skills(self.bundled, self.user)
        import shutil

        shutil.rmtree(self.user / "a")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.user_deleted, ["a"])
        self.assertFalse((self.user / "a").exists())

    def test_skill_removida_do_bundled_e_limpa_do_manifesto(self):
        self.mk_bundled("a")
        sync_bundled_skills(self.bundled, self.user)
        import shutil

        shutil.rmtree(self.bundled / "a")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.cleaned, ["a"])
        self.assertNotIn("a", read_manifest(self.user / MANIFEST_NAME))

    def test_manifesto_v1_e_auto_migrado_e_e_CONSERVADOR(self):
        """Hash vazio = origem desconhecida. Sem saber a origem, não dá para
        afirmar que o usuário não editou — então preserva."""
        self.mk_bundled("a", "v1")
        self.user.mkdir(parents=True, exist_ok=True)
        (self.user / "a").mkdir()
        (self.user / "a" / "SKILL.md").write_text("qualquer", encoding="utf-8")
        (self.user / MANIFEST_NAME).write_text("a\n", encoding="utf-8")  # v1
        self.assertEqual(read_manifest(self.user / MANIFEST_NAME), {"a": ""})
        self.mk_bundled("a", "v2")
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertEqual(r.user_modified, ["a"])

    def test_opt_out_por_perfil_zera_o_seeding(self):
        self.mk_bundled("a")
        self.user.mkdir(parents=True)
        (self.user / NO_BUNDLED_SKILLS_MARKER).touch()
        r = sync_bundled_skills(self.bundled, self.user)
        self.assertTrue(r.skipped_opt_out)
        self.assertEqual(r.copied, [])
        self.assertFalse((self.user / "a").exists())

    def test_o_hash_cobre_o_diretorio_inteiro_nao_so_o_SKILL_md(self):
        d = self.mk_bundled("a")
        antes = origin_hash(d)
        (d / "scripts").mkdir()
        (d / "scripts" / "run.sh").write_text("echo", encoding="utf-8")
        self.assertNotEqual(origin_hash(d), antes, "editar um script tem de mudar o hash")

    def test_manifesto_e_escrito_atomicamente(self):
        # Um manifesto meio escrito faria as skills sem entrada serem tratadas
        # como novas e re-copiadas por cima da edição do usuário.
        alvo = self.root / "m"
        write_manifest(alvo, {"a": "h1", "b": "h2"})
        self.assertEqual(read_manifest(alvo), {"a": "h1", "b": "h2"})
        self.assertFalse(alvo.with_suffix(".tmp").exists())


# ===========================================================================
class QuarentenaTests(unittest.TestCase):
    """A quarentena é etapa OBRIGATÓRIA, não gatilho de suspeita."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.q = self.root / "quarantine"
        self.skills = self.root / "skills"
        self.q.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def bundle(self, name="nova", files=None):
        return SkillBundle(name=name, files=files or {"SKILL.md": b"# Nova\n"})

    def test_o_pipeline_e_download_quarentena_scan_instalacao(self):
        path = quarantine_bundle(self.bundle(), self.q)
        self.assertEqual(scan_quarantined(path), [])
        destino = install_from_quarantine(path, self.q, self.skills)
        self.assertTrue((destino / "SKILL.md").is_file())
        self.assertFalse(path.exists(), "sai da quarentena ao ser promovida")

    def test_nome_de_arquivo_no_bundle_e_entrada_NAO_CONFIAVEL(self):
        """Um `../../.bashrc` escaparia da quarentena antes de qualquer scan."""
        with self.assertRaises(UnsafeQuarantinePath):
            quarantine_bundle(self.bundle(files={"../../escapou.txt": b"x"}), self.q)

    def test_nome_de_skill_hostil_e_recusado(self):
        for ruim in ("../fora", "a/b", ".oculta", ""):
            with self.subTest(name=ruim), self.assertRaises(QuarantineError):
                quarantine_bundle(self.bundle(name=ruim), self.q)

    def test_promocao_de_caminho_FORA_da_quarentena_e_recusada(self):
        fora = self.root / "fora"
        fora.mkdir()
        (fora / "SKILL.md").write_text("x", encoding="utf-8")
        with self.assertRaises(UnsafeQuarantinePath):
            install_from_quarantine(fora, self.q, self.skills)

    def test_dot_dot_no_meio_do_caminho_nao_passa(self):
        """Sem `resolve()`, um `..` no meio passaria pela contenção."""
        quarantine_bundle(self.bundle(), self.q)
        disfarcado = self.q / "nova" / ".." / ".." / "fora"
        # Especificamente UnsafeQuarantinePath: se fosse QuarantineError
        # genérico, o teste passaria também por "falta SKILL.md" e deixaria
        # de significar "a contenção pegou".
        with self.assertRaises(UnsafeQuarantinePath):
            install_from_quarantine(disfarcado, self.q, self.skills)

    def test_symlink_e_rejeitado_no_scan_E_na_promocao(self):
        """Reconferido na promoção porque entre o scan e ela o conteúdo pode
        ter mudado. E um link para ~/.ssh/id_rsa transformaria `skill_view`
        num leitor de arquivo arbitrário."""
        path = quarantine_bundle(self.bundle(), self.q)
        (path / "link").symlink_to("/etc/passwd")
        self.assertTrue(any("symlink" in f for f in scan_quarantined(path)))
        with self.assertRaises(UnsafeQuarantinePath):
            install_from_quarantine(path, self.q, self.skills)

    def test_bundle_sem_SKILL_md_e_reprovado(self):
        path = quarantine_bundle(self.bundle(files={"leia.md": b"x"}), self.q)
        self.assertIn("falta SKILL.md", scan_quarantined(path))
        with self.assertRaises(QuarantineError):
            install_from_quarantine(path, self.q, self.skills)


# ===========================================================================
class CuradorTests(unittest.TestCase):
    def cfg(self, **kw):
        return CuratorConfig(**kw)

    def test_a_consolidacao_e_opt_in_a_poda_NAO(self):
        c = self.cfg()
        self.assertTrue(c.enabled)
        self.assertFalse(c.consolidate, "o passe opinativo com custo de modelo é opt-in")

    def test_defaults_confirmados_no_legado(self):
        c = self.cfg()
        self.assertEqual(
            (c.interval_hours, c.min_idle_hours, c.stale_after_days, c.archive_after_days),
            (168, 2, 30, 90),
        )

    def test_portoes_de_cadencia(self):
        c = self.cfg()
        self.assertTrue(should_run_now(c, hours_since_last_run=200, idle_hours=3))
        self.assertFalse(should_run_now(c, hours_since_last_run=10, idle_hours=3))
        self.assertFalse(should_run_now(c, hours_since_last_run=200, idle_hours=0.5))
        self.assertFalse(
            should_run_now(self.cfg(paused=True), hours_since_last_run=200, idle_hours=3)
        )
        self.assertFalse(
            should_run_now(self.cfg(enabled=False), hours_since_last_run=200, idle_hours=3)
        )

    def test_transicoes_por_inatividade(self):
        s = Skill("x", Provenance.SEDIMENT)
        r = apply_automatic_transitions([s], days_unused={"x": 31}, cfg=self.cfg())
        self.assertEqual(r.transitions, {"x": SkillState.STALE})

        r = apply_automatic_transitions([s], days_unused={"x": 91}, cfg=self.cfg())
        self.assertEqual(r.transitions, {"x": SkillState.ARCHIVED})

    def test_skill_do_usuario_NAO_e_auto_curada_e_o_motivo_e_registrado(self):
        s = Skill("minha", Provenance.USER, state=SkillState.STALE)
        r = apply_automatic_transitions([s], days_unused={"minha": 999}, cfg=self.cfg())
        self.assertEqual(r.transitions, {})
        self.assertIn("minha", r.protected)
        self.assertIn("proveniência", r.protected["minha"])
        self.assertEqual(s.state, SkillState.STALE)

    def test_skill_referenciada_por_cron_PAUSADO_e_protegida(self):
        s = Skill("agendada", Provenance.SEDIMENT, state=SkillState.STALE)
        idx = CronReferenceIndex(referenced={"agendada"})
        r = apply_automatic_transitions(
            [s], days_unused={"agendada": 999}, cfg=self.cfg(), cron_index=idx
        )
        self.assertIn("agendada", r.protected)
        self.assertEqual(s.state, SkillState.STALE)

    def test_uma_protegida_nao_aborta_a_poda_das_demais(self):
        protegida = Skill("minha", Provenance.USER, state=SkillState.STALE)
        podavel = Skill("sedimento", Provenance.SEDIMENT, state=SkillState.STALE)
        r = apply_automatic_transitions(
            [protegida, podavel], days_unused={"minha": 999, "sedimento": 999}, cfg=self.cfg()
        )
        self.assertEqual(r.transitions, {"sedimento": SkillState.ARCHIVED})
        self.assertIn("minha", r.protected)

    def test_o_usuario_em_foreground_pode_arquivar_a_propria(self):
        s = Skill("minha", Provenance.USER, state=SkillState.STALE)
        r = apply_automatic_transitions(
            [s], days_unused={"minha": 999}, cfg=self.cfg(), actor=Actor.USER_FOREGROUND
        )
        self.assertEqual(r.transitions, {"minha": SkillState.ARCHIVED})


# ===========================================================================
class ReconciliacaoTests(unittest.TestCase):
    """O sistema NÃO confia na palavra do modelo sobre o que ele fez."""

    def test_declaracao_e_evidencia_concordando(self):
        r = reconcile_classification({"a": "guarda-chuva"}, {"a": "guarda-chuva"})
        self.assertEqual(r.absorbed, {"a": "guarda-chuva"})
        self.assertEqual(r.discrepancies, [])

    def test_quando_divergem_a_EVIDENCIA_prevalece(self):
        """Declaração é intenção; chamada de ferramenta é fato."""
        r = reconcile_classification({"a": "x"}, {"a": "y"})
        self.assertEqual(r.absorbed["a"], "y")
        self.assertTrue(r.discrepancies)

    def test_modelo_declarou_e_nao_fez(self):
        r = reconcile_classification({"a": "x"}, {})
        self.assertEqual(r.removed, set())
        self.assertIn("nenhuma chamada de ferramenta a removeu", r.discrepancies[0])

    def test_ferramenta_removeu_sem_o_modelo_declarar(self):
        r = reconcile_classification({}, {"a": "x"})
        self.assertEqual(r.removed, {"a"})
        self.assertIn("não declarada pelo modelo", r.discrepancies[0])

    def test_a_divergencia_NAO_e_descartada(self):
        """Um modelo que declara sistematicamente o que não faz é sinal de
        problema no prompt; apagar o sintoma esconderia isso."""
        r = reconcile_classification({"a": "x", "b": "y"}, {"c": "z"})
        self.assertEqual(len(r.discrepancies), 3)


if __name__ == "__main__":
    unittest.main()
