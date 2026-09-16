"""Arquitetura de aprendizado operacional baseado em experiência.

Armazena lições extraídas de turnos reais (erros corrigidos, configurações
que funcionam, armadilhas evitadas) e as oferece como contexto opcional a
turnos futuros — **sem reconstruir o system prompt** e sem violar a Lei 1.

Ciclo de 9 passos (proposto):

1. Executar — turno acontece
2. Detectar — falha/correção observada
3. Capturar — experiência candidata criada (nunca automática)
4. Revisar — humano/CLI confirma → status=CANDIDATA; confirma → ATIVA
5. Normalizar — trigger, observação, correção, escopo preenchidos
6. Persistir — JSON atômico com versão e audit trail
7. Injetar — build_experience_context() prependida ao turno atual (opt-in)
8. Avaliar — resultado do turno vira feedback (hits/successes)
9. Ajustar — confiança sobe/desce; baixa demais → INVALIDA

Segurança: experiências são sugestões, não alteram regras. Nunca são
auto-aprovadas. Rejeição mantém o registro (INVALIDA) para auditoria.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from kairos_security.credentials.io import secure_atomic_write_text

EXPERIENCES_FILE = "experiences.json"
EXPERIENCE_BLOCK_HEADER = "Experiencias passadas (use como referencia; nao altere regras):"

_MAX_ENTRIES = 300
_MAX_HISTORY = 20


class ExperienceStatus(StrEnum):
    ATIVA = "ativa"
    CANDIDATA = "candidata"
    INVALIDA = "invalida"


@dataclass(frozen=True)
class Experience:
    id: str
    status: ExperienceStatus
    trigger: str
    observation: str
    correction: str
    scope: str
    source: str
    confidence: float
    hits: int
    successes: int
    created_at: str
    updated_at: str
    history: tuple[dict[str, Any], ...] = ()
    version: int = 1


class ExperienceStore:
    def __init__(
        self,
        home: Path,
        *,
        max_entries: int = _MAX_ENTRIES,
        max_history: int = _MAX_HISTORY,
    ) -> None:
        self.home = Path(home)
        self.max_entries = max_entries
        self.max_history = max_history
        self._records: list[dict[str, Any]] | None = None

    @property
    def _path(self) -> Path:
        return self.home / EXPERIENCES_FILE

    def _load_raw(self) -> list[dict[str, Any]]:
        if self._records is not None:
            return self._records
        try:
            self._records = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            self._records = []
        return self._records  # type: ignore[return-value]

    def _save(self) -> None:
        if self._records is None:
            return
        self.home.mkdir(parents=True, exist_ok=True)
        secure_atomic_write_text(
            self._path,
            json.dumps(self._records, ensure_ascii=False, indent=2, default=str),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _words(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", text.lower()))

    def _evict_if_needed(self) -> None:
        records = self._records or []
        if len(records) <= self.max_entries:
            return
        invalidas = [r for r in records if r["status"] == ExperienceStatus.INVALIDA]
        candidatas = [r for r in records if r["status"] == ExperienceStatus.CANDIDATA]
        ativas = [r for r in records if r["status"] == ExperienceStatus.ATIVA]
        evict_order = invalidas + candidatas + ativas
        evict_order.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        to_remove = len(records) - self.max_entries
        remove_ids = {r["id"] for r in evict_order[:to_remove]}
        self._records = [r for r in records if r["id"] not in remove_ids]

    def add(
        self,
        trigger: str,
        observation: str,
        correction: str,
        *,
        source: str = "usuario",
        scope: str = "global",
        confidence: float = 0.6,
        status: ExperienceStatus = ExperienceStatus.CANDIDATA,
    ) -> Experience:
        now = self._now()
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "status": status,
            "trigger": trigger.strip(),
            "observation": observation.strip(),
            "correction": correction.strip(),
            "scope": scope.strip(),
            "source": source.strip(),
            "confidence": max(0.05, min(0.99, float(confidence))),
            "hits": 0,
            "successes": 0,
            "created_at": now,
            "updated_at": now,
            "history": [],
            "version": 1,
        }
        self._load_raw()
        self._records.append(record)  # type: ignore[union-attr]
        self._evict_if_needed()
        self._save()
        return self._from_record(record)

    def get(self, exp_id: str) -> Experience | None:
        self._load_raw()
        for rec in self._records or []:
            if rec["id"] == exp_id:
                return self._from_record(rec)
        return None

    def list(
        self,
        *,
        status: ExperienceStatus | tuple[ExperienceStatus, ...] | None = None,
        scope: str | None = None,
        limit: int = 50,
    ) -> list[Experience]:
        self._load_raw()
        wanted = None
        if status is not None:
            wanted = {
                ExperienceStatus(s) for s in (status if isinstance(status, tuple) else (status,))
            }
        results: list[Experience] = []
        for rec in self._records or []:
            if wanted is not None and ExperienceStatus(rec["status"]) not in wanted:
                continue
            if scope and rec["scope"] != scope:
                continue
            results.append(self._from_record(rec))
            if len(results) >= limit:
                break
        return results

    def find(
        self,
        query: str,
        *,
        limit: int = 3,
        min_confidence: float = 0.4,
    ) -> list[Experience]:
        query_words = self._words(query)
        if not query_words:
            return []
        now = datetime.now(UTC)
        scored: list[tuple[float, Experience]] = []
        for rec in self._load_raw() or []:
            if rec["status"] != ExperienceStatus.ATIVA:
                continue
            if rec["confidence"] < min_confidence:
                continue
            trigger_words = self._words(rec["trigger"] + " " + rec.get("observation", ""))
            overlap = trigger_words & query_words
            if not overlap:
                continue
            base = len(overlap) / max(len(trigger_words), 1)
            updated = datetime.fromisoformat(rec["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            days = max((now - updated).total_seconds() / 86400, 0.0)
            recency = math.exp(-days / 30.0)
            score = base * 0.7 + recency * 0.2 + rec["confidence"] * 0.1
            scored.append((score, self._from_record(rec)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [exp for _, exp in scored[:limit]]

    def confirm(self, exp_id: str) -> Experience | None:
        self._load_raw()
        for rec in self._records or []:
            if rec["id"] == exp_id and rec["status"] == ExperienceStatus.CANDIDATA:
                rec["status"] = ExperienceStatus.ATIVA
                rec["confidence"] = max(0.6, rec["confidence"])
                rec["updated_at"] = self._now()
                rec["version"] = rec.get("version", 1) + 1
                rec["history"].append({"at": self._now(), "event": "confirm"})
                self._save()
                return self._from_record(rec)
        return None

    def reject(self, exp_id: str) -> bool:
        self._load_raw()
        before = len(self._records or [])
        self._records = [r for r in (self._records or []) if r["id"] != exp_id]  # type: ignore[assignment]
        self._save()
        return len(self._records or []) < before  # type: ignore[arg-type]

    def invalidate(self, exp_id: str) -> Experience | None:
        self._load_raw()
        for rec in self._records or []:
            if rec["id"] == exp_id and rec["status"] != ExperienceStatus.INVALIDA:
                rec["status"] = ExperienceStatus.INVALIDA
                rec["updated_at"] = self._now()
                rec["version"] = rec.get("version", 1) + 1
                rec["history"].append({"at": self._now(), "event": "invalidate"})
                self._save()
                return self._from_record(rec)
        return None

    def record_outcome(self, exp_id: str, *, success: bool) -> Experience | None:
        self._load_raw()
        for rec in self._records or []:
            if rec["id"] == exp_id:
                rec["hits"] = rec.get("hits", 0) + 1
                if success:
                    rec["successes"] = rec.get("successes", 0) + 1
                    rec["confidence"] = min(0.99, rec["confidence"] + 0.05)
                else:
                    rec["confidence"] = max(0.05, rec["confidence"] - 0.10)
                rec["updated_at"] = self._now()
                rec["version"] = rec.get("version", 1) + 1
                rec["history"].append({"at": self._now(), "success": success})
                if len(rec["history"]) > self.max_history:
                    rec["history"] = rec["history"][-self.max_history :]
                if not success and rec["hits"] >= 3 and rec["confidence"] < 0.15:
                    rec["status"] = ExperienceStatus.INVALIDA
                    rec["history"].append(
                        {"at": self._now(), "event": "auto-invalidated: confidence too low"}
                    )
                self._save()
                return self._from_record(rec)
        return None

    def suggest_from_failure(self, query: str, error: str) -> Experience | None:
        query_words = self._words(query + " " + error)
        self._load_raw()
        for rec in self._records or []:
            if rec["status"] in (ExperienceStatus.ATIVA, ExperienceStatus.CANDIDATA):
                trigger_words = self._words(rec["trigger"])
                if trigger_words & query_words:
                    return None
        return self.add(
            trigger=f"{query} | {error}",
            observation=error,
            correction="(pendente de correcao)",
            source="aprendizado",
            status=ExperienceStatus.CANDIDATA,
            confidence=0.3,
        )

    @staticmethod
    def _from_record(rec: dict[str, Any]) -> Experience:
        return Experience(
            id=rec["id"],
            status=ExperienceStatus(rec["status"]),
            trigger=rec["trigger"],
            observation=rec["observation"],
            correction=rec["correction"],
            scope=rec.get("scope", "global"),
            source=rec.get("source", "usuario"),
            confidence=rec.get("confidence", 0.5),
            hits=rec.get("hits", 0),
            successes=rec.get("successes", 0),
            created_at=rec.get("created_at", ""),
            updated_at=rec.get("updated_at", ""),
            history=tuple(rec.get("history", [])),
            version=rec.get("version", 1),
        )


def build_experience_context(
    query: str,
    home: Path,
    *,
    enabled: bool = True,
    limit: int = 3,
    char_cap: int = 1200,
) -> str | None:
    if not enabled:
        return None
    store = ExperienceStore(home)
    results = store.find(query, limit=limit)
    if not results:
        return None
    lines: list[str] = [EXPERIENCE_BLOCK_HEADER]
    for exp in results:
        conf = f"{exp.confidence:.1f}"
        lines.append(f"[{exp.id}] {exp.trigger} — {exp.correction} (conf:{conf})")
    block = "\n".join(lines)
    if len(block) > char_cap:
        block = block[: char_cap - 20] + "\n... (truncado)"
    return block
