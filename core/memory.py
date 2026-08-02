"""Business memory storage.

This is the single source of truth for the whole app: company profile,
tasks, approvals, decisions, daily logs, reports, etc.

Storage backend is chosen at runtime by whether `DATABASE_URL` is set:

- **Not set (default, local dev):** a local SQLite file at
  `database/leadlens.db`, in WAL mode with real transactions.
- **Set to a Postgres connection string (Supabase, Neon, or any standard
  Postgres):** the data lives there instead, decoupled from wherever the
  app itself is hosted. This is the option to use once real clinic data
  is involved and the app is deployed somewhere with an ephemeral
  filesystem (Streamlit Community Cloud, most free/default hosting
  tiers) — the app container can sleep, reboot, or redeploy as often as
  it wants; the data survives regardless, because it isn't sitting on
  that container's local disk at all.

Either way, the very first time this connects to an empty store it will
look for existing local data (a local leadlens.db, or the original
company.json) and migrate it in automatically, so switching backends
never silently starts you over with blank data.

Every function below keeps its exact original name and behaviour, so no
other file in the app needs to change no matter which backend is active.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_FOLDER = PROJECT_ROOT / "database"
def _db_file() -> Path:
    """Computed fresh each call (not a baked-in constant) so that anything
    which reassigns DATABASE_FOLDER at runtime — tests, in particular —
    actually gets isolated storage instead of silently still pointing at
    whatever path was current when this module was first imported."""
    return DATABASE_FOLDER / "leadlens.db"


def _company_file() -> Path:
    """Kept only so any legacy code path (or the one-time migrations below)
    can still find the old JSON file if it exists on disk. Computed fresh
    for the same isolation reason as _db_file() above."""
    return DATABASE_FOLDER / "company.json"

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS memory_store (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
"""

DEFAULT_MEMORY = {
    "company": {},
    "daily_logs": [],
    "decisions": [],
    "tasks": [],
    "completed_tasks": [],
    "meetings": [],
    "campaigns": [],
    "reports": [],
    "employees": [],
    "clients": [],
    "sales": [],
    "expenses": [],
    "marketing": [],
    "finance": [],
    "hr": [],
    "operations": [],
    "kpis": [],
    "approvals": [],
}


def _fresh_default():
    return copy.deepcopy(DEFAULT_MEMORY)


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _using_postgres() -> bool:
    return bool(_database_url())


def _row_to_memory(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid database format")
    if "company" not in data:
        old_company_data = data
        data = _fresh_default()
        data["company"] = old_company_data
    for key, value in DEFAULT_MEMORY.items():
        data.setdefault(key, copy.deepcopy(value))
    return data


# ---------------------------------------------------------------------------
# SQLite backend (default, local dev)
# ---------------------------------------------------------------------------

def _sqlite_ensure_version_column(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_store)").fetchall()}
    if "version" not in columns:
        conn.execute("ALTER TABLE memory_store ADD COLUMN version INTEGER NOT NULL DEFAULT 1")


def _sqlite_connect() -> sqlite3.Connection:
    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_file()), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.execute(CREATE_TABLE_SQL)
    _sqlite_ensure_version_column(conn)
    return conn


def _sqlite_load_existing_payload() -> str | None:
    """Read the raw payload from the local SQLite file, if any exists.

    Used both as the normal local-dev read path, and as a one-time source
    to migrate from when switching over to Postgres for the first time.
    """
    if not _db_file().exists():
        return None
    conn = _sqlite_connect()
    try:
        row = conn.execute("SELECT payload FROM memory_store WHERE id = 1").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _migrate_legacy_json_payload() -> str | None:
    """One-time import of the old database/company.json, if it's still there.

    Returns the migrated payload as a JSON string, or None if there was
    nothing to migrate. Renames the old file to a dated backup so nothing
    is ever silently deleted.
    """
    if not _company_file().exists():
        return None
    try:
        with _company_file().open("r", encoding="utf-8") as file:
            legacy = json.load(file)
        if not isinstance(legacy, dict):
            return None
        data = _row_to_memory(json.dumps(legacy))
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    try:
        backup = _company_file().with_suffix(f".migrated-{int(datetime.now().timestamp())}.json")
        shutil.move(str(_company_file()), str(backup))
    except OSError:
        pass
    return json.dumps(data, ensure_ascii=False)


def _sqlite_ensure_database():
    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
    conn = _sqlite_connect()
    try:
        row = conn.execute("SELECT payload FROM memory_store WHERE id = 1").fetchone()
        if row is not None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        payload = _migrate_legacy_json_payload() or json.dumps(_fresh_default(), ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO memory_store (id, payload, updated_at) VALUES (1, ?, ?)",
            (payload, now),
        )
    finally:
        conn.close()


def _sqlite_load_raw() -> str:
    _sqlite_ensure_database()
    conn = _sqlite_connect()
    try:
        row = conn.execute("SELECT payload FROM memory_store WHERE id = 1").fetchone()
        return row[0] if row else json.dumps(_fresh_default(), ensure_ascii=False)
    finally:
        conn.close()


def _sqlite_save_raw(payload: str) -> None:
    """Unconditional overwrite (no optimistic-lock check) — but it still
    must bump `version`, or a concurrent update_memory() that read the row
    before this write won't detect this write happened and will silently
    clobber it on its own save. `INSERT OR REPLACE` can't do that (SQLite
    implements it as delete-then-reinsert, which would reset version to
    its default instead of incrementing it), so this uses an explicit
    UPDATE, falling back to INSERT only if the row doesn't exist yet."""
    conn = _sqlite_connect()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE memory_store SET payload = ?, version = version + 1, updated_at = ? "
            "WHERE id = 1",
            (payload, now),
        )
        if cursor.rowcount == 0:
            conn.execute(
                "INSERT INTO memory_store (id, payload, updated_at, version) VALUES (1, ?, ?, 1)",
                (payload, now),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _sqlite_load_versioned() -> tuple[str, int]:
    """Like _sqlite_load_raw, but also returns the row's version counter so
    the caller can save back with an optimistic-lock check (see
    update_memory below)."""
    _sqlite_ensure_database()
    conn = _sqlite_connect()
    try:
        row = conn.execute("SELECT payload, version FROM memory_store WHERE id = 1").fetchone()
        if row is None:
            return json.dumps(_fresh_default(), ensure_ascii=False), 1
        return row[0], row[1]
    finally:
        conn.close()


def _sqlite_save_versioned(payload: str, expected_version: int) -> bool:
    """Save only if the row's version still matches what was read. Returns
    False (writing nothing) on a conflict, so the caller can retry against
    fresh data instead of silently clobbering someone else's write."""
    conn = _sqlite_connect()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE memory_store SET payload = ?, version = version + 1, updated_at = ? "
            "WHERE id = 1 AND version = ?",
            (payload, now, expected_version),
        )
        if cursor.rowcount == 0:
            conn.execute("ROLLBACK")
            return False
        conn.execute("COMMIT")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Postgres backend (Supabase / Neon / any standard Postgres via DATABASE_URL)
# ---------------------------------------------------------------------------

def _pg_connect():
    try:
        import psycopg2
    except ImportError as error:
        raise RuntimeError(
            "DATABASE_URL is set but the 'psycopg2-binary' package is not "
            "installed. Run 'python -m pip install -r requirements.txt'."
        ) from error
    try:
        conn = psycopg2.connect(_database_url())
    except psycopg2.OperationalError as error:
        raise RuntimeError(
            "Could not connect to the Postgres database at DATABASE_URL. "
            "Double-check the connection string (host, password, and that "
            "sslmode is set if your provider requires it) and that the "
            f"database is reachable from here. Original error: {error}"
        ) from error
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(
            "ALTER TABLE memory_store ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
        )
    conn.commit()
    return conn


def _pg_load_raw() -> str:
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM memory_store WHERE id = 1")
            row = cur.fetchone()
        if row is not None:
            return row[0]

        # Empty database: migrate in whatever local data already exists
        # (a local leadlens.db first, then the original company.json)
        # instead of silently starting the customer over with nothing.
        payload = _sqlite_load_existing_payload() or _migrate_legacy_json_payload()
        payload = payload or json.dumps(_fresh_default(), ensure_ascii=False)
        now = datetime.now().isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_store (id, payload, updated_at) VALUES (1, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, "
                "updated_at = EXCLUDED.updated_at",
                (payload, now),
            )
        conn.commit()
        return payload
    finally:
        conn.close()


def _pg_save_raw(payload: str) -> None:
    """Unconditional overwrite (no optimistic-lock check) — but it still
    must bump `version` on conflict, or a concurrent update_memory() that
    read the row before this write won't detect this write happened and
    will silently clobber it on its own save."""
    conn = _pg_connect()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_store (id, payload, updated_at, version) VALUES (1, %s, %s, 1) "
                "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, "
                "updated_at = EXCLUDED.updated_at, version = memory_store.version + 1",
                (payload, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _pg_load_versioned() -> tuple[str, int]:
    """Like _pg_load_raw, but also returns the row's version counter so the
    caller can save back with an optimistic-lock check (see update_memory
    below)."""
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT payload, version FROM memory_store WHERE id = 1")
            row = cur.fetchone()
        if row is not None:
            return row[0], row[1]

        payload = _sqlite_load_existing_payload() or _migrate_legacy_json_payload()
        payload = payload or json.dumps(_fresh_default(), ensure_ascii=False)
        now = datetime.now().isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_store (id, payload, updated_at, version) VALUES (1, %s, %s, 1) "
                "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, "
                "updated_at = EXCLUDED.updated_at",
                (payload, now),
            )
        conn.commit()
        return payload, 1
    finally:
        conn.close()


def _pg_save_versioned(payload: str, expected_version: int) -> bool:
    """Save only if the row's version still matches what was read. Returns
    False (writing nothing) on a conflict, so the caller can retry against
    fresh data instead of silently clobbering someone else's write."""
    conn = _pg_connect()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE memory_store SET payload = %s, version = version + 1, updated_at = %s "
                "WHERE id = 1 AND version = %s",
                (payload, now, expected_version),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backend-agnostic public API (unchanged from before)
# ---------------------------------------------------------------------------

def ensure_database():
    if _using_postgres():
        _pg_load_raw()
    else:
        _sqlite_ensure_database()


def load_memory():
    raw = _pg_load_raw() if _using_postgres() else _sqlite_load_raw()
    try:
        return _row_to_memory(raw)
    except (json.JSONDecodeError, ValueError):
        # Corrupt payload: back it up for inspection, then reset cleanly
        # rather than ever crashing the app on a bad row.
        DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
        backup_path = DATABASE_FOLDER / f"leadlens.corrupt-{int(datetime.now().timestamp())}.json"
        try:
            backup_path.write_text(raw, encoding="utf-8")
        except OSError:
            pass
        data = _fresh_default()
        save_memory(data)
        return data


def save_memory(memory):
    payload = json.dumps(memory, ensure_ascii=False)
    if _using_postgres():
        _pg_save_raw(payload)
    else:
        _sqlite_save_raw(payload)


def load_memory_versioned():
    """Like load_memory(), but also returns the version counter the row
    had at read time — pair with save_memory_versioned() (or just use
    update_memory() below) to detect a concurrent write instead of
    silently overwriting it."""
    raw, version = _pg_load_versioned() if _using_postgres() else _sqlite_load_versioned()
    try:
        return _row_to_memory(raw), version
    except (json.JSONDecodeError, ValueError):
        # Corrupt payload: same recovery as load_memory() — back it up,
        # reset cleanly, and report the fresh row's version.
        DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
        backup_path = DATABASE_FOLDER / f"leadlens.corrupt-{int(datetime.now().timestamp())}.json"
        try:
            backup_path.write_text(raw, encoding="utf-8")
        except OSError:
            pass
        data = _fresh_default()
        save_memory(data)
        return load_memory_versioned()


def save_memory_versioned(memory, expected_version):
    """Save only if nothing else has saved since `expected_version` was
    read. Returns True if the save landed, False on a conflict."""
    payload = json.dumps(memory, ensure_ascii=False)
    if _using_postgres():
        return _pg_save_versioned(payload, expected_version)
    return _sqlite_save_versioned(payload, expected_version)


class ConcurrentUpdateError(RuntimeError):
    """update_memory() couldn't win the optimistic-lock race after
    retrying — some other process is saving very frequently."""


NO_CHANGE = object()


def update_memory(mutator, max_retries=8):
    """Atomically read-modify-write the whole memory store.

    `mutator(memory)` mutates the dict in place; its return value is
    ignored unless it is the NO_CHANGE sentinel, which skips the write
    entirely (for mutators that may decide there's nothing to do).

    If another process saves in between this function's read and write,
    the write is rejected and the whole cycle retries against a fresh
    read — so `mutator` may run more than once and must be safe to re-run
    (a pure function of whichever memory dict it's handed).

    This is how every mutator in this module avoids the lost-update race
    that a bare load_memory() + save_memory() has: two processes reading
    the same row and saving back full copies, where the second save
    silently erases whatever the first one added.
    """
    for attempt in range(max_retries):
        memory, version = load_memory_versioned()
        outcome = mutator(memory)
        if outcome is NO_CHANGE:
            return memory
        if save_memory_versioned(memory, version):
            return memory
        time.sleep(0.05 * (attempt + 1))
    raise ConcurrentUpdateError(
        f"Could not save after {max_retries} attempts — too many concurrent writers."
    )


def generate_id(section):
    prefix = section.upper().replace("_", "-")
    return f"{prefix}-{uuid4().hex[:8].upper()}"


def company_exists():
    return bool(load_memory().get("company", {}))


def load_company():
    return load_memory().get("company", {})


def save_company(company_data):
    def mutate(memory):
        memory["company"] = company_data
    update_memory(mutate)


def update_company(key, value):
    def mutate(memory):
        memory.setdefault("company", {})[key] = value
    update_memory(mutate)


def reset_company():
    def mutate(memory):
        memory.clear()
        memory.update(_fresh_default())
    update_memory(mutate)


def get_company_value(key, default=None):
    return load_company().get(key, default)


def add_memory_entry(section, entry):
    entry_data = {
        "id": generate_id(section),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": entry,
    }

    def mutate(memory):
        memory.setdefault(section, []).append(entry_data)

    update_memory(mutate)
    return entry_data


def get_memory_section(section):
    return load_memory().get(section, [])


def _open_entry_exists(memory, section, title, department=""):
    title_key = title.strip().casefold()
    department_key = department.strip().casefold()
    for item in memory.get(section, []):
        data = item.get("data", {})
        same_title = str(data.get("title", "")).strip().casefold() == title_key
        same_department = not department_key or str(data.get("department", "")).strip().casefold() == department_key
        status = str(data.get("status", "Pending")).casefold()
        if same_title and same_department and status in {"pending", "open"}:
            return item
    return None


def add_task(title, department, priority="Medium", status="Pending"):
    if not title.strip():
        return None
    result = {}

    def mutate(memory):
        existing = _open_entry_exists(memory, "tasks", title, department)
        if existing:
            result["entry"] = existing
            return NO_CHANGE
        entry_data = {
            "id": generate_id("tasks"),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": {
                "title": title,
                "department": department,
                "priority": priority,
                "status": status,
            },
        }
        memory.setdefault("tasks", []).append(entry_data)
        result["entry"] = entry_data
        return None

    update_memory(mutate)
    return result["entry"]


def complete_task(task_id):
    result = {"task": None}

    def mutate(memory):
        remaining_tasks = []
        completed_task = None
        for task in memory.get("tasks", []):
            if task.get("id") == task_id:
                completed_task = task
                completed_task.setdefault("data", {})["status"] = "Completed"
                completed_task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                remaining_tasks.append(task)
        result["task"] = completed_task
        if completed_task is None:
            return NO_CHANGE
        memory["tasks"] = remaining_tasks
        memory.setdefault("completed_tasks", []).append(completed_task)
        return None

    update_memory(mutate)
    return result["task"]


def add_decision(title, reason, impact="Medium"):
    if not title.strip():
        return None
    return add_memory_entry("decisions", {"title": title, "reason": reason, "impact": impact})


def add_approval(title, department, risk_level="Medium", status="Pending"):
    if not title.strip():
        return None
    result = {}

    def mutate(memory):
        existing = _open_entry_exists(memory, "approvals", title, department)
        if existing:
            result["entry"] = existing
            return NO_CHANGE
        entry_data = {
            "id": generate_id("approvals"),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": {
                "title": title,
                "department": department,
                "risk_level": risk_level,
                "status": status,
            },
        }
        memory.setdefault("approvals", []).append(entry_data)
        result["entry"] = entry_data
        return None

    update_memory(mutate)
    return result["entry"]


def update_approval_status(approval_id, status):
    """Resolve an approval and any duplicate pending copies.

    Older LeadLens builds could create duplicate approval cards. Resolving only
    one copy made the same decision appear again immediately, which looked like
    the button had failed. This function resolves the selected approval and all
    pending duplicates with the same title and department in one atomic write.
    It also completes any pending task with the exact same title.
    """
    canonical_status = str(status).strip().title()
    allowed = {"Approved", "Rejected", "Pending"}
    if canonical_status not in allowed:
        raise ValueError("Invalid approval status")

    result = {"outcome": None}

    def mutate(memory):
        approvals = memory.get("approvals", [])
        selected = next(
            (item for item in approvals if item.get("id") == approval_id),
            None,
        )
        if selected is None:
            return NO_CHANGE

        selected_data = selected.setdefault("data", {})
        title_key = str(selected_data.get("title", "")).strip().casefold()
        department_key = str(selected_data.get("department", "")).strip().casefold()
        resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resolved_ids = []

        for approval in approvals:
            data = approval.setdefault("data", {})
            same_title = str(data.get("title", "")).strip().casefold() == title_key
            same_department = (
                str(data.get("department", "")).strip().casefold()
                == department_key
            )
            is_pending = str(data.get("status", "Pending")).strip().casefold() in {
                "pending",
                "open",
            }
            if approval.get("id") == approval_id or (
                same_title and same_department and is_pending
            ):
                data["status"] = canonical_status
                approval["resolved_at"] = resolved_at
                resolved_ids.append(approval.get("id"))

        # Complete exact-title tasks when an approval represents the task itself.
        if canonical_status == "Approved":
            remaining_tasks = []
            for task in memory.get("tasks", []):
                task_data = task.setdefault("data", {})
                task_title = str(task_data.get("title", "")).strip().casefold()
                task_department = str(task_data.get("department", "")).strip().casefold()
                if task_title == title_key and task_department == department_key:
                    task_data["status"] = "Completed"
                    task["completed_at"] = resolved_at
                    memory.setdefault("completed_tasks", []).append(task)
                else:
                    remaining_tasks.append(task)
            memory["tasks"] = remaining_tasks

        action_word = "approved" if canonical_status == "Approved" else "rejected"
        memory.setdefault("reports", []).append({
            "id": generate_id("reports"),
            "created_at": resolved_at,
            "data": {
                "type": "Activity",
                "title": f"Decision {action_word}: {selected_data.get('title', 'Approval')}",
                "department": selected_data.get("department", "System"),
                "activity_type": canonical_status,
                "details": (
                    f"Resolved {len(resolved_ids)} matching pending approval"
                    f"{'s' if len(resolved_ids) != 1 else ''}."
                ),
            },
        })
        memory.setdefault("reports", []).append({
            "id": generate_id("reports"),
            "created_at": resolved_at,
            "data": {
                "type": "Notification",
                "title": f"Approval {canonical_status}",
                "message": selected_data.get("title", "Approval decision updated"),
                "department": selected_data.get("department", "System"),
                "level": "Success" if canonical_status == "Approved" else "Warning",
                "status": "Unread",
            },
        })

        result["outcome"] = {
            "approval": selected,
            "resolved_ids": resolved_ids,
            "status": canonical_status,
        }
        return None

    update_memory(mutate)
    return result["outcome"]


def add_daily_log(summary):
    if not str(summary).strip():
        return None
    return add_memory_entry("daily_logs", {"summary": summary})


def backup_now(destination: str | os.PathLike | None = None) -> Path:
    """Write a timestamped backup and return its path.

    - SQLite backend: a true binary copy of the live database file, safe to
      call while the app is running (SQLite's own backup API takes a
      consistent snapshot even mid-write).
    - Postgres backend: a JSON export of the current data. Supabase and
      Neon both take their own automatic backups on the database itself;
      this is a convenient extra local copy, not a replacement for those.

    Intended to be wired up to a scheduled job (cron, GitHub Action, your
    hosting platform's job runner, etc.) once this is handling real
    customer data.
    """
    DATABASE_FOLDER.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if _using_postgres():
        if destination is None:
            destination = DATABASE_FOLDER / "backups" / f"leadlens-{stamp}.json"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(load_memory(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination

    ensure_database()
    if destination is None:
        destination = DATABASE_FOLDER / "backups" / f"leadlens-{stamp}.db"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_conn = sqlite3.connect(str(_db_file()))
    dest_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()
    return destination
