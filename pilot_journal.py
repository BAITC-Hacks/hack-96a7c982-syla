"""Local aggregate pilot audit trail. Never used as training data by Agent."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


class PilotJournal:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("""CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL,
            seed INTEGER NOT NULL, customers INTEGER NOT NULL, model_version TEXT NOT NULL,
            payload TEXT NOT NULL
        )""")
        return connection

    def save(self, report):
        # Do not persist customer IDs, uploaded CSVs, file names or ephemeral upload tokens.
        payload = {
            "source": report["dataset"]["kind"], "seed": report["seed"],
            "customers": report["dataset"]["customers"], "model_version": report["model_version"],
            "dataset_fingerprint": report["dataset_fingerprint"],
            "provenance": "simulation", "summary": report["summary"],
            "pilots": report["pilot_trace"],
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        key = hashlib.sha256(encoded.encode()).hexdigest()
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, created, payload["source"], payload["seed"], payload["customers"],
                     payload["model_version"], encoded),
                )
                inserted = cursor.rowcount == 1
            result = self.get(key)
            result["new_record"] = inserted
            return result
        finally:
            connection.close()

    def list(self):
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, created_at, source, seed, customers, model_version FROM runs "
                "ORDER BY created_at DESC, id DESC LIMIT 100"
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            return {"runs": [dict(row) for row in rows], "total": total}
        finally:
            connection.close()

    def get(self, key):
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (key,)).fetchone()
            if row is None:
                raise KeyError("Запись не найдена.")
            result = json.loads(row["payload"])
            result.update(id=row["id"], created_at=row["created_at"])
            return result
        finally:
            connection.close()
