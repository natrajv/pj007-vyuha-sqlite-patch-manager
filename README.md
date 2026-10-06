# My SQLite Merger

Flask + SQLite web application for comparing and merging two SQLite databases.

## Merge semantics

- Base SQLite database is copied to `merged.sqlite`.
- Patch SQLite database is treated as the desired final state.
- Tables existing only in Patch SQLite database are created/copied.
- Tables existing only in Base SQLite database remain.
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
