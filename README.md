# My SQLite Merger

Flask + SQLite web application for comparing and merging two SQLite databases.

## Features
* Compare a Patch SQLite database or Excel workbook with a Base SQLite database.
* Build an Excel patch from sheets named for Base tables, importing matching columns in Base column order.
* Preview Patch and Base tables, plus Merged tables after a successful merge.
* Automatically detect schema mismatches and view detailed schema reports.
* Detect table, column, and row additions, changes, and deletions.
* Ignore Base-only tables so they remain unchanged.
* Configure structure changes, row deletion, and row updates.
* Inspect row-level differences and filter tables by change type or wildcard name.
* Browse table lists and preview rows with pagination.
* Merge selected Patch changes into a copy of the Base database and download the result.

# Release Notes
### v1.1.0
* Release Date: 18 Oct 2026
Change history
* Patch file accepts both SQLite and Excel
* Only matching tables in patch and base is processed
* Preview included to display patch, base and merged databases

# Merge semantics

- Base SQLite database is copied to `merged.sqlite`.
- Patch SQLite database is treated as the desired final state.
- Tables existing only in Patch SQLite database are created/copied.
- Tables existing only in Base SQLite database are ignored and remain unchanged.
- Common tables:
  - Patch SQLite database columns missing in Base SQLite database are added.
  - Matching rows are updated from Patch SQLite database.
  - Patch-only rows are inserted.
  - Base SQLite database-only rows are deleted.
- Rows are matched by identical primary-key definitions.
- If neither table has a primary key, all common columns are used as row identity.
- If primary keys differ, row-level merge is skipped for that table to avoid unsafe changes.

## Run on Windows / WSL

```powershell
uv init
uv add Flask
uv run python app.py
```

Or with an existing environment:

```powershell
uv pip install -r requirements.txt
python app.py
```

Open:

http://127.0.0.1:5000

## Notes

This is a functional first version. It deliberately does not attempt destructive schema operations such as dropping Base database columns or changing SQLite column definitions because SQLite has important ALTER TABLE limitations. It also does not automatically merge indexes, triggers, views, foreign-key changes, or custom virtual tables.
