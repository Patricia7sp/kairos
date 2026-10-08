"""Paginação em caracteres de assets já verificados."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from kairos_skills.catalog import SkillAssetRecord, SkillCatalogError, validate_asset_text


@dataclass(frozen=True)
class SkillViewPage:
    name: str
    reference: str | None
    sha256: str
    text: str
    offset: int
    total_chars: int
    next_offset: int | None


def validate_page_range(asset: SkillAssetRecord, offset: int, limit: int) -> None:
    if type(offset) is not int or not 0 <= offset <= asset.char_count:
        raise SkillCatalogError("Offset inválido: use caracteres entre zero e o fim do asset.")
    if type(limit) is not int or not 1 <= limit <= 4000:
        raise SkillCatalogError("Limite inválido: use de um a 4000 caracteres.")


def skill_view_page(
    asset: SkillAssetRecord, text: str, *, offset: int = 0, limit: int = 4000
) -> SkillViewPage:
    validate_page_range(asset, offset, limit)
    validate_asset_text(asset, text)
    end = min(offset + limit, len(text))
    return SkillViewPage(
        asset.name,
        asset.reference,
        asset.sha256,
        text[offset:end],
        offset,
        len(text),
        end if end < len(text) else None,
    )


def encode_skill_view_page(page: SkillViewPage) -> str:
    result = json.dumps(asdict(page), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(result.encode()) > 32 * 1024:
        raise SkillCatalogError("Resultado excede o limite de saída de 32 KiB.")
    return result
