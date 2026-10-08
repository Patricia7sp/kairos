"""Catálogo durável e capacidade de leitura limitada a um turno."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

from kairos_integration.interaction_contract import InteractionEnvelope
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_providers._async_cleanup import run_persistent_cleanup
from kairos_skills.catalog import (
    SkillAssetRecord,
    SkillCatalogError,
    SkillCatalogSnapshot,
    catalog_asset,
    catalog_home_id,
)
from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
from kairos_skills.catalog_view import (
    SkillViewPage,
    encode_skill_view_page,
    skill_view_page,
    validate_page_range,
)
from kairos_state.repositories.skill_catalogs import SkillCatalogRepository


class SkillCatalogTurn:
    def __init__(
        self,
        home: Path,
        conversation_id: str,
        snapshot: SkillCatalogSnapshot,
        *,
        load_asset: Callable[[SkillAssetRecord], Awaitable[str]],
    ) -> None:
        self.home = Path(home)
        self.conversation_id = conversation_id
        self.snapshot = snapshot
        self._load_asset = load_asset
        self._remaining = 128 * 1024
        self._closed = False
        self._lock = threading.Lock()

    @property
    def remaining_bytes(self) -> int:
        with self._lock:
            return self._remaining

    def close(self) -> None:
        with self._lock:
            self._closed = True

    async def view(
        self, name: str, reference: str | None = None, offset: int = 0, limit: int = 4000
    ) -> SkillViewPage:
        asset = catalog_asset(self.snapshot, name, reference)
        validate_page_range(asset, offset, limit)
        reserved = min(limit, asset.char_count - offset) * 4
        with self._lock:
            if self._closed:
                raise SkillCatalogError("Capacidade de leitura encerrada.")
            if reserved > self._remaining:
                raise SkillCatalogError(
                    "Reserva de orçamento insuficiente para esta página; reduza limit ou inicie outro turno."
                )
            self._remaining -= reserved
        try:
            text = await self._load_asset(asset)
            page = skill_view_page(asset, text, offset=offset, limit=limit)
            encode_skill_view_page(page)
            with self._lock:
                if self._closed:
                    raise SkillCatalogError("Capacidade de leitura encerrada.")
                self._remaining += reserved - len(page.text.encode())
            return page
        except BaseException:
            with self._lock:
                self._remaining += reserved
            raise


async def _local_io(function, *args):
    operation = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError as cancellation:
        outcome = await run_persistent_cleanup(
            lambda: asyncio.shield(operation), task_name="kairos-skill-catalog-io"
        )
        if outcome.error is not None:
            raise outcome.error from cancellation
        raise cancellation


async def prepare_skill_catalog_turn(
    envelope: InteractionEnvelope,
    *,
    home: Path,
    repository: SkillCatalogRepository | None,
    persistence: SQLiteAsyncInteractionPersistence | None,
) -> SkillCatalogTurn | None:
    if not envelope.skills_catalog:
        return None
    if (repository is None) == (persistence is None):
        raise SkillCatalogError("Catálogo exige uma fronteira de persistência por sessão.")
    home_id = catalog_home_id(home)
    if persistence is not None:
        snapshot = await persistence.get_skill_catalog(envelope.conversation_id, home_id=home_id)
        if snapshot is None:
            captured = await persistence._call(capture_skill_catalog, home)
            snapshot = await persistence.create_skill_catalog_if_absent(
                envelope.conversation_id, captured
            )
    else:
        assert repository is not None
        snapshot = repository.get(envelope.conversation_id, home_id=home_id)
        if snapshot is None:
            captured = await _local_io(capture_skill_catalog, home)
            snapshot = repository.create_if_absent(envelope.conversation_id, captured)

    cached: dict[SkillAssetRecord, str] = {}
    load_lock = asyncio.Lock()

    async def load_asset(asset: SkillAssetRecord) -> str:
        async with load_lock:
            if asset in cached:
                return cached[asset]
            if persistence is not None:
                text = await persistence.get_skill_asset(envelope.conversation_id, asset)
                if text is None:
                    text = await persistence._call(read_catalog_asset, home, asset)
                    text = await persistence.put_skill_asset(envelope.conversation_id, asset, text)
            else:
                assert repository is not None
                text = repository.get_asset(envelope.conversation_id, asset)
                if text is None:
                    text = await _local_io(read_catalog_asset, home, asset)
                    text = repository.put_asset(envelope.conversation_id, asset, text)
            cached[asset] = text
            return text

    return SkillCatalogTurn(home, envelope.conversation_id, snapshot, load_asset=load_asset)
