#!/usr/bin/env python3
"""
GTD App v5 — Flask backend
Serves the SQLite-backed REST API consumed by static/index.html.

Data lives entirely in ./data/:
  gtd.db       — tasks, events, notes, journal metadata
  config.json  — user preferences (timezone, categories)
  journal/     — one plain .md file per day  (YYYY/MM/YYYY-MM-DD.md)
  notes/       — mirrored .md copies of each note  ({id}-{slug}.md)
"""

import os, json, re, calendar
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

from flask import Flask, g, jsonify, request, send_from_directory

# ── Paths ──────────────────────────────────────────────────────────────────
BASE        = Path(__file__).parent
DATA        = BASE / "data"
JOURNAL_DIR = DATA / "journal"
NOTES_DIR   = DATA / "notes"
DB_PATH     = DATA / "gtd.db"
CONFIG_FILE = DATA / "config.json"

# Create directories on first run
for d in [DATA, JOURNAL_DIR, NOTES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Hard cap: recurrences are never expanded more than 2 years into the future.
TWO_YEARS = 730

DEFAULT_CONFIG = {
    "timezone": "Europe/Rome",
    # Categories stored as objects: [{name, color}]
    # Old installs may have a plain string array — the frontend migrates on load.
    "categories": [
        {"name": "uncategorized", "color": "slate"},
        {"name": "work",          "color": "blue"},
        {"name": "personal",      "color": "violet"},
    ],
    "defaultCategory": "uncategorized",
}


# ── Config helpers ─────────────────────────────────────────────────────────

def load_config() -> dict:
    """Read config.json, back-filling any missing keys from DEFAULT_CONFIG."""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except (json.JSONDecodeError, OSError):
            pass  # fall through to default
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Database helpers ───────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """
    Return the per-request SQLite connection stored in Flask's 'g'.
    Opens and caches it on first call within each request context.
    """
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    """Close the SQLite connection at the end of every request."""
    db = g.pop("db", None)
    if db:
        db.close()


def init_db() -> None:
    """
    Create tables on first run and seed sample data when the tasks table
    is empty.  Also runs safe ALTER TABLE migrations for columns added
    after the initial schema was deployed.
    """
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    db.executescript("""
    CREATE TABLE IF NOT EXISTS tasks(
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      title           TEXT    NOT NULL,
      category        TEXT    DEFAULT 'personal',
      priority        INTEGER DEFAULT 3,
      effort          INTEGER DEFAULT 30,
      deadline        TEXT,
      scheduled       TEXT,
      tags            TEXT    DEFAULT '',
      status          TEXT    DEFAULT 'open',
      created_at      TEXT    DEFAULT (datetime('now')),
      completed_at    TEXT,                        -- set when status → 'done'
      deferred_until  TEXT,
      notes           TEXT    DEFAULT '',
      recur           TEXT    DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS events(
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      title       TEXT    NOT NULL,
      category    TEXT    DEFAULT 'personal',
      start_dt    TEXT    NOT NULL,
      end_dt      TEXT,
      all_day     INTEGER DEFAULT 0,
      recur       TEXT    DEFAULT '',
      recur_end   TEXT,
      exceptions  TEXT    DEFAULT '[]',
      color       TEXT    DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS notes(
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      title       TEXT    NOT NULL,
      body        TEXT    DEFAULT '',
      tags        TEXT    DEFAULT '',
      links       TEXT    DEFAULT '',
      created_at  TEXT    DEFAULT (datetime('now')),
      updated_at  TEXT    DEFAULT (datetime('now'))
    );

    -- FTS5 table for fast full-text search over journal entries.
    -- Columns: date (the ISO date string), body (entry text).
    CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts
      USING fts5(date UNINDEXED, body);

    CREATE TABLE IF NOT EXISTS journal_meta(
      date        TEXT PRIMARY KEY,
      tags        TEXT    DEFAULT '',
      word_count  INTEGER DEFAULT 0
    );
    """)

    # ── Safe migrations: add columns that may be missing in older DBs ──────
    for sql in [
        "ALTER TABLE events ADD COLUMN recur_end TEXT",
        "ALTER TABLE events ADD COLUMN exceptions TEXT DEFAULT '[]'",
        "ALTER TABLE tasks  ADD COLUMN completed_at TEXT",
    ]:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists — safe to ignore

    db.commit()

    # ── Seed sample data only when the tasks table is completely empty ──────
    if db.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"] == 0:
        _seed_sample_data(db)

    db.close()


def _seed_sample_data(db: sqlite3.Connection) -> None:
    """Insert a handful of realistic tasks and events for first-run demo."""
    today     = date.today().isoformat()
    tomorrow  = (date.today() + timedelta(days=1)).isoformat()
    next_week = (date.today() + timedelta(days=7)).isoformat()

    db.executemany(
        "INSERT INTO tasks(title,category,priority,effort,deadline,tags,status)"
        " VALUES(?,?,?,?,?,?,?)",
        [
            ("Reply to client emails",   "work",     4, 15, today,     "email,urgent", "open"),
            ("Finish project proposal",  "work",     5, 90, tomorrow,  "writing",      "open"),
            ("Buy groceries",            "personal", 3, 20, None,      "errands",      "open"),
            ("Call dentist",             "personal", 3,  5, None,      "health",       "open"),
            ("Review quarterly budget",  "work",     4, 45, next_week, "finance",      "open"),
            ("Read current book chapter","personal", 2, 30, None,      "reading",      "open"),
        ],
    )

    now     = datetime.now()
    mon_dt  = datetime.combine(
        date.today() - timedelta(days=date.today().weekday()),
        datetime.min.time(),
    ).replace(hour=10)

    db.executemany(
        "INSERT INTO events(title,category,start_dt,end_dt,all_day,recur,exceptions)"
        " VALUES(?,?,?,?,?,?,?)",
        [
            (
                "Team standup", "work",
                now.replace(hour=9,  minute=0,  second=0).isoformat()[:16],
                now.replace(hour=9,  minute=30, second=0).isoformat()[:16],
                0, "", "[]",
            ),
            (
                "Weekly team meeting", "work",
                mon_dt.isoformat()[:16],
                (mon_dt + timedelta(hours=1)).isoformat()[:16],
                0,
                json.dumps({"freq": "weekly", "interval": 1, "days": [0]}),
                "[]",
            ),
        ],
    )
    db.commit()


# ── Urgency scoring ────────────────────────────────────────────────────────

def compute_urgency(task: dict) -> float:
    """
    Return a float urgency score used to surface the most pressing tasks.

    Formula:  urgency = (priority × 2) × deadline_coefficient + age_bonus

    deadline_coefficient:
        overdue  → 10×    today    → 6×    ≤2 days → 4×
        ≤7 days  → 2.5×   ≤14 days → 1.5×  further → ~1×

    age_bonus: up to +1.5 for tasks that haven't been touched in 60+ days,
    so nothing rots at the bottom of the list forever.
    """
    priority = task.get("priority") or 3
    base     = priority * 2.0
    dc       = 1.0   # deadline coefficient, default = no deadline

    if task.get("deadline"):
        try:
            dl   = date.fromisoformat(task["deadline"])
            days = (dl - date.today()).days
            if days < 0:      dc = 10.0
            elif days == 0:   dc = 6.0
            elif days <= 2:   dc = 4.0
            elif days <= 7:   dc = 2.5
            elif days <= 14:  dc = 1.5
            else:             dc = 1.0 + max(0, (30 - days) / 60)
        except ValueError:
            pass

    # Age bonus: tasks sitting around for a long time bubble up gently
    age_days = 0
    if task.get("created_at"):
        try:
            created  = datetime.fromisoformat(task["created_at"]).date()
            age_days = (date.today() - created).days
        except ValueError:
            pass

    return round(base * dc + min(age_days / 60, 1.5), 2)


def task_to_dict(row) -> dict:
    """Convert a sqlite3.Row into a plain dict, adding the computed urgency score
    and expanding the comma-separated tags string into a list."""
    d = dict(row)
    d["urgency"] = compute_urgency(d)
    d["tags"]    = [t.strip() for t in (d.get("tags") or "").split(",") if t.strip()]
    return d


# ── Recurrence engine ──────────────────────────────────────────────────────

def nth_weekday(year: int, month: int, nth: int, weekday: int):
    """
    Return the date of the Nth occurrence of `weekday` (0=Mon … 6=Sun)
    in the given month/year, or None if it doesn't exist (e.g. 5th Monday).
    """
    first = date(year, month, 1)
    diff  = (weekday - first.weekday()) % 7
    result = first + timedelta(days=diff) + timedelta(weeks=nth - 1)
    return result if result.month == month else None


def expand_event_dates(ev: dict, from_date: date, to_date: date) -> list[str]:
    """
    Yield ISO date strings for all occurrences of a recurring event that
    fall within [from_date, to_date].

    Supports: daily, weekly (multi-day-of-week), monthly (by date or
    Nth weekday), and yearly patterns.

    Always respects:
      - ev['recur_end']   — hard end date for the series
      - ev['exceptions']  — list of ISO dates to skip (single edits)
      - TWO_YEARS cap     — never expands more than 2 years from today
    """
    if not ev.get("recur"):
        return []

    try:
        rule = json.loads(ev["recur"])
    except (json.JSONDecodeError, TypeError):
        return []

    start    = date.fromisoformat(ev["start_dt"][:10])
    freq     = rule.get("freq", "")
    interval = max(1, rule.get("interval", 1))

    # Apply global two-year cap then optional per-series end date
    cap     = date.today() + timedelta(days=TWO_YEARS)
    to_date = min(to_date, cap)

    if ev.get("recur_end"):
        try:
            to_date = min(to_date, date.fromisoformat(ev["recur_end"]))
        except ValueError:
            pass

    if from_date > to_date:
        return []

    # Parse the exception set (individual occurrence deletions/edits)
    try:
        exceptions = set(json.loads(ev.get("exceptions") or "[]"))
    except (json.JSONDecodeError, TypeError):
        exceptions = set()

    results = []

    def add(d: date) -> None:
        """Append date to results if it isn't in the exceptions list."""
        if d.isoformat() not in exceptions:
            results.append(d.isoformat())

    # ── Daily ──────────────────────────────────────────────────────────────
    if freq == "daily":
        days_since = max(0, (from_date - start).days)
        offset     = (interval - (days_since % interval)) % interval
        d          = from_date + timedelta(days=offset)
        iters      = 0
        while d <= to_date and iters < 1000:
            if d >= start:
                add(d)
            d     += timedelta(days=interval)
            iters += 1

    # ── Weekly (supports multiple days of week) ────────────────────────────
    elif freq == "weekly":
        days_of_week = rule.get("days", [start.weekday()])
        # Walk forward in interval-week steps from the aligned start week
        start_mon   = start     - timedelta(days=start.weekday())
        from_mon    = from_date - timedelta(days=from_date.weekday())
        wk_diff     = (from_mon - start_mon).days // 7
        aligned     = (wk_diff // interval) * interval
        cur_mon     = start_mon + timedelta(weeks=aligned)
        iters       = 0
        while cur_mon <= to_date + timedelta(days=6) and iters < 500:
            for wd in days_of_week:
                c = cur_mon + timedelta(days=wd)
                if c >= start and from_date <= c <= to_date:
                    add(c)
            cur_mon += timedelta(weeks=interval)
            iters   += 1

    # ── Monthly (same date, or Nth weekday) ────────────────────────────────
    elif freq == "monthly":
        by  = rule.get("by", "date")
        y, m = from_date.year, from_date.month
        # Step back one month so the loop catches the boundary month
        m -= 1
        if m < 1:
            m = 12; y -= 1
        for _ in range(300):
            if by == "date":
                try:
                    c = date(y, m, start.day)
                except ValueError:
                    # start.day doesn't exist in this month (e.g. Feb 30) →
                    # clamp to the last day of the month
                    c = date(y, m, calendar.monthrange(y, m)[1])
            else:
                # Nth weekday variant
                c = nth_weekday(y, m, rule.get("nth", 1), rule.get("day", start.weekday()))

            if c:
                if c > to_date:
                    break
                if c >= start and from_date <= c <= to_date:
                    add(c)

            m += 1
            if m > 12:
                m = 1; y += 1

    # ── Yearly ────────────────────────────────────────────────────────────
    elif freq == "yearly":
        y = start.year
        while y <= to_date.year + 1:
            try:
                c = date(y, start.month, start.day)
            except ValueError:
                # Feb 29 in a non-leap year → use Feb 28
                c = date(y, start.month, 28)
            if c > to_date:
                break
            if c >= start and from_date <= c <= to_date:
                add(c)
            y += interval

    return results


def events_in_range(db: sqlite3.Connection, from_iso: str, to_iso: str) -> list[dict]:
    """
    Return all events (non-recurring + expanded recurring occurrences)
    whose dates overlap [from_iso, to_iso], sorted by start_dt.
    """
    from_date = date.fromisoformat(from_iso)
    to_date   = date.fromisoformat(to_iso)

    # Non-recurring events: include multi-day events that OVERLAP the range
    rows = db.execute("""
        SELECT * FROM events
        WHERE (recur IS NULL OR recur = '')
          AND date(start_dt) <= :to
          AND (
                end_dt IS NULL OR end_dt = ''
                OR date(end_dt) >= :from
                OR date(start_dt) >= :from
              )
        ORDER BY start_dt
    """, {"to": to_iso, "from": from_iso}).fetchall()

    result = [dict(r) for r in rows]

    # Recurring events: expand each series and attach virtual occurrence dicts
    for row in db.execute(
        "SELECT * FROM events WHERE recur IS NOT NULL AND recur != '' AND date(start_dt) <= ?",
        [to_iso],
    ).fetchall():
        ev = dict(row)
        for occ_date in expand_event_dates(ev, from_date, to_date):
            occ = dict(ev)
            # Preserve the original time portion; only swap the date
            time_part      = ev["start_dt"][10:] if len(ev["start_dt"]) > 10 else "T00:00"
            occ["start_dt"] = occ_date + time_part
            if ev.get("end_dt") and len(ev["end_dt"]) > 10:
                occ["end_dt"] = occ_date + ev["end_dt"][10:]
            occ["is_recurring"] = True
            occ["recur_date"]   = occ_date
            result.append(occ)

    result.sort(key=lambda e: e.get("start_dt", ""))
    return result


# ── Tag extraction helpers ─────────────────────────────────────────────────

# Matches: #word, #multi-word-tag, #tag.sub, #"quoted tag with spaces"
TAG_RE = re.compile(r'#"([^"]+)"|#([A-Za-z][A-Za-z0-9_\-\.]*)')


def extract_tags(text: str) -> list[str]:
    """Return a de-duplicated list of all #tags found in `text`."""
    found = []
    for m in TAG_RE.finditer(text or ""):
        # Group 1 = quoted tag (keep as-is), group 2 = bare word (strip trailing punctuation)
        tag = m.group(1).strip() if m.group(1) else m.group(2).rstrip(".-").strip()
        if tag and tag not in found:
            found.append(tag)
    return found


def merge_tags(explicit_str: str, body: str) -> list[str]:
    """
    Merge explicit comma-separated tags with tags extracted from body text.
    Preserves insertion order and removes duplicates.
    """
    explicit  = [t.strip() for t in (explicit_str or "").split(",") if t.strip()]
    body_tags = extract_tags(body)
    return list(dict.fromkeys(explicit + body_tags))


# ── Note file mirroring ────────────────────────────────────────────────────

def _note_slug(nid: int, title: str) -> str:
    """Return a safe filename for a note's .md mirror: '{id}-{slug}.md'."""
    slug = re.sub(r"[^\w\s-]", "", (title or "untitled").lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")[:40]
    return f"{nid}-{slug}.md"


def mirror_note(nid: int, title: str, body: str, tags_str: str) -> None:
    """
    Write (or overwrite) a plain-text .md copy of a note so the data/notes/
    directory stays grep-able even without the app running.
    Removes any previous slug file for this note ID first.
    """
    for old in NOTES_DIR.glob(f"{nid}-*.md"):
        old.unlink(missing_ok=True)

    content = (
        f"---\ntitle: {title}\ntags: {tags_str}\n"
        f"updated: {datetime.now().isoformat()[:10]}\n---\n\n{body or ''}"
    )
    (NOTES_DIR / _note_slug(nid, title)).write_text(content)


# ── Journal metadata ───────────────────────────────────────────────────────

def update_journal_meta(db: sqlite3.Connection, iso: str, body: str) -> None:
    """
    Upsert journal_meta and keep the FTS5 index in sync whenever a journal
    entry is saved.  Both are used by the search endpoint.
    """
    tags = ",".join(extract_tags(body))
    wc   = len((body or "").split())

    db.execute(
        """INSERT INTO journal_meta(date, tags, word_count)
           VALUES(?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             tags       = excluded.tags,
             word_count = excluded.word_count""",
        [iso, tags, wc],
    )

    # Keep the FTS index current: delete old row then re-insert
    db.execute("DELETE FROM journal_fts WHERE date = ?", [iso])
    db.execute("INSERT INTO journal_fts(date, body) VALUES(?, ?)", [iso, body or ""])
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# API — Tasks
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    """
    Return tasks filtered by status, category, effort range and sorted by
    the requested key.  Deferred tasks are hidden until their deferral date.
    """
    db      = get_db()
    today_s = date.today().isoformat()

    status     = request.args.get("status",     "open")
    category   = request.args.get("category",   "")
    effort_min = request.args.get("effort_min", "")
    effort_max = request.args.get("effort_max", "")
    sort       = request.args.get("sort",       "urgency")

    sql    = "SELECT * FROM tasks WHERE 1=1"
    params = []

    if status:
        sql += " AND status = ?"; params.append(status)
    if category:
        sql += " AND category = ?"; params.append(category)
    if effort_min != "":
        sql += " AND effort >= ?"; params.append(int(effort_min))
    if effort_max != "":
        sql += " AND effort <= ?"; params.append(int(effort_max))

    # Hide tasks deferred into the future
    sql += " AND (deferred_until IS NULL OR deferred_until <= ?)"; params.append(today_s)

    rows  = db.execute(sql, params).fetchall()
    tasks = [task_to_dict(r) for r in rows]

    # Sort in Python so we can use the computed urgency field
    if   sort == "deadline":  tasks.sort(key=lambda t: (t.get("deadline") or "9999",))
    elif sort == "priority":  tasks.sort(key=lambda t: -t.get("priority", 3))
    elif sort == "effort":    tasks.sort(key=lambda t:  t.get("effort") or 0)
    elif sort == "created":   tasks.sort(key=lambda t:  t.get("created_at") or "", reverse=True)
    elif sort == "title":     tasks.sort(key=lambda t:  t.get("title", "").lower())
    else:                     tasks.sort(key=lambda t: -t["urgency"])  # default: urgency

    return jsonify(tasks)


@app.route("/api/tasks/day/<iso_date>", methods=["GET"])
def tasks_for_day(iso_date: str):
    """
    Return up to 7 tasks for the Today view.
    Tasks explicitly scheduled or due on `iso_date` are pinned first;
    the remainder are the highest-urgency open tasks.
    """
    db = get_db()

    rows  = db.execute(
        "SELECT * FROM tasks WHERE status = 'open'"
        " AND (deferred_until IS NULL OR deferred_until <= ?)",
        [iso_date],
    ).fetchall()
    tasks = [task_to_dict(r) for r in rows]

    # Pin tasks scheduled/due today; fill remaining slots by urgency
    pinned_ids = {t["id"] for t in tasks if t.get("scheduled") == iso_date or t.get("deadline") == iso_date}
    pinned     = [t for t in tasks if t["id"] in pinned_ids]
    rest       = sorted([t for t in tasks if t["id"] not in pinned_ids], key=lambda t: -t["urgency"])

    return jsonify((pinned + rest)[:7])


@app.route("/api/tasks", methods=["POST"])
def create_task():
    """Create a new task.  Returns the created task dict with urgency."""
    db   = get_db()
    data = request.json

    cur = db.execute(
        "INSERT INTO tasks(title,category,priority,effort,deadline,scheduled,tags,notes,recur)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        [
            data.get("title", ""),
            data.get("category", "personal"),
            data.get("priority", 3),
            data.get("effort", 30),
            data.get("deadline")  or None,
            data.get("scheduled") or None,
            ",".join(data.get("tags", [])),
            data.get("notes", ""),
            data.get("recur", ""),
        ],
    )
    db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", [cur.lastrowid]).fetchone()
    return jsonify(task_to_dict(row)), 201


@app.route("/api/tasks/<int:tid>", methods=["PATCH"])
def update_task(tid: int):
    """
    Partial update of a task.  Only fields in `allowed` may be changed.
    Automatically sets completed_at when status transitions to 'done' and
    clears it when a task is reopened.
    """
    db   = get_db()
    data = request.json

    allowed = {"title", "category", "priority", "effort", "deadline",
               "scheduled", "tags", "status", "deferred_until", "notes", "recur"}

    sets, vals = [], []
    for k in allowed:
        if k not in data:
            continue
        v = data[k]
        if k == "tags" and isinstance(v, list):
            v = ",".join(v)
        sets.append(f"{k} = ?")
        vals.append(v)

    if not sets:
        return jsonify({"error": "no valid fields provided"}), 400

    # Manage completed_at automatically based on status changes
    if "status" in data:
        if data["status"] == "done":
            sets.append("completed_at = ?")
            vals.append(datetime.now().isoformat())
        elif data["status"] == "open":
            sets.append("completed_at = ?")
            vals.append(None)

    vals.append(tid)
    db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)
    db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", [tid]).fetchone()
    return jsonify(task_to_dict(row))


@app.route("/api/tasks/<int:tid>", methods=["DELETE"])
def delete_task(tid: int):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ?", [tid])
    db.commit()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
# API — Events
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/events", methods=["GET"])
def list_events():
    db = get_db()
    from_iso = request.args.get("from", date.today().isoformat())
    to_iso   = request.args.get("to",   (date.today() + timedelta(days=7)).isoformat())
    return jsonify(events_in_range(db, from_iso, to_iso))


@app.route("/api/events/day/<iso_date>", methods=["GET"])
def events_for_day(iso_date: str):
    """
    Return events for a single day, annotating each multi-day timed event
    with its day_role: 'start' | 'middle' | 'end' | 'single'.
    The frontend uses this to render the correct portion on each day.
    """
    db  = get_db()
    evs = events_in_range(db, iso_date, iso_date)
    for ev in evs:
        start_d = ev["start_dt"][:10]
        end_d   = ev["end_dt"][:10] if ev.get("end_dt") else start_d
        if start_d == end_d:
            ev["day_role"] = "single"
        elif iso_date == start_d:
            ev["day_role"] = "start"
        elif iso_date == end_d:
            ev["day_role"] = "end"
        else:
            ev["day_role"] = "middle"
    return jsonify(evs)


@app.route("/api/events", methods=["POST"])
def create_event():
    db   = get_db()
    data = request.json

    recur = data.get("recur", "")
    if isinstance(recur, dict):
        recur = json.dumps(recur)

    cur = db.execute(
        "INSERT INTO events(title,category,start_dt,end_dt,all_day,recur,exceptions,color)"
        " VALUES(?,?,?,?,?,?,'[]',?)",
        [
            data["title"],
            data.get("category", "personal"),
            data["start_dt"],
            data.get("end_dt", ""),
            1 if data.get("all_day") else 0,
            recur,
            data.get("color", ""),
        ],
    )
    db.commit()

    row = db.execute("SELECT * FROM events WHERE id = ?", [cur.lastrowid]).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/events/<int:eid>", methods=["PATCH"])
def patch_event(eid: int):
    db   = get_db()
    data = request.json

    allowed = {"title", "category", "start_dt", "end_dt", "all_day",
               "recur", "recur_end", "exceptions", "color"}

    sets, vals = [], []
    for k in allowed:
        if k not in data:
            continue
        v = data[k]
        if k == "recur"      and isinstance(v, dict): v = json.dumps(v)
        if k == "exceptions" and isinstance(v, list): v = json.dumps(v)
        sets.append(f"{k} = ?")
        vals.append(v)

    if not sets:
        return jsonify({"error": "no valid fields provided"}), 400

    vals.append(eid)
    db.execute(f"UPDATE events SET {', '.join(sets)} WHERE id = ?", vals)
    db.commit()

    row = db.execute("SELECT * FROM events WHERE id = ?", [eid]).fetchone()
    return jsonify(dict(row))


@app.route("/api/events/<int:eid>", methods=["DELETE"])
def delete_event(eid: int):
    db = get_db()
    db.execute("DELETE FROM events WHERE id = ?", [eid])
    db.commit()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
# API — Journal
# ═══════════════════════════════════════════════════════════════════════════

def journal_path(iso: str) -> Path:
    """Return the Path for a journal entry, creating parent dirs as needed."""
    y, m, _ = iso.split("-")
    p = JOURNAL_DIR / y / m
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{iso}.md"


@app.route("/api/journal/<iso_date>", methods=["GET"])
def get_journal(iso_date: str):
    p    = journal_path(iso_date)
    body = p.read_text() if p.exists() else ""
    return jsonify({"date": iso_date, "body": body, "exists": p.exists()})


@app.route("/api/journal/<iso_date>", methods=["PUT"])
def save_journal(iso_date: str):
    """Save a journal entry and keep the FTS + metadata tables in sync."""
    db   = get_db()
    body = request.json.get("body", "")
    journal_path(iso_date).write_text(body)
    update_journal_meta(db, iso_date, body)
    return jsonify({"ok": True, "tags": extract_tags(body)})


@app.route("/api/journal/search", methods=["GET"])
def search_journal():
    """
    Full-text search over journal entries using the FTS5 index.
    Falls back to the file-scan method when the FTS table is empty
    (e.g. on first run before any entry has been saved since the upgrade).
    """
    db = get_db()
    q  = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    # Try FTS5 first (fast)
    try:
        fts_rows = db.execute(
            "SELECT date, snippet(journal_fts, 1, '', '', '…', 15) AS snippet"
            " FROM journal_fts WHERE body MATCH ?"
            " ORDER BY rank LIMIT 20",
            [q],
        ).fetchall()
        if fts_rows:
            return jsonify([{"date": r["date"], "snippet": r["snippet"]} for r in fts_rows])
    except sqlite3.OperationalError:
        pass  # FTS index not populated yet — fall through

    # Fallback: file scan (only happens before first save after upgrade)
    results = []
    for f in sorted(JOURNAL_DIR.rglob("*.md"), reverse=True):
        text = f.read_text()
        if q.lower() in text.lower():
            idx     = text.lower().find(q.lower())
            snippet = text[max(0, idx - 60) : idx + 100].replace("\n", " ")
            results.append({"date": f.stem, "snippet": snippet})
    return jsonify(results[:20])


@app.route("/api/journal/dates", methods=["GET"])
def journal_dates():
    return jsonify(sorted([f.stem for f in JOURNAL_DIR.rglob("*.md")], reverse=True))


@app.route("/api/journal/tags", methods=["GET"])
def journal_tags():
    """Return the top 30 most-used tags and the 15 most-recently-used tags
    across all journal entries."""
    db = get_db()

    # Count tag frequency across all entries
    rows   = db.execute("SELECT tags FROM journal_meta WHERE tags != ''").fetchall()
    counts = {}
    for r in rows:
        for t in (r["tags"] or "").split(","):
            t = t.strip()
            if t:
                counts[t] = counts.get(t, 0) + 1

    # Collect most-recently-used tags (preserve order)
    recent_rows = db.execute(
        "SELECT tags FROM journal_meta WHERE tags != '' ORDER BY date DESC LIMIT 10"
    ).fetchall()
    recent = []
    for r in recent_rows:
        for t in (r["tags"] or "").split(","):
            t = t.strip()
            if t and t not in recent:
                recent.append(t)

    return jsonify({
        "top":    sorted(counts, key=lambda t: -counts[t])[:30],
        "recent": recent[:15],
    })


# ═══════════════════════════════════════════════════════════════════════════
# API — Notes
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/notes", methods=["GET"])
def list_notes():
    """Search notes by free-text query and/or tag filter."""
    db     = get_db()
    q      = request.args.get("q",   "").lower()
    tag    = request.args.get("tag", "")
    sql    = "SELECT * FROM notes WHERE 1=1"
    params = []

    if q:
        sql += " AND (lower(title) LIKE ? OR lower(body) LIKE ? OR lower(tags) LIKE ?)"
        params += [f"%{q}%"] * 3

    if tag:
        # Match both the comma-separated tags field and inline #tags in the body
        sql += " AND ((',' || tags || ',') LIKE ? OR body LIKE ?)"
        params += [f"%,{tag},%", f"%#{tag}%"]

    sql += " ORDER BY updated_at DESC"
    return jsonify([dict(r) for r in db.execute(sql, params).fetchall()])


@app.route("/api/notes/titles", methods=["GET"])
def note_titles():
    """Return id/title/updated_at for autocomplete (lightweight — no body)."""
    db    = get_db()
    q     = request.args.get("q", "").lower()
    rows  = db.execute(
        "SELECT id, title, updated_at FROM notes ORDER BY updated_at DESC"
    ).fetchall()
    items = [dict(r) for r in rows]
    if q:
        items = [i for i in items if q in i["title"].lower()]
    return jsonify(items[:20])


@app.route("/api/notes", methods=["POST"])
def create_note():
    db   = get_db()
    data = request.json
    now  = datetime.now().isoformat()
    body = data.get("body", "")

    # Merge explicit tags (from the tag input) with #tags extracted from body
    explicit = data.get("tags", [])
    if isinstance(explicit, str):
        explicit = [t.strip() for t in explicit.split(",") if t.strip()]
    all_tags = list(dict.fromkeys(explicit + extract_tags(body)))

    cur = db.execute(
        "INSERT INTO notes(title,body,tags,links,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        [
            data.get("title", "Untitled"),
            body,
            ",".join(all_tags),
            ",".join(data.get("links", [])),
            now, now,
        ],
    )
    db.commit()

    row = dict(db.execute("SELECT * FROM notes WHERE id = ?", [cur.lastrowid]).fetchone())
    mirror_note(row["id"], row["title"], row["body"], row["tags"])
    return jsonify(row), 201


@app.route("/api/notes/<int:nid>", methods=["PUT"])
def update_note(nid: int):
    db   = get_db()
    data = request.json
    now  = datetime.now().isoformat()
    body = data.get("body", "")

    explicit = data.get("tags", [])
    if isinstance(explicit, str):
        explicit = [t.strip() for t in explicit.split(",") if t.strip()]
    elif isinstance(explicit, list):
        # Strip leading '#' that the frontend might include
        explicit = [t.strip().lstrip("#") for t in explicit if t.strip()]
    all_tags = list(dict.fromkeys(explicit + extract_tags(body)))

    links = (
        ",".join(data.get("links", []))
        if isinstance(data.get("links"), list)
        else data.get("links", "")
    )

    db.execute(
        "UPDATE notes SET title=?,body=?,tags=?,links=?,updated_at=? WHERE id=?",
        [data.get("title", "Untitled"), body, ",".join(all_tags), links, now, nid],
    )
    db.commit()

    row = dict(db.execute("SELECT * FROM notes WHERE id = ?", [nid]).fetchone())
    mirror_note(nid, row["title"], row["body"], row["tags"])
    return jsonify(row)


@app.route("/api/notes/<int:nid>/rename", methods=["POST"])
def rename_note(nid: int):
    """
    Propagate a title change to all [[OldTitle]] links in other notes and
    journal entries so backlinks stay consistent after a rename.
    """
    db   = get_db()
    data = request.json
    old  = data.get("old_title", "")
    new  = data.get("new_title", "")

    if not old or not new or old == new:
        return jsonify({"ok": True, "updated": 0})

    old_link = f"[[{old}]]"
    new_link = f"[[{new}]]"
    updated  = 0

    # Update links in other notes
    for n in db.execute(
        "SELECT id, title, body, tags FROM notes WHERE id != ? AND body LIKE ?",
        [nid, f"%{old_link}%"],
    ).fetchall():
        new_body = n["body"].replace(old_link, new_link)
        db.execute(
            "UPDATE notes SET body = ?, updated_at = ? WHERE id = ?",
            [new_body, datetime.now().isoformat(), n["id"]],
        )
        mirror_note(n["id"], n["title"], new_body, n["tags"])
        updated += 1

    # Update links in journal files
    for f in JOURNAL_DIR.rglob("*.md"):
        text = f.read_text()
        if old_link in text:
            f.write_text(text.replace(old_link, new_link))
            updated += 1

    db.commit()
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/notes/<int:nid>", methods=["DELETE"])
def delete_note(nid: int):
    db = get_db()
    for f in NOTES_DIR.glob(f"{nid}-*.md"):
        f.unlink(missing_ok=True)
    db.execute("DELETE FROM notes WHERE id = ?", [nid])
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/notes/tags", methods=["GET"])
def all_note_tags():
    """Return top-30 and 15 most-recent tags across all notes."""
    db     = get_db()
    rows   = db.execute("SELECT tags, body FROM notes").fetchall()
    counts = {}
    for r in rows:
        for t in merge_tags(r["tags"], r["body"]):
            if t:
                counts[t] = counts.get(t, 0) + 1

    recent_rows = db.execute(
        "SELECT tags, body FROM notes ORDER BY updated_at DESC LIMIT 10"
    ).fetchall()
    recent = []
    for r in recent_rows:
        for t in merge_tags(r["tags"], r["body"]):
            if t and t not in recent:
                recent.append(t)

    return jsonify({
        "top":    sorted(counts, key=lambda t: -counts[t])[:30],
        "recent": recent[:15],
    })


# ═══════════════════════════════════════════════════════════════════════════
# API — Config & Stats
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["PUT"])
def put_config():
    cfg = load_config()
    cfg.update(request.json)
    save_config(cfg)
    return jsonify(cfg)


@app.route("/api/stats", methods=["GET"])
def stats():
    """
    Header stats: open task count, overdue count, and tasks completed today.
    'done_today' uses completed_at (set automatically on status→done) so it
    correctly counts tasks *finished* today regardless of when they were created.
    """
    db      = get_db()
    today_s = date.today().isoformat()
    return jsonify({
        "open":       db.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'open'"
        ).fetchone()["c"],
        "overdue":    db.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'open' AND deadline < ?",
            [today_s],
        ).fetchone()["c"],
        "done_today": db.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'done'"
            " AND date(completed_at) = ?",
            [today_s],
        ).fetchone()["c"],
    })


@app.route("/api/config/rename-category", methods=["POST"])
def rename_category():
    """
    Rename a category everywhere: config, tasks, and events.
    The frontend updates its local state; this call keeps the DB in sync.
    """
    db   = get_db()
    data = request.json
    old  = data.get("old", "").strip()
    new  = data.get("new", "").strip()

    if not old or not new or old == new or old == "uncategorized":
        return jsonify({"ok": False, "error": "invalid names"}), 400

    db.execute("UPDATE tasks  SET category = ? WHERE category = ?", [new, old])
    db.execute("UPDATE events SET category = ? WHERE category = ?", [new, old])
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/config/delete-category", methods=["POST"])
def delete_category():
    """
    Delete a category from tasks and events.

    action='move'  — reassign all tasks/events to 'uncategorized'
    action='purge' — delete all tasks and events in this category
    """
    db     = get_db()
    data   = request.json
    name   = data.get("name", "").strip()
    action = data.get("action", "move")

    if not name or name == "uncategorized":
        return jsonify({"ok": False, "error": "cannot delete uncategorized"}), 400

    if action == "purge":
        db.execute("DELETE FROM tasks  WHERE category = ?", [name])
        db.execute("DELETE FROM events WHERE category = ?", [name])
    else:  # move
        db.execute("UPDATE tasks  SET category = 'uncategorized' WHERE category = ?", [name])
        db.execute("UPDATE events SET category = 'uncategorized' WHERE category = ?", [name])

    db.commit()
    return jsonify({"ok": True})


# ── Static file serving ────────────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(BASE / "static", "index.html")


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("GTD at http://localhost:5000")
    app.run(debug=False, port=5000)
