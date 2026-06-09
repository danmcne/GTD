# GTD — Personal Productivity App

GTD has been superseded by planr: https://github.com/danmcne/planr

The idea is almost identical, just a name change.


A self-hosted, local-first Getting Things Done app for Ubuntu.  
Everything runs on your machine at `http://localhost:5000`.  
All data is stored in plain files and SQLite — no cloud, no accounts.

## Quick Start

```bash
tar -xzf gtd-app-v6.tar.gz
cd gtd
bash install.sh
```

Open `http://localhost:5000` in your browser.

## What It Does

### ◎ Day
Your daily cockpit.  The left panel shows a time-slot schedule of the day's events; the right panel lists the top 7 highest-urgency open tasks.

- **Time grid**: timed events appear as positioned blocks at their actual hour; all-day and multi-day events appear in a banner above the grid; the current-time line scrolls into view automatically.
- **Quick buttons** above the pane step through today, tomorrow, and the next 3 days; ‹/› arrows navigate any day.
- **Click any event** to edit it; **+ Event** adds one for that day.
- **Task cards**: ☑ marks done, ⤵ defers to tomorrow, clicking opens the edit modal.
- **Quick capture**: type in the header bar and press Enter.

### ▦ Week
Seven-column calendar week (Mon–Sun).  Click any day header to jump to the Day view.

### ▣ Month
Full month grid.  Click any cell to jump to the Day view.

### ☰ Tasks
Full task list with filtering and sorting.

- **Filters**: category (with color-coded buttons), status (open/done), effort range, text search.
- **Sort**: urgency (default), deadline, priority, effort, created, title.
- **Urgency formula**: `(priority × 2) × deadline_coefficient + age_bonus`.

| Days to deadline | Coefficient |
|-----------------|-------------|
| Overdue | 10× |
| Today | 6× |
| ≤ 2 days | 4× |
| ≤ 7 days | 2.5× |
| ≤ 14 days | 1.5× |
| Further | ~1× |

`age_bonus` adds up to 1.5 points for tasks sitting untouched for 60+ days.

### ✏ Journal
One entry per day, stored as plain `.md` files (`data/journal/YYYY/MM/YYYY-MM-DD.md`).

- Auto-saves (1.2 s debounce).
- Full-text search using SQLite FTS5 (fast).
- Inline `#tags`, `[[Note links]]` with autocomplete, Preview mode.

### ◫ Notes
Searchable, tagged notes stored in SQLite and mirrored to `data/notes/`.

- **Tabs**: open multiple notes side-by-side; each tab is independently editable.
- **Back / Forward** buttons navigate your visit history within the session (like a browser).
- **`[[Note Title]]` links** — type `[[` for live autocomplete.  Clicking a link in Preview mode opens the target note in a new tab.
- Renaming a note propagates the change to all `[[links]]` in other notes and journal entries.
- Inline `#tags` and explicit tag input; top/recent tag panel for quick insertion.

## Categories

Default categories: **Uncategorized**, **Work**, **Personal**.

Open the **+ Category** button in the Tasks toolbar to manage categories:

- **Add** a new category (name auto-slugged).
- **Rename** by clicking the name and editing in-place.
- **Change color** by clicking the color swatch; 10 colors available, auto-adapted to the active theme.
- **Set default** — the category pre-selected for new tasks and events.
- **Remove** — choose to move its tasks/events to Uncategorized, or delete them all.

Deleting a category never silently destroys data; you are always asked what to do.

## Multi-day and All-day Events

Check **All day / multi-day** when creating an event to enable the end-date field.  
Multi-day events are always stored as all-day spans (no time component) and appear in the all-day banner in the Day view, and in the multi-day strip in the Week view.  
Timed events are always single-day.

## Recurring Events

| Pattern | Example |
|---------|---------
| Daily / every N days | Every 3 days |
| Weekly | Every Monday and Wednesday |
| Monthly — same date | 15th of every month |
| Monthly — same weekday | 2nd Thursday |
| Yearly | Annual review on Jan 1 |

Recurrences are computed dynamically (up to 2 years ahead).  
Click a recurring event to edit **this occurrence**, **this and future**, or **all occurrences**.

## Themes

Click the theme button (top-right) to cycle:

- **◑ Dark** — soft dark, comfortable for long sessions (no pure black)
- **● Dark HC** — high-contrast dark (pure black, OLED-friendly)
- **○ Light** — bright
- **◎ Light HC** — maximum contrast

Category colors automatically adapt to the active theme.

## File Layout

```
~/gtd/
├── app.py              # Flask backend
├── static/
│   └── index.html      # Single-page frontend
├── install.sh
├── README.md
├── CHANGELOG.md
└── data/
    ├── gtd.db          # Tasks, events, notes (SQLite)
    ├── config.json     # Categories, timezone, defaults
    ├── journal/        # YYYY/MM/YYYY-MM-DD.md
    └── notes/          # {id}-{slug}.md mirrors
```

## Backup

```bash
cp -r ~/gtd/data ~/gtd-backup-$(date +%Y%m%d)
```

## Service Management

```bash
systemctl --user status gtd
systemctl --user restart gtd
systemctl --user stop gtd
journalctl --user -u gtd -f
```

## Updating

```bash
cp new-app.py ~/gtd/app.py
cp new-index.html ~/gtd/static/index.html
systemctl --user restart gtd
```

Data in `~/gtd/data/` is never touched by updates.
