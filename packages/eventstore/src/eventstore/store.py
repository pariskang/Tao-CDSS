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
        # encounter_id 索引: resume/回放按 encounter 查询是主访问路径
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_encounter_events_encounter "
            "ON encounter_events (encounter_id, event_seq)"
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
        # 读也加锁: 与 append 的写锁对称,避免多线程下读到半提交状态
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_seq, event_type, payload FROM encounter_events "
                "WHERE encounter_id = ? ORDER BY event_seq",
                (encounter_id,),
            ).fetchall()
        return [
            {"seq": r[0], "event_type": r[1], "payload": json.loads(r[2])}
            for r in rows
        ]

    def all_events(self) -> list[dict]:
        """全量回放 API(README 声明"全量回放"的真实入口): 跨 encounter
        按全局序返回,供离线审计/迁移/重建投影使用。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_seq, encounter_id, event_type, payload "
                "FROM encounter_events ORDER BY event_seq"
            ).fetchall()
        return [
            {"seq": r[0], "encounter_id": r[1], "event_type": r[2],
             "payload": json.loads(r[3])}
            for r in rows
        ]

    def encounter_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT encounter_id FROM encounter_events "
                "ORDER BY encounter_id"
            ).fetchall()
        return [r[0] for r in rows]

    # ---------------------------------------------------------- 资源管理
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
