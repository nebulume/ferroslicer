"""
Print job tracking database for MeshyGen.
SQLite-backed store for all slicing jobs, send history, and print status.
"""

import sqlite3
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


def _db_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path.home() / "Documents" / "FerroSlicer"
        base.mkdir(parents=True, exist_ok=True)
        return base / "prints.db"
    return Path(__file__).parent.parent / "data" / "prints.db"

DB_PATH = _db_path()


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS print_jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name    TEXT NOT NULL,
                stl_file    TEXT,
                gcode_file  TEXT,
                settings_json TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                sent_at     TEXT,
                completed_at TEXT,
                status      TEXT DEFAULT 'generated',
                printer_ip  TEXT,
                notes       TEXT
            )
        """)


def add_job(
    stl_file: str,
    gcode_file: str,
    settings: dict,
    printer_ip: str = "",
    job_name: str = "",
) -> int:
    if not job_name:
        job_name = Path(gcode_file).stem if gcode_file else Path(stl_file).stem
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO print_jobs
               (job_name, stl_file, gcode_file, settings_json, printer_ip, status)
               VALUES (?, ?, ?, ?, ?, 'generated')""",
            (job_name, stl_file, gcode_file, json.dumps(settings), printer_ip),
        )
        return cur.lastrowid


def update_status(job_id: int, status: str) -> None:
    """Update job status. Also sets sent_at/completed_at timestamps."""
    with _get_conn() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if status == "sent":
            conn.execute(
                "UPDATE print_jobs SET status=?, sent_at=? WHERE id=?",
                (status, now, job_id),
            )
        elif status in ("completed", "failed"):
            conn.execute(
                "UPDATE print_jobs SET status=?, completed_at=? WHERE id=?",
                (status, now, job_id),
            )
        else:
            conn.execute(
                "UPDATE print_jobs SET status=? WHERE id=?",
                (status, job_id),
            )


def get_all_jobs() -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM print_jobs ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM print_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_job(job_id: int) -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM print_jobs WHERE id=?", (job_id,))


# Auto-init on import
init_db()
