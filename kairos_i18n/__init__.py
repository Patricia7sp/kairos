"""Internacionalização — deliberadamente **fina**.

Reconstruído de `_reversa_sdd/i18n/` (Tarefa 06).

**Escopo, por decisão.** Só as mensagens estáticas de maior impacto que o
próprio sistema mostra: prompts de aprovação, algumas respostas de slash
command e avisos de restart/drain. Ficam **em inglês**: saída gerada pelo
agente, linhas de log, tracebacks, saídas de ferramenta e descrições de slash
command.

A razão de o escopo ser fino é que traduzir saída do agente exigiria traduzir
o que o modelo escreve — o que não é catálogo, é outra chamada de LLM. E
traduzir traceback tornaria impossível colar um erro numa busca.

**A invariante central**: um catálogo quebrado nunca derruba o agente. A
resolução degrada em cascata até devolver a própria chave.
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path

import yaml

from kairos_i18n.coverage import (
    COVERAGE,
    Surface,
    coverage_for,
    covers,
    falls_back_to_english,
)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "BASELINE",
    "LANGUAGE_ENV",
    "t",
    "get_language",
    "reset_language_cache",
    "flatten",
    "load_catalog",
    "locales_dir",
    "Surface",
    "COVERAGE",
    "coverage_for",
    "covers",
    "falls_back_to_english",
]

BASELINE = "en"
LANGUAGE_ENV = "KAIROS_LANGUAGE"

#: Os 17 idiomas do backend. Valor desconhecido cai para o baseline.
SUPPORTED_LANGUAGES: frozenset[str] = coverage_for(Surface.BACKEND).locales

#: Protege a invalidação do cache: `reset_language_cache` pode ser chamado
#: enquanto outra thread resolve uma chave, e limpar o `lru_cache` no meio
#: de uma leitura deixaria a thread com um catálogo pela metade.
_CACHE_LOCK = threading.Lock()

_CONFIG_LANGUAGE: str | None = None
_CONFIG_LANGUAGE_SET = False


def locales_dir() -> Path:
    """Onde vivem os catálogos. Sobrescrevível para teste."""
    override = os.environ.get("KAIROS_LOCALES_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "locales"


def flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    """Achata o YAML aninhado em chaves pontilhadas.

    O aninhamento no arquivo é **puramente para leitura humana** — a chave
    real é sempre a pontilhada. Um catálogo de 400 chaves planas seria
    ilegível; um de 400 chaves aninhadas em 6 grupos, não.
    """
    out: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{path}."))
        elif value is not None:
            out[path] = str(value)
    return out


@lru_cache(maxsize=32)
def load_catalog(lang: str) -> dict[str, str]:
    """Carrega e achata um catálogo. **Nunca levanta.**

    YAML inválido, arquivo ausente ou ilegível resultam em catálogo vazio —
    que a cascata cobre. Levantar aqui derrubaria o agente por causa de um
    arquivo de texto.
    """
    path = locales_dir() / f"{lang}.yaml"
    try:
        with open(path, encoding="utf-8") as fh:
            tree = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(tree, dict):
        return {}
    return flatten(tree)


def _config_language() -> str | None:
    """`display.language` do config. Cacheado, e tolerante a ausência."""
    global _CONFIG_LANGUAGE, _CONFIG_LANGUAGE_SET
    if _CONFIG_LANGUAGE_SET:
        return _CONFIG_LANGUAGE

    value: str | None = None
    home = os.environ.get("KAIROS_HOME")
    root = Path(home).expanduser() if home else Path.home() / ".kairos"
    try:
        with open(root / "config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        display = cfg.get("display") if isinstance(cfg, dict) else None
        if isinstance(display, dict):
            raw = display.get("language")
            if isinstance(raw, str) and raw.strip():
                value = raw.strip()
    except (OSError, yaml.YAMLError, AttributeError):
        value = None

    _CONFIG_LANGUAGE = value
    _CONFIG_LANGUAGE_SET = True
    return value


def _normalize(lang: str | None) -> str:
    """Idioma desconhecido cai para o baseline, sem erro.

    Um valor de config errado não deve tornar o sistema inutilizável — deve
    tornar as mensagens inglesas.
    """
    if not lang:
        return BASELINE
    candidate = lang.strip()
    if candidate in SUPPORTED_LANGUAGES:
        return candidate
    # `pt-BR` cai para `pt` quando a base existe: é mais útil que cair no
    # inglês por causa de um sufixo regional.
    base = candidate.split("-")[0]
    if base in SUPPORTED_LANGUAGES:
        return base
    return BASELINE


def get_language() -> str:
    """Ordem de precedência: env → config → baseline.

    O nível de override por chamada (`lang=`) é resolvido dentro de `t()`,
    porque é por invocação e não por processo.

    A variável de ambiente vem **antes** do config de propósito: é o
    mecanismo de override rápido, para teste e para uma execução pontual num
    idioma diferente sem editar arquivo.
    """
    return _normalize(os.environ.get(LANGUAGE_ENV) or _config_language())


def t(key: str, *, lang: str | None = None, **params) -> str:
    """Traduz uma chave pontilhada.

    **A cascata de três degraus é a invariante da unit:**

    1. chave no idioma ativo
    2. chave em inglês
    3. **a própria chave**, devolvida como texto

    O terceiro degrau é o que garante que um catálogo quebrado nunca derrube
    o agente. Uma chave pontilhada aparecendo na tela é feia e diagnóstica —
    e infinitamente melhor que um `KeyError` no meio de um prompt de
    aprovação, que é justamente o momento em que o usuário mais precisa que a
    interface funcione.

    A substituição também degrada: parâmetro faltante devolve o texto cru em
    vez de levantar.
    """
    active = _normalize(lang) if lang else get_language()

    text = load_catalog(active).get(key)
    if text is None and active != BASELINE:
        text = load_catalog(BASELINE).get(key)
    if text is None:
        return key

    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        # Placeholder que o catálogo tem e o chamador não passou (ou vice-
        # versa). O gate de placeholders pega isso em CI; em runtime, mostrar
        # o texto cru é melhor que quebrar.
        return text


def reset_language_cache() -> None:
    """Invalida catálogo e idioma de config."""
    global _CONFIG_LANGUAGE, _CONFIG_LANGUAGE_SET
    with _CACHE_LOCK:
        load_catalog.cache_clear()
        _CONFIG_LANGUAGE = None
        _CONFIG_LANGUAGE_SET = False
