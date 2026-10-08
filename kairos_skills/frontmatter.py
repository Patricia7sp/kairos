"""Frontmatter de skill e as regras de formato.

RF-05. `_reversa_sdd/skills/` (Tarefa 09).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "CANONICAL_SECTIONS",
    "SKILL_PROMPT_DESC_LIMIT",
    "Frontmatter",
    "FrontmatterError",
    "parse_frontmatter",
    "validate_frontmatter",
]

#: **A regra é funcional, não estética.** O índice de skills é carregado em
#: TODA sessão e trunca a descrição em 60 caracteres. O que passa disso é
#: cortado em silêncio — e a skill **nunca roteia**, porque o modelo decide
#: carregá-la a partir da descrição truncada. Uma descrição de 80 chars não
#: produz um índice feio: produz uma skill que nunca é usada.
SKILL_PROMPT_DESC_LIMIT = 60

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

#: Ordem canônica, 8 posições.
CANONICAL_SECTIONS: tuple[str, ...] = (
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Quick Reference",
    "Procedure",
    "Pitfalls",
    "Verification",
)


class FrontmatterError(ValueError):
    """Frontmatter inválido."""


@dataclass(frozen=True)
class Frontmatter:
    name: str
    description: str
    version: str = "0.1.0"
    #: **Nunca derivado do ambiente.** Ver `validate_frontmatter`.
    author: str | None = None
    license: str | None = None
    #: Declarado só quando a skill usa primitiva presa ao SO.
    platforms: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


def validate_frontmatter(fm: Frontmatter, *, new_skill: bool = False) -> None:
    """Valida. Levanta `FrontmatterError` com mensagem acionável.

    `new_skill=True` aperta o que só faz sentido cobrar na criação — uma
    skill antiga com `version` fora do semver não deve virar erro de leitura
    a cada sessão.
    """
    if not _NAME.match(fm.name or ""):
        raise FrontmatterError(f"name deve ser kebab-case: {fm.name!r}")
    if len(fm.name) > 64:
        raise FrontmatterError(f"name excede 64 caracteres ({len(fm.name)})")

    desc = (fm.description or "").strip()
    if not desc:
        raise FrontmatterError("description é obrigatória")
    if len(desc) > SKILL_PROMPT_DESC_LIMIT:
        raise FrontmatterError(
            f"description tem {len(desc)} caracteres, acima do limite de "
            f"{SKILL_PROMPT_DESC_LIMIT}. O índice de skills TRUNCA em {SKILL_PROMPT_DESC_LIMIT} "
            f"e a skill nunca rotearia — o limite é funcional, não estético. "
            f"Conte os caracteres depois de escrever: {desc!r}"
        )
    if new_skill and not desc.endswith("."):
        raise FrontmatterError("description deve ser uma frase terminada em ponto")

    if new_skill and not _SEMVER.match(fm.version or ""):
        raise FrontmatterError(f"version deve ser semver: {fm.version!r}")

    # `author` NUNCA é derivado do ambiente — nem de login, nem de git config.
    # Skills são compartilhadas e publicadas, e um nome vindo dali seria "a
    # privacy leak the user never opted into". Ausente é ausente.
    if fm.author is not None:
        # Um catálogo real traz `author` ora como texto, ora como lista de
        # nomes. Sem esta guarda o validador estourava com AttributeError —
        # um traceback em vez da mensagem acionável que ele promete.
        if not isinstance(fm.author, str):
            raise FrontmatterError(
                f"author deve ser texto, veio {type(fm.author).__name__}: {fm.author!r}"
            )
        if not fm.author.strip():
            raise FrontmatterError("author, se presente, não pode ser vazio")


_FM_BLOCK = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str, *, strict_types: bool = False) -> tuple[Frontmatter, str]:
    """Extrai frontmatter YAML e devolve `(frontmatter, corpo)`."""
    import yaml

    m = _FM_BLOCK.match(text)
    if m is None:
        raise FrontmatterError("SKILL.md não começa com bloco de frontmatter '---'")

    try:
        data = _load_strict_yaml(m.group(1)) if strict_types else yaml.safe_load(m.group(1))
        data = data or {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter não é YAML válido: {exc}") from exc
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter deve ser um mapa")

    if strict_types:
        _validate_field_types(data)

    meta = (data.get("metadata") or {}).get("kairos") or {}
    known = {
        "name",
        "description",
        "version",
        "author",
        "license",
        "platforms",
        "metadata",
        "tags",
        "related_skills",
    }

    def lista(chave: str) -> tuple:
        """`metadata.kairos` primeiro, nível de topo depois.

        As duas formas circulam: o aninhamento vem do catálogo, o topo é o que
        se escreve à mão em YAML. Aceitar só uma faz a outra ser silenciosamente
        ignorada — a skill carrega, mas perde as tags e o roteamento por elas.
        """
        valor = meta.get(chave)
        if valor is None:
            valor = data.get(chave)
        return tuple(valor or ())

    fm = Frontmatter(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        version=str(data.get("version", "0.1.0")),
        author=data.get("author"),
        license=data.get("license"),
        platforms=tuple(data.get("platforms") or ()),
        tags=lista("tags"),
        related_skills=lista("related_skills"),
        extra={k: v for k, v in data.items() if k not in known},
    )
    return fm, text[m.end() :]


def _load_strict_yaml(text: str):
    import yaml

    class BoundedLoader(yaml.SafeLoader):
        def __init__(self, stream):
            self.skill_depth = 0
            self.skill_nodes = 0
            super().__init__(stream)

        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise FrontmatterError("Aliases YAML não são aceitos no contexto de skills.")
            if self.skill_depth >= 64 or self.skill_nodes >= 2048:
                raise FrontmatterError("Frontmatter excede o limite de complexidade.")
            self.skill_depth += 1
            self.skill_nodes += 1
            try:
                return super().compose_node(parent, index)
            finally:
                self.skill_depth -= 1

    loader = BoundedLoader(text)
    try:
        return loader.get_single_data()
    except (yaml.YAMLError, ValueError, OverflowError, RecursionError):
        raise FrontmatterError("Frontmatter YAML inválido.") from None
    finally:
        loader.dispose()


def _validate_field_types(data: dict) -> None:
    for key in ("name", "description", "version", "author", "license"):
        if key not in data or (key in {"author", "license"} and data[key] is None):
            continue
        if not isinstance(data[key], str):
            raise FrontmatterError(f"{key} deve ser texto")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise FrontmatterError("metadata deve ser um mapa")
    namespace = metadata.get("kairos", {})
    if not isinstance(namespace, dict):
        raise FrontmatterError("metadata.kairos deve ser um mapa")
    for mapping in (data, namespace):
        for key in ("platforms", "tags", "related_skills"):
            if key in mapping and (
                not isinstance(mapping[key], list)
                or any(not isinstance(value, str) for value in mapping[key])
            ):
                raise FrontmatterError(f"{key} deve ser uma lista de textos")
