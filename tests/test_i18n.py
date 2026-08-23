"""Tarefa 06 — i18n.

Critério de pronto: "A cascata de três degraus nunca lança — o último degrau
devolve a própria chave, *so a broken catalog never crashes the agent*; o gate
de paridade reprova catálogo divergente."

Os dois gates são **herdados**, não inventados: o legado já os tinha em
`tests/agent/test_i18n.py`, e a lacuna G-30 que dizia o contrário foi retirada
(ver a errata de `gaps.md`).
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

import yaml

import kairos_i18n as i18n
from kairos_i18n import (
    BASELINE,
    LANGUAGE_ENV,
    SUPPORTED_LANGUAGES,
    flatten,
    get_language,
    load_catalog,
    locales_dir,
    reset_language_cache,
    t,
)
from kairos_i18n.coverage import (
    COVERAGE,
    Surface,
    SurfaceCoverage,
    coverage_for,
    covers,
    falls_back_to_english,
)

SHIPPING = sorted(coverage_for(Surface.BACKEND).locales)
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def raw(lang: str) -> dict[str, str]:
    with open(locales_dir() / f"{lang}.yaml", encoding="utf-8") as fh:
        return flatten(yaml.safe_load(fh) or {})


class EnvBase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (LANGUAGE_ENV, "KAIROS_HOME")}
        os.environ.pop(LANGUAGE_ENV, None)
        # Isola do config.yaml do usuário real.
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["KAIROS_HOME"] = self._tmp.name
        reset_language_cache()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()
        reset_language_cache()


# ---------------------------------------------------------------------------
class ParityGateTests(unittest.TestCase):
    """T-08 — os DOIS gates herdados."""

    def test_gate_de_chaves_nos_dois_sentidos(self):
        """Reprova a que falta E a que sobra.

        Só o primeiro sentido deixaria passar chave órfã de uma remoção
        incompleta no inglês — que nunca aparece na tela e ninguém percebe.
        """
        en = set(raw(BASELINE))
        self.assertTrue(en, "en.yaml não pode estar vazio")
        for lang in SHIPPING:
            if lang == BASELINE:
                continue
            with self.subTest(lang=lang):
                other = set(raw(lang))
                self.assertEqual(en - other, set(), f"{lang}.yaml: chaves faltando")
                self.assertEqual(other - en, set(), f"{lang}.yaml: chaves que não existem no inglês")

    def test_gate_de_placeholders(self):
        """O gate mais valioso, e o que quase se perdeu.

        Pega o caso em que a chave existe e o texto está traduzido, e mesmo
        assim o valor interpolado some ou levanta `KeyError` — um
        `{description}` digitado como `{descricao}`.
        """
        en = raw(BASELINE)
        for lang in SHIPPING:
            flat = raw(lang)
            for key, en_value in en.items():
                with self.subTest(lang=lang, key=key):
                    self.assertEqual(
                        set(PLACEHOLDER.findall(flat.get(key, ""))),
                        set(PLACEHOLDER.findall(en_value)),
                        f"{lang}.yaml chave={key!r}: placeholders divergem do inglês",
                    )

    def test_o_gate_de_chaves_REPROVA_um_catalogo_divergente(self):
        """Um gate que nunca reprova não é gate."""
        en = {"a.b": "x", "a.c": "y"}
        divergente = {"a.b": "x", "a.z": "sobrando"}
        self.assertNotEqual(set(en) - set(divergente), set())
        self.assertNotEqual(set(divergente) - set(en), set())

    def test_o_gate_de_placeholders_REPROVA_token_trocado(self):
        self.assertNotEqual(
            set(PLACEHOLDER.findall("olá {description}")),
            set(PLACEHOLDER.findall("olá {descricao}")),
        )

    def test_todo_catalogo_declarado_existe_em_disco(self):
        for lang in SHIPPING:
            with self.subTest(lang=lang):
                self.assertTrue((locales_dir() / f"{lang}.yaml").is_file())


# ---------------------------------------------------------------------------
class CascadeTests(EnvBase):
    """RF-04, RF-05 — a invariante da unit."""

    def test_rf05_chave_inexistente_devolve_a_PROPRIA_chave(self):
        # "so a broken catalog never crashes the agent"
        self.assertEqual(t("nao.existe.em.lugar.nenhum"), "nao.existe.em.lugar.nenhum")

    def test_rf04_chave_ausente_no_idioma_cai_para_o_ingles(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "en.yaml").write_text("a:\n  b: 'inglês'\n", encoding="utf-8")
            Path(d, "pt.yaml").write_text("outra: 'coisa'\n", encoding="utf-8")
            os.environ["KAIROS_LOCALES_DIR"] = d
            reset_language_cache()
            try:
                self.assertEqual(t("a.b", lang="pt"), "inglês")
            finally:
                del os.environ["KAIROS_LOCALES_DIR"]
                reset_language_cache()

    def test_catalogo_com_yaml_invalido_nao_levanta(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "en.yaml").write_text("isto: [nao\n  fecha", encoding="utf-8")
            os.environ["KAIROS_LOCALES_DIR"] = d
            reset_language_cache()
            try:
                self.assertEqual(load_catalog("en"), {})
                self.assertEqual(t("qualquer.chave"), "qualquer.chave")
            finally:
                del os.environ["KAIROS_LOCALES_DIR"]
                reset_language_cache()

    def test_catalogo_ausente_nao_levanta(self):
        self.assertEqual(load_catalog("idioma-que-nao-existe"), {})

    def test_catalogo_que_nao_e_mapa_nao_levanta(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "en.yaml").write_text("- isto\n- e uma lista\n", encoding="utf-8")
            os.environ["KAIROS_LOCALES_DIR"] = d
            reset_language_cache()
            try:
                self.assertEqual(load_catalog("en"), {})
            finally:
                del os.environ["KAIROS_LOCALES_DIR"]
                reset_language_cache()


# ---------------------------------------------------------------------------
class ResolutionTests(EnvBase):
    """RF-02, RF-03, RF-06."""

    def test_rf03_override_por_chamada_vence_tudo(self):
        os.environ[LANGUAGE_ENV] = "es"
        reset_language_cache()
        self.assertEqual(t("approval.denied", lang="pt"),
                         "Negado. O comando não foi executado.")

    def test_rf02_env_vence_o_baseline(self):
        os.environ[LANGUAGE_ENV] = "pt"
        reset_language_cache()
        self.assertEqual(get_language(), "pt")

    def test_env_vence_o_config(self):
        # A env é o override rápido: teste e execução pontual sem editar arquivo.
        Path(self._tmp.name, "config.yaml").write_text(
            "display:\n  language: es\n", encoding="utf-8")
        reset_language_cache()
        self.assertEqual(get_language(), "es")
        os.environ[LANGUAGE_ENV] = "pt"
        reset_language_cache()
        self.assertEqual(get_language(), "pt")

    def test_config_vence_o_baseline(self):
        Path(self._tmp.name, "config.yaml").write_text(
            "display:\n  language: pt\n", encoding="utf-8")
        reset_language_cache()
        self.assertEqual(get_language(), "pt")

    def test_baseline_quando_nada_esta_definido(self):
        self.assertEqual(get_language(), BASELINE)

    def test_rf06_idioma_desconhecido_cai_para_o_baseline(self):
        os.environ[LANGUAGE_ENV] = "xx"
        reset_language_cache()
        self.assertEqual(get_language(), BASELINE)

    def test_sufixo_regional_cai_para_a_base(self):
        # pt-BR → pt é mais útil que pt-BR → en.
        os.environ[LANGUAGE_ENV] = "pt-BR"
        reset_language_cache()
        self.assertEqual(get_language(), "pt")

    def test_config_quebrado_nao_levanta(self):
        Path(self._tmp.name, "config.yaml").write_text("[[[nao é yaml", encoding="utf-8")
        reset_language_cache()
        self.assertEqual(get_language(), BASELINE)


# ---------------------------------------------------------------------------
class FormatTests(EnvBase):
    """RF-07, RF-08."""

    def test_rf07_achatamento_em_chaves_pontilhadas(self):
        self.assertEqual(
            flatten({"approval": {"choose": "x", "n": {"deep": "y"}}, "top": "z"}),
            {"approval.choose": "x", "approval.n.deep": "y", "top": "z"},
        )

    def test_achatamento_ignora_valor_nulo(self):
        self.assertEqual(flatten({"a": None, "b": "x"}), {"b": "x"})

    def test_rf08_substituicao_de_parametros(self):
        self.assertEqual(
            t("gateway.drain_started", lang="en", count=3),
            "Draining: waiting for 3 turn(s) to finish.",
        )

    def test_parametro_faltante_devolve_o_texto_cru_sem_levantar(self):
        out = t("gateway.drain_started", lang="en")
        self.assertIn("{count}", out)

    def test_parametro_extra_e_ignorado(self):
        self.assertEqual(t("approval.denied", lang="en", irrelevante=1),
                         "Denied. The command was not run.")


# ---------------------------------------------------------------------------
class CacheTests(EnvBase):
    """RF-09, RF-10."""

    def test_rf09_catalogo_e_cacheado(self):
        load_catalog.cache_clear()
        load_catalog("en")
        antes = load_catalog.cache_info().hits
        load_catalog("en")
        self.assertEqual(load_catalog.cache_info().hits, antes + 1)

    def test_rf10_reset_forca_releitura(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d, "en.yaml")
            arq.write_text("k: 'antes'\n", encoding="utf-8")
            os.environ["KAIROS_LOCALES_DIR"] = d
            reset_language_cache()
            try:
                self.assertEqual(t("k"), "antes")
                arq.write_text("k: 'depois'\n", encoding="utf-8")
                self.assertEqual(t("k"), "antes", "ainda cacheado")
                reset_language_cache()
                self.assertEqual(t("k"), "depois")
            finally:
                del os.environ["KAIROS_LOCALES_DIR"]
                reset_language_cache()


# ---------------------------------------------------------------------------
class CoverageTests(unittest.TestCase):
    """T-09, T-11 — a decisão de G-32."""

    def test_as_tres_superficies_estao_declaradas(self):
        self.assertEqual(set(COVERAGE), {Surface.BACKEND, Surface.SPA, Surface.DESKTOP})

    def test_toda_superficie_declara_uma_justificativa(self):
        for name, cov in COVERAGE.items():
            with self.subTest(surface=name):
                self.assertTrue(cov.rationale.strip(), f"{name} sem justificativa")

    def test_a_divergencia_do_desktop_e_INTENCIONAL_e_registrada(self):
        desktop = coverage_for(Surface.DESKTOP)
        backend = coverage_for(Surface.BACKEND)
        self.assertLess(len(desktop.target_locales), len(backend.target_locales))
        self.assertIn("INTENCIONAL", desktop.rationale)

    def test_o_gate_valida_DENTRO_da_superficie_nao_entre_elas(self):
        # Divergência entre superfícies é decisão registrada, não defeito.
        b = coverage_for(Surface.BACKEND).target_locales
        d = coverage_for(Surface.DESKTOP).target_locales
        self.assertNotEqual(b, d)   # e isso não reprova nada

    def test_entrega_nunca_excede_o_alvo(self):
        for name, cov in COVERAGE.items():
            with self.subTest(surface=name):
                self.assertLessEqual(cov.locales, cov.target_locales)

    def test_superficie_declarando_idioma_fora_do_alvo_e_recusada(self):
        with self.assertRaises(ValueError):
            SurfaceCoverage(surface="x", locales=frozenset({"pt"}),
                            target_locales=frozenset({"en"}), rationale="—")

    def test_t11_aviso_olha_o_que_ENTREGA_nao_o_que_pretende(self):
        """A distinção que importa para não mentir ao usuário.

        O desktop *pretende* cobrir japonês (está no alvo), mas ainda não
        entrega nada (Tarefa 19). O aviso precisa refletir a entrega: dizer
        que cobre japonês, com respaldo de um arquivo de declaração, seria
        mentir com aparência de rigor.
        """
        from kairos_i18n.coverage import intends
        self.assertTrue(intends(Surface.DESKTOP, "ja"))       # pretende
        self.assertFalse(covers(Surface.DESKTOP, "ja"))       # não entrega
        self.assertTrue(falls_back_to_english(Surface.DESKTOP, "ja"))

        # O backend entrega pt hoje: nada de aviso.
        self.assertTrue(covers(Surface.BACKEND, "pt"))
        self.assertFalse(falls_back_to_english(Surface.BACKEND, "pt"))

        # E o desktop nunca pretendeu cobrir pt: aviso permanente, não dívida.
        self.assertFalse(intends(Surface.DESKTOP, "pt"))
        self.assertTrue(falls_back_to_english(Surface.DESKTOP, "pt"))

        # Inglês nunca "cai para inglês".
        self.assertFalse(falls_back_to_english(Surface.DESKTOP, "en"))

    def test_a_mensagem_de_aviso_existe_no_catalogo(self):
        msg = t("i18n.falls_back_to_english", lang="en",
                surface="desktop", language="Portuguese")
        self.assertNotEqual(msg, "i18n.falls_back_to_english")
        self.assertIn("desktop", msg)

    def test_superficie_desconhecida_levanta_com_mensagem_util(self):
        with self.assertRaises(KeyError) as ctx:
            coverage_for("inexistente")
        self.assertIn("backend", str(ctx.exception))

    def test_a_divida_de_traducao_fica_VISIVEL(self):
        # O alvo herdado do legado é 17; o Kairos entrega 3. A diferença é
        # trabalho de tradução (conteúdo), não de mecanismo — e não some.
        backend = coverage_for(Surface.BACKEND)
        self.assertEqual(len(backend.target_locales), 17)
        self.assertEqual(backend.locales, {"en", "pt", "es"})
        self.assertEqual(len(backend.pending), 14)


# ---------------------------------------------------------------------------
class ScopeTests(unittest.TestCase):
    """RF-11 — o escopo é fino por decisão."""

    def test_o_catalogo_so_tem_mensagens_estaticas_do_sistema(self):
        grupos = {k.split(".")[0] for k in raw(BASELINE)}
        self.assertEqual(grupos, {"approval", "gateway", "session", "i18n"})

    def test_nao_ha_chave_para_saida_de_agente_log_ou_traceback(self):
        proibidos = ("agent.", "log.", "traceback.", "tool.", "slash_description.")
        for key in raw(BASELINE):
            for p in proibidos:
                self.assertFalse(key.startswith(p), f"{key} está fora do escopo declarado")


if __name__ == "__main__":
    unittest.main()
