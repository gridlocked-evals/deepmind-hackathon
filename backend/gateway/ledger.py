from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4

from .config import GatewayConfig, dollars
from .contracts import ErrorCode, GatewayError, PreparedCall, Usage, canonical, digest


class Ledger:
    """Atomic, restart-safe reservations shared by API workers and the smoke CLI."""

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.root = config.data_dir
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        self.database = self.root / "usage.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    request_id TEXT PRIMARY KEY, call_id TEXT NOT NULL,
                    service TEXT NOT NULL, model_id TEXT NOT NULL, stage TEXT NOT NULL,
                    context TEXT NOT NULL, config_hash TEXT NOT NULL, credential_hash TEXT NOT NULL,
                    input_hash TEXT NOT NULL, result_hash TEXT, provider_request_id TEXT,
                    created_at REAL NOT NULL, finished_at REAL, lease_until REAL NOT NULL,
                    outcome TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
                    charged_microusd INTEGER NOT NULL, charged_tokens INTEGER NOT NULL,
                    usage TEXT, latency_ms REAL, qualifying INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS attempt_service ON attempts(service, created_at);
                CREATE TABLE IF NOT EXISTS blocks (reason TEXT PRIMARY KEY, request_id TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def artifact(self, content: bytes) -> str:
        fingerprint = digest(content)
        path = self.root / "artifacts" / f"{fingerprint}.json"
        if path.exists():
            if digest(path.read_bytes()) != fingerprint:
                raise GatewayError(ErrorCode.STORAGE)
            return fingerprint
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Publish only complete bytes. Concurrent writers of this hash have
            # identical content, so replacing their completed artifact is harmless.
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return fingerprint

    def reserve(self, call: PreparedCall, call_id: str, credential_hash: str,
                model_id: str, microusd: int, tokens: int, timeout: float) -> str:
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM blocks LIMIT 1").fetchone():
                raise GatewayError(ErrorCode.BUDGET)
            if call.service == "genie":
                # _execute marks dispatched before any provider request, so an expired undispatched
                # row is a crash before sending: nothing can be running. Its reservation stays charged.
                db.execute("UPDATE attempts SET outcome=?,finished_at=? WHERE service='genie' AND outcome='pending' AND dispatched=0 AND lease_until<=?",
                           (ErrorCode.CANCELLED.value, now, now))
                unresolved = db.execute(
                    "SELECT MAX(dispatched=1 AND (outcome!='pending' OR lease_until<=?)) FROM attempts WHERE service='genie' AND (outcome='pending' OR (outcome='termination_unconfirmed' AND dispatched=1))",
                    (now,),
                ).fetchone()[0]
                if unresolved is not None:
                    # A live or never-sent world only has to finish; a dispatched one nobody owns may still bill.
                    raise GatewayError(ErrorCode.TERMINATION if unresolved else ErrorCode.CONCURRENCY)
            # Genie is bounded by the one-world rule above, so it neither uses nor takes a slot.
            elif db.execute(
                "SELECT COUNT(*) FROM attempts WHERE outcome='pending' AND lease_until>? AND service!='genie'", (now,)
            ).fetchone()[0] >= self.config.max_concurrency:
                raise GatewayError(ErrorCode.CONCURRENCY)
            spent = db.execute("SELECT COALESCE(SUM(charged_microusd),0) FROM attempts").fetchone()[0]
            stage_spent, stage_tokens = db.execute(
                "SELECT COALESCE(SUM(charged_microusd),0), COALESCE(SUM(charged_tokens),0) FROM attempts WHERE stage=?",
                (call.stage,),
            ).fetchone()
            # Only explicit demo calls may consume the reserve.
            ceiling = dollars(self.config.total_usd)
            if call.context.purpose != "demo":
                ceiling -= dollars(self.config.demo_reserve_usd)
            stage = self.config.stages[call.stage]
            if spent + microusd > ceiling or stage_spent + microusd > dollars(stage.usd) or stage_tokens + tokens > stage.tokens:
                raise GatewayError(ErrorCode.BUDGET)
            identifier = str(uuid4())
            db.execute("""
                INSERT INTO attempts
                (request_id,call_id,service,model_id,stage,context,config_hash,credential_hash,
                 input_hash,created_at,lease_until,outcome,charged_microusd,charged_tokens)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)
            """, (identifier, call_id, call.service.value, model_id, call.stage,
                  call.context.model_dump_json(), call.config_hash, credential_hash, call.input_hash,
                  now, now + timeout + 5, microusd, tokens))
        return identifier

    def reject(self, call: PreparedCall, call_id: str, credential_hash: str,
               model_id: str, code: ErrorCode) -> str:
        identifier = str(uuid4())
        now = time.time()
        with self.connection() as db:
            db.execute("""
                INSERT INTO attempts
                (request_id,call_id,service,model_id,stage,context,config_hash,credential_hash,
                 input_hash,created_at,finished_at,lease_until,outcome,charged_microusd,charged_tokens)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
            """, (identifier, call_id, call.service.value, model_id, call.stage,
                  call.context.model_dump_json(), call.config_hash, credential_hash,
                  call.input_hash, now, now, now, code.value))
        return identifier

    def dispatched(self, request_id: str):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # A paused worker may resume after another worker reclaimed its lease.
            # Atomically fence that stale owner before any provider request is sent.
            updated = db.execute("""
                UPDATE attempts SET dispatched=1
                WHERE request_id=? AND outcome='pending' AND dispatched=0 AND lease_until>?
            """, (request_id, time.time()))
            if updated.rowcount != 1:
                raise GatewayError(ErrorCode.CANCELLED)

    def provider_id(self, request_id: str, provider_id: str):
        with self.connection() as db:
            db.execute("UPDATE attempts SET provider_request_id=? WHERE request_id=?", (provider_id, request_id))

    def finish(self, request_id: str, *, outcome: str, latency_ms: float,
               result_hash: str | None = None, usage: Usage | None = None,
               microusd: int | None = None, qualifying: bool = False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM attempts WHERE request_id=?", (request_id,)).fetchone()
            if usage is not None and microusd is not None:
                if microusd > row["charged_microusd"] or usage.charged_tokens > row["charged_tokens"]:
                    db.execute("INSERT OR IGNORE INTO blocks VALUES ('request_allowance_exceeded',?)", (request_id,))
                db.execute("UPDATE attempts SET charged_microusd=?,charged_tokens=?,usage=? WHERE request_id=?",
                           (microusd, usage.charged_tokens, usage.model_dump_json(), request_id))
            db.execute("""
                UPDATE attempts SET outcome=?,finished_at=?,latency_ms=?,result_hash=?,qualifying=? WHERE request_id=?
            """, (outcome, time.time(), round(latency_ms, 3), result_hash, int(qualifying), request_id))

    def attempts(self, *, limit: int = 50) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY created_at DESC LIMIT ?", (limit,))]

    def get(self, request_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM attempts WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def latest(self, service: str, credential_hash: str, config_hash: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("""
                SELECT * FROM attempts WHERE service=? AND credential_hash=? AND config_hash=?
                AND dispatched=1 ORDER BY created_at DESC LIMIT 1
            """, (service, credential_hash, config_hash)).fetchone()
            return dict(row) if row else None

    def budget_status(self) -> dict:
        with self.connection() as db:
            charged = db.execute("SELECT COALESCE(SUM(charged_microusd),0) FROM attempts").fetchone()[0]
            stages = {row["stage"]: {"charged_usd": row["cost"] / 1_000_000, "charged_tokens": row["tokens"]}
                      for row in db.execute("SELECT stage,SUM(charged_microusd) cost,SUM(charged_tokens) tokens FROM attempts GROUP BY stage")}
            unresolved = [row[0] for row in db.execute(
                "SELECT request_id FROM attempts WHERE service='genie' AND (outcome='pending' OR (outcome='termination_unconfirmed' AND dispatched=1))")]
            blocks = [dict(row) for row in db.execute("SELECT * FROM blocks")]
        return {"charged_usd": charged / 1_000_000, "limit_usd": float(self.config.total_usd),
                "demo_reserve_usd": float(self.config.demo_reserve_usd), "stages": stages,
                "blocks": blocks, "unresolved_world_requests": unresolved,
                "notice": "Includes conservative reservations for unreported/unknown usage; not a provider invoice."}

    def submission_models(self, episode_id: str, source_id: str, capture_id: str) -> dict:
        with self.connection() as db:
            rows = db.execute("SELECT request_id,stage,context FROM attempts WHERE outcome='success' AND qualifying=1").fetchall()
        traces: dict[str, list[str]] = {"observation": [], "compilation": []}
        for row in rows:
            context = json.loads(row["context"])
            if (context["purpose"] in ("submission", "demo") and context["episode_id"] == episode_id
                    and context["source_id"] == source_id and context["capture_id"] == capture_id
                    and row["stage"] in traces):
                traces[row["stage"]].append(row["request_id"])
        return {"ready": all(traces.values()), "traces": traces,
                "missing": [role for role, ids in traces.items() if not ids]}
