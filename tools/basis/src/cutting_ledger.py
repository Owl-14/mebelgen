"""Durable cross-process mutation ledger for Cutting operator runs.

SQLite transactions make ``prepared -> ambiguous -> success`` durable before
and after every external mutation.  A repeated key is never replayed by the
client: operators must reconcile remote state and use a new key when a prior
attempt was not applied.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{11,63}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{11,127}$")


class CuttingLedgerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LedgerRun:
    run_id: str
    mode: str
    fixture_sha256: str
    model_sha256: str
    transport_origin: str
    max_mutations: int
    deadline_epoch: float
    created_at: str


class MutationLedger:
    """Atomic durable ledger. Live use requires a normal filesystem path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if str(path) == ":memory:":
            raise ValueError("in-memory ledger is not durable")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK (mode IN ('offline_contract', 'live')),
                    fixture_sha256 TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL,
                    transport_origin TEXT NOT NULL,
                    max_mutations INTEGER NOT NULL CHECK (max_mutations > 0),
                    deadline_epoch REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    fingerprint TEXT NOT NULL,
                    route TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('prepared', 'ambiguous', 'success')),
                    prepared_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_sha256 TEXT
                );
                CREATE INDEX IF NOT EXISTS operations_run_idx ON operations(run_id);
                CREATE TABLE IF NOT EXISTS reconciliations (
                    idempotency_key TEXT PRIMARY KEY REFERENCES operations(idempotency_key),
                    remote_outcome TEXT NOT NULL CHECK (remote_outcome IN ('applied', 'not_applied')),
                    evidence_sha256 TEXT NOT NULL,
                    reconciled_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _hash(value: str, field: str) -> str:
        if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
        return value

    @staticmethod
    def _timestamp(now_epoch: float | None = None) -> str:
        epoch = time.time() if now_epoch is None else now_epoch
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def register_run(
        self,
        *,
        run_id: str,
        mode: str,
        fixture_sha256: str,
        model_sha256: str,
        transport_origin: str,
        max_mutations: int,
        deadline_epoch: float,
        now_epoch: float | None = None,
    ) -> LedgerRun:
        now = time.time() if now_epoch is None else now_epoch
        if not _RUN_RE.fullmatch(run_id):
            raise ValueError("run_id must be 12..64 safe ASCII characters")
        if mode not in {"offline_contract", "live"}:
            raise ValueError("mode must be offline_contract or live")
        fixture_hash = self._hash(fixture_sha256, "fixture_sha256")
        model_hash = self._hash(model_sha256, "model_sha256")
        if not isinstance(transport_origin, str) or not transport_origin.startswith("https://"):
            raise ValueError("transport_origin must be an HTTPS origin")
        if isinstance(max_mutations, bool) or not isinstance(max_mutations, int) or not 1 <= max_mutations <= 20:
            raise ValueError("max_mutations must be an integer 1..20")
        if not isinstance(deadline_epoch, (int, float)) or isinstance(deadline_epoch, bool) \
                or deadline_epoch <= now:
            raise ValueError("deadline_epoch must be in the future")
        values = (
            run_id, mode, fixture_hash, model_hash, transport_origin,
            max_mutations, float(deadline_epoch), self._timestamp(now),
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values
                )
            else:
                comparable = tuple(existing[field] for field in (
                    "run_id", "mode", "fixture_sha256", "model_sha256",
                    "transport_origin", "max_mutations", "deadline_epoch",
                ))
                if comparable != values[:7]:
                    raise CuttingLedgerError(
                        "cutting.ledger.run_collision",
                        "run_id is already registered with different immutable approval data",
                    )
            connection.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> LedgerRun:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise CuttingLedgerError("cutting.ledger.run_missing", "approved ledger run is missing")
        return LedgerRun(**dict(row))

    def prepare(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        fingerprint: str,
        route: str,
        trace_id: str,
        now_epoch: float | None = None,
    ) -> None:
        now = time.time() if now_epoch is None else now_epoch
        if not _KEY_RE.fullmatch(idempotency_key):
            raise CuttingLedgerError(
                "cutting.idempotency.required",
                "idempotency key must be 12..128 safe ASCII characters",
            )
        self._hash(fingerprint, "fingerprint")
        timestamp = self._timestamp(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise CuttingLedgerError("cutting.ledger.run_missing", "approved ledger run is missing")
            if float(run["deadline_epoch"]) <= now:
                raise CuttingLedgerError("cutting.deadline.exceeded", "approved run deadline has expired")
            existing = connection.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint or existing["run_id"] != run_id:
                    code = "cutting.idempotency.collision"
                    message = "idempotency key conflicts with another durable operation"
                elif existing["state"] in {"prepared", "ambiguous"}:
                    code = "cutting.idempotency.ambiguous"
                    message = "durable operation is incomplete; remote reconciliation is required"
                else:
                    code = "cutting.idempotency.duplicate"
                    message = "durable operation already succeeded; duplicate execution is blocked"
                raise CuttingLedgerError(code, message)
            count = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if count >= int(run["max_mutations"]):
                raise CuttingLedgerError(
                    "cutting.cost_guard.exhausted",
                    "durable mutation limit for the approved run is exhausted",
                )
            connection.execute(
                """INSERT INTO operations
                   (idempotency_key, run_id, fingerprint, route, trace_id, state,
                    prepared_at, updated_at, response_sha256)
                   VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, NULL)""",
                (idempotency_key, run_id, fingerprint, route, trace_id, timestamp, timestamp),
            )
            connection.commit()

    def mark_ambiguous(self, idempotency_key: str, *, now_epoch: float | None = None) -> None:
        self._transition(idempotency_key, "prepared", "ambiguous", now_epoch=now_epoch)

    def mark_success(
        self,
        idempotency_key: str,
        response_sha256: str,
        *,
        now_epoch: float | None = None,
    ) -> None:
        digest = self._hash(response_sha256, "response_sha256")
        self._transition(
            idempotency_key, "ambiguous", "success",
            response_sha256=digest, now_epoch=now_epoch,
        )

    def _transition(
        self,
        key: str,
        expected: str,
        target: str,
        *,
        response_sha256: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        timestamp = self._timestamp(now_epoch)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE operations SET state = ?, updated_at = ?, response_sha256 = COALESCE(?, response_sha256)
                   WHERE idempotency_key = ? AND state = ?""",
                (target, timestamp, response_sha256, key, expected),
            )
            if cursor.rowcount != 1:
                raise CuttingLedgerError(
                    "cutting.ledger.state_conflict",
                    f"cannot transition durable operation from {expected} to {target}",
                )
            connection.commit()

    def record_reconciliation(
        self,
        idempotency_key: str,
        *,
        remote_outcome: str,
        evidence_sha256: str,
        now_epoch: float | None = None,
    ) -> None:
        if remote_outcome not in {"applied", "not_applied"}:
            raise ValueError("remote_outcome must be applied or not_applied")
        digest = self._hash(evidence_sha256, "evidence_sha256")
        timestamp = self._timestamp(now_epoch)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if row is None or row["state"] not in {"prepared", "ambiguous"}:
                raise CuttingLedgerError(
                    "cutting.ledger.reconciliation_invalid",
                    "only incomplete operations can be reconciled",
                )
            connection.execute(
                "INSERT INTO reconciliations VALUES (?, ?, ?, ?)",
                (idempotency_key, remote_outcome, digest, timestamp),
            )
            if remote_outcome == "applied":
                connection.execute(
                    "UPDATE operations SET state = 'success', updated_at = ?, response_sha256 = ? "
                    "WHERE idempotency_key = ?",
                    (timestamp, digest, idempotency_key),
                )
            connection.commit()

    def operation(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return dict(row) if row is not None else None

    def run_operation_count(self, run_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row[0])
