"""事件溯源存储(append-only)。clinical_state 由事件流重建,支持断线恢复与全量回放。"""
from __future__ import annotations

import json
import sqlite3
import threading


class EventStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS encounter_events (
              event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
              encounter_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.commit()

    def append(self, encounter_id: str, event_type: str, payload: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO encounter_events (encounter_id, event_type, payload) "
                "VALUES (?, ?, ?)",
                (
                    encounter_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def events(self, encounter_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT event_seq, event_type, payload FROM encounter_events "
            "WHERE encounter_id = ? ORDER BY event_seq",
            (encounter_id,),
        ).fetchall()
        return [
            {"seq": r[0], "event_type": r[1], "payload": json.loads(r[2])}
            for r in rows
        ]
