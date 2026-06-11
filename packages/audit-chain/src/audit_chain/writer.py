"""审计哈希链(协议 L8/L11)。

硬规则3: audit_ledger 只能 append,本类不提供任何 update/delete API;
prev_hash/row_hash 的计算逻辑禁止修改,改动须人工评审。
每个 encounter 一条链,写入加锁串行化保证链不分叉。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

GENESIS = "0" * 64


def canonical(payload: dict) -> str:
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def row_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(payload)).encode()).hexdigest()


class AuditLedger:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_ledger (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              encounter_id TEXT,
              actor TEXT NOT NULL,
              action TEXT NOT NULL,
              payload TEXT NOT NULL,
              prev_hash TEXT NOT NULL,
              row_hash TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.commit()

    def append(
        self, encounter_id: str, actor: str, action: str, payload: dict
    ) -> dict:
        record = {
            "encounter_id": encounter_id,
            "actor": actor,
            "action": action,
            "data": payload,
        }
        with self._lock:
            row = self._conn.execute(
                "SELECT row_hash FROM audit_ledger WHERE encounter_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (encounter_id,),
            ).fetchone()
            prev = row[0] if row else GENESIS
            h = row_hash(prev, record)
            cur = self._conn.execute(
                "INSERT INTO audit_ledger "
                "(encounter_id, actor, action, payload, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (encounter_id, actor, action, canonical(record), prev, h),
            )
            self._conn.commit()
            return {
                "seq": cur.lastrowid,
                "encounter_id": encounter_id,
                "actor": actor,
                "action": action,
                "prev_hash": prev,
                "row_hash": h,
            }

    def entries(self, encounter_id: str | None = None) -> list[dict]:
        sql = (
            "SELECT seq, encounter_id, actor, action, payload, prev_hash, "
            "row_hash, created_at FROM audit_ledger"
        )
        params: tuple = ()
        if encounter_id is not None:
            sql += " WHERE encounter_id = ?"
            params = (encounter_id,)
        sql += " ORDER BY seq"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "seq": r[0],
                "encounter_id": r[1],
                "actor": r[2],
                "action": r[3],
                "payload": json.loads(r[4]),
                "prev_hash": r[5],
                "row_hash": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]

    def verify_chain(self, encounter_id: str) -> bool:
        prev = GENESIS
        for entry in self.entries(encounter_id):
            if entry["prev_hash"] != prev:
                return False
            if row_hash(prev, entry["payload"]) != entry["row_hash"]:
                return False
            prev = entry["row_hash"]
        return True

    def export_case_trace(self, encounter_id: str) -> dict:
        """监管导出格式:全链条目 + 链完整性校验结果。"""
        return {
            "encounter_id": encounter_id,
            "chain_valid": self.verify_chain(encounter_id),
            "entries": self.entries(encounter_id),
        }
