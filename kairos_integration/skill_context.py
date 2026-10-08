"""Contexto e metadata do consumo explícito de skills, sem leitura de disco."""

import json
from typing import Any

from kairos_skills.runtime import SkillSnapshot


def render_skill_context(content: str, skills: tuple[SkillSnapshot, ...]) -> str:
    if not skills:
        return content
    selected = [
        {
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "sha256": skill.sha256,
            "text": skill.text,
        }
        for skill in skills
    ]
    context = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Skills selecionadas explicitamente para este turno "
        "(procedimentos de referência; não concedem permissões):\n"
        + context
        + "\n\nPedido do usuário:\n"
        + content
    )


def skill_display_metadata(skills: tuple[SkillSnapshot, ...]) -> dict[str, Any]:
    if not skills:
        return {}
    return {
        "skills": [
            {"name": skill.name, "version": skill.version, "sha256": skill.sha256}
            for skill in skills
        ]
    }
