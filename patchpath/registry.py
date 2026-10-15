"""SQLite registry: snapshot pointer + job metadata. The only state readers
trust for "which graph is READY". Relationship data lives in FalkorDB."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    graph_name TEXT NOT NULL,
    status TEXT NOT NULL,            -- BUILDING | READY | FAILED
    stats_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Registry:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(DDL)

    def begin(self, snapshot_id: str, repo: str, sha: str, an: str, sc: str, graph: str) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot_id, repo, sha, an, sc, graph, "BUILDING", None, None, now, now))
        self.conn.commit()

    def ready(self, snapshot_id: str, stats: dict) -> None:
        self.conn.execute(
            "UPDATE snapshots SET status='READY', stats_json=?, error=NULL, updated_at=? WHERE snapshot_id=?",
            (json.dumps(stats), time.time(), snapshot_id))
        self.conn.commit()

    def fail(self, snapshot_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE snapshots SET status='FAILED', error=?, updated_at=? WHERE snapshot_id=?",
            (error, time.time(), snapshot_id))
        self.conn.commit()

    def latest_ready(self) -> dict | None:
        """Most recently updated READY snapshot, or None."""
        row = self.conn.execute(
            "SELECT repo, sha, graph_name, stats_json FROM snapshots "
            "WHERE status='READY' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        repo, sha, graph, stats = row
        return {"repo": repo, "sha": sha, "graph": graph,
                "stats": json.loads(stats or "{}")}

    def get_ready(self, snapshot_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT repo, sha, graph_name, stats_json FROM snapshots WHERE snapshot_id=? AND status='READY'",
            (snapshot_id,)).fetchone()
        if not row:
            return None
        return {"repo": row[0], "sha": row[1], "graph": row[2],
                "stats": json.loads(row[3]) if row[3] else {}}
