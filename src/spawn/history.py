import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .contracts import Model, Request

MIN_OUTCOMES = 20


def cohort_key(request: Request, model: Model, strategy: str, source_bytes: int) -> str:
    return json.dumps(
        [
            "spawn-v1",
            request.task_class,
            asdict(model),
            strategy,
            len(request.criteria),
            len(request.sources),
            int(math.log2(max(1, source_bytes))),
            int(math.log2(max(0.000001, request.budget.max_usd))),
            int(math.log2(request.budget.max_seconds)),
        ],
        separators=(",", ":"),
    )


class History:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, cohort TEXT NOT NULL, cost REAL NOT NULL,
                    seconds REAL NOT NULL, success INTEGER, eligible INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                yield connection
        finally:
            connection.close()

    def forecast(self, cohort: str) -> dict:
        base = {
            "percent": None,
            "interval_percent": None,
            "samples": 0,
            "status": "insufficient_evidence",
            "calibrated": False,
            "minimum_samples": MIN_OUTCOMES,
            "meaning": (
                "Full original contract success, manually labeled, in a coarse cohort"
            ),
        }
        if not self.path.exists():
            return base
        with self._connect() as connection:
            count, successes = connection.execute(
                "SELECT COUNT(*), SUM(success) FROM runs "
                "WHERE cohort=? AND success IS NOT NULL",
                (cohort,),
            ).fetchone()
        base["samples"] = count
        if count < MIN_OUTCOMES:
            return base
        rate = successes / count
        z = 1.96
        denominator = 1 + z * z / count
        center = (rate + z * z / (2 * count)) / denominator
        half = (
            z
            * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count**2))
            / denominator
        )
        return {
            **base,
            "percent": round(rate * 100, 2),
            "interval_percent": [
                round((center - half) * 100, 2),
                round((center + half) * 100, 2),
            ],
            "status": "historical_rate_not_calibrated",
        }

    def record(
        self, run_id: str, cohort: str, cost: float, seconds: float, *, eligible: bool
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs (run_id,cohort,cost,seconds,eligible) "
                "VALUES (?,?,?,?,?)",
                (run_id, cohort, cost, seconds, int(eligible)),
            )

    def label(self, run_id: str, success: bool) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT eligible FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown run ID: {run_id}")
            if success and not row[0]:
                raise ValueError(
                    "A partial, unpriced or over-limit run cannot be labeled successful"
                )
            cursor = connection.execute(
                "UPDATE runs SET success=? WHERE run_id=?", (int(success), run_id)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown run ID: {run_id}")
