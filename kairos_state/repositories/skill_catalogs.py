"""Snapshots e assets imutáveis, com validação em cada fronteira do banco."""

from __future__ import annotations

import sqlite3
from typing import Any

from kairos_skills.catalog import (
    CATALOG_PREAMBLE,
    MAX_INDEX_BYTES,
    MAX_MANIFEST_BYTES,
    SkillAssetRecord,
    SkillCatalogError,
    SkillCatalogSnapshot,
    catalog_asset,
    decode_skill_catalog,
    validate_asset_text,
)
from kairos_state.contention import Budget
from kairos_state.writes import write_with_retry


class SkillCatalogRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def _snapshot(
        self, session_id: str, *, home_id: str | None = None
    ) -> SkillCatalogSnapshot | None:
        sizes = self._conn.execute(
            "SELECT length(CAST(manifest_json AS BLOB)), length(CAST(system_text AS BLOB)) "
            "FROM skill_catalogs WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if sizes is None:
            if self._conn.execute(
                "SELECT 1 FROM skill_catalog_assets WHERE session_id=? LIMIT 1", (session_id,)
            ).fetchone():
                raise SkillCatalogError("Cache de skills sem catálogo; inicie uma nova sessão.")
            return None
        if (
            sizes[0] > MAX_MANIFEST_BYTES
            or sizes[1] > MAX_INDEX_BYTES + len(CATALOG_PREAMBLE.encode()) + 1
        ):
            raise SkillCatalogError("Catálogo persistido acima do limite.")
        row = self._conn.execute(
            "SELECT home_id,digest,manifest_json,system_text FROM skill_catalogs WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return decode_skill_catalog(
            row[2], row[3], row[1], home_id=row[0] if home_id is None else home_id
        )

    def _assets(self, session_id: str, snapshot: SkillCatalogSnapshot) -> list[dict[str, Any]]:
        inventory = {(a.name, a.reference or ""): a for e in snapshot.entries for a in e.assets}
        sizes = self._conn.execute(
            "SELECT name,reference,sha256,length(CAST(text AS BLOB)) "
            "FROM skill_catalog_assets WHERE session_id=? ORDER BY name,reference",
            (session_id,),
        )
        keys = []
        for row in sizes:
            asset = inventory.get((row[0], row[1]))
            if asset is None or row[2] != asset.sha256 or row[3] != asset.size_bytes:
                raise SkillCatalogError("Cache de asset inválido; inicie uma nova sessão.")
            keys.append(asset)
            if len(keys) > len(inventory):
                raise SkillCatalogError("Cache de assets acima do limite.")
        loaded = []
        for asset in keys:
            text = self._conn.execute(
                "SELECT text FROM skill_catalog_assets WHERE session_id=? AND name=? AND reference=?",
                (session_id, asset.name, asset.reference or ""),
            ).fetchone()[0]
            validate_asset_text(asset, text)
            loaded.append(
                {
                    "name": asset.name,
                    "reference": asset.reference,
                    "sha256": asset.sha256,
                    "text": text,
                }
            )
        return loaded

    def get(self, session_id: str, *, home_id: str) -> SkillCatalogSnapshot | None:
        snapshot = self._snapshot(session_id, home_id=home_id)
        if snapshot is not None:
            self._assets(session_id, snapshot)
        return snapshot

    def create_if_absent(
        self, session_id: str, snapshot: SkillCatalogSnapshot
    ) -> SkillCatalogSnapshot:
        if type(snapshot) is not SkillCatalogSnapshot:
            raise SkillCatalogError("Snapshot de catálogo inválido.")

        def operation():
            with self._conn:
                self._conn.execute(
                    "INSERT INTO skill_catalogs(session_id,home_id,digest,manifest_json,system_text) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO NOTHING",
                    (
                        session_id,
                        snapshot.home_id,
                        snapshot.digest,
                        snapshot.manifest_json,
                        snapshot.system_text,
                    ),
                )
                winner = self.get(session_id, home_id=snapshot.home_id)
                if winner is None:
                    raise SkillCatalogError("Catálogo não foi persistido.")
                return winner

        return write_with_retry(operation, budget=Budget.TRANSCRIPT, detail="skill_catalog_create")

    def _member(self, session_id: str, asset: SkillAssetRecord) -> None:
        snapshot = self._snapshot(session_id)
        if snapshot is None or catalog_asset(snapshot, asset.name, asset.reference) != asset:
            raise SkillCatalogError("Asset não pertence ao catálogo da sessão.")

    def get_asset(self, session_id: str, asset: SkillAssetRecord) -> str | None:
        self._member(session_id, asset)
        size = self._conn.execute(
            "SELECT sha256,length(CAST(text AS BLOB)) FROM skill_catalog_assets "
            "WHERE session_id=? AND name=? AND reference=?",
            (session_id, asset.name, asset.reference or ""),
        ).fetchone()
        if size is None:
            return None
        if size[0] != asset.sha256 or size[1] != asset.size_bytes:
            raise SkillCatalogError("Cache de asset inválido; inicie uma nova sessão.")
        text = self._conn.execute(
            "SELECT text FROM skill_catalog_assets WHERE session_id=? AND name=? AND reference=?",
            (session_id, asset.name, asset.reference or ""),
        ).fetchone()[0]
        validate_asset_text(asset, text)
        return text

    def put_asset(self, session_id: str, asset: SkillAssetRecord, text: str) -> str:
        validate_asset_text(asset, text)

        def operation():
            with self._conn:
                self._member(session_id, asset)
                self._conn.execute(
                    "INSERT INTO skill_catalog_assets(session_id,name,reference,sha256,text) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(session_id,name,reference) DO NOTHING",
                    (session_id, asset.name, asset.reference or "", asset.sha256, text),
                )
                stored = self.get_asset(session_id, asset)
                if stored is None:
                    raise SkillCatalogError("Asset não foi persistido.")
                return stored

        return write_with_retry(operation, budget=Budget.TRANSCRIPT, detail="skill_catalog_asset")

    def export_session(self, session_id: str) -> dict[str, Any] | None:
        snapshot = self._snapshot(session_id)
        if snapshot is None:
            return None
        return {
            "home_id": snapshot.home_id,
            "digest": snapshot.digest,
            "manifest_json": snapshot.manifest_json,
            "system_text": snapshot.system_text,
            "assets": self._assets(session_id, snapshot),
        }
