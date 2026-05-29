# Changelog

## v6 (current)

### Day view redesign
- Renamed "Today" tab to "Day".
- Left and right columns are now equal width (50/50 split).
- Left column shows a full 24-hour time-slot schedule with hour labels.
- Timed events appear as absolutely-positioned blocks at their actual time slot; overlapping events are laid out in columns so nothing is hidden.
- All-day and multi-day events appear in a collapsible banner above the grid.
- Current-time indicator (red line) auto-scrolls into view on today.
- Date hero moved into the pane toolbar bar; cleaner layout.

### Multi-day events simplified
- Multi-day events are now forced to be all-day spans.  This eliminates the edge cases and display bugs that occurred when a multi-day event had time components.
- Checking "All day / multi-day" reveals the end-date field; the time picker is hidden.
- Timed events remain single-day.

### Category colors
- Each category now has an assigned color from a 10-color palette.
- Colors auto-adapt when switching themes — each palette entry has a per-theme hex value chosen for good contrast.
- Category buttons in the Tasks toolbar show a colored left border.
- Task cards, event chips, week/month items all use the per-category color.
- Color picker is accessible from the category modal (click the swatch).

### Category management improvements
- **Rename**: click a category name in the modal to edit it in-place.  All tasks and events are updated in the database.
- **Set default**: each category has a "☆ set default" button; the default is used for quick-capture and new task/event forms.
- **Uncategorized** category is always present and cannot be renamed or deleted.
- **Delete flow**: removing a category opens a confirmation dialog with two choices: move its tasks/events to Uncategorized, or delete them all.  Data is never silently lost.

### Notes: tabs and navigation
- Multiple notes can be open simultaneously as tabs above the editor.
- Clicking a `[[link]]` in Preview mode opens the linked note in a new adjacent tab.
- **Back (‹) / Forward (›)** buttons navigate the note visit history within the session (like a browser back button).
- Tab titles update when a note is renamed.

### Dark theme
- Default dark theme no longer uses pure black/white — backgrounds start at `#111318` for a softer look.
- Dark HC theme retains pure black for OLED/accessibility use.

### Other
- Quick capture uses the configured default category instead of hardcoded 'personal'.
- Backend: new endpoints `POST /api/config/rename-category` and `POST /api/config/delete-category`.
- Backend: `DEFAULT_CONFIG` now includes Uncategorized and color metadata.

---

## v5

- **Security**: added `esc()` helper; all user content escaped before `innerHTML` insertion.
- **Bug fix**: `tasks_for_day` pinned/rest comparison used object identity (always false) — fixed to use ID set.
- **`done_today` stat**: now uses `completed_at` column (set on status → done) instead of `created_at`.
- **FTS5 journal search**: replaced O(n) file scan with SQLite full-text search index.
- **Notes sidebar**: no longer refetches on every autosave; updates item in-place.
- **`completed_at` column** added to tasks table with safe migration.
- **Bare `except: pass`** replaced with typed exception catches throughout backend.
- Comments added throughout both files.
- README version reference fixed (was pointing to v3).

---

## v4

- Initial public release with Tasks, Events, Journal, Notes, recurring events, urgency scoring, and theme switching.
