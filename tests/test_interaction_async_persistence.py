from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_providers import (
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
)
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository


def _hold_write_lock(path: Path, locked: threading.Event, seconds: float) -> None:
    connection = sqlite3.connect(path, timeout=0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        locked.set()
        time.sleep(seconds)
        connection.rollback()
    finally:
        connection.close()


class AsyncPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_contention_does_not_block_unrelated_coroutine(self) -> None:
        """A sync transcript retry would freeze every session on the event loop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.db"
            connection = connect(path)
            initialize_schema(connection)
            sessions = SessionRepository(connection)
            sessions.create("s1", source="test")
            persistence = SQLiteAsyncInteractionPersistence(path)
            selection = ResolvedModelSelection(
                ProviderModelRef("fake", "chat"),
                SelectionReason.GLOBAL_DEFAULT,
            )
            locked = threading.Event()
            blocker = threading.Thread(
                target=_hold_write_lock,
                args=(path, locked, 0.3),
                daemon=True,
            )
            blocker.start()
            self.assertTrue(locked.wait(timeout=1))

            started = asyncio.get_running_loop().time()
            transcript_write = asyncio.create_task(
                persistence.append_turn_message("s1", "user", "olá", selection, api_content="olá")
            )
            await asyncio.sleep(0.05)
            unrelated_delay = asyncio.get_running_loop().time() - started

            message_id = await asyncio.wait_for(transcript_write, timeout=5)
            blocker.join(timeout=1)
            await persistence.aclose()
            row = connection.execute(
                "SELECT content FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            connection.close()

            self.assertLess(unrelated_delay, 0.15)
            self.assertEqual(row["content"], "olá")
