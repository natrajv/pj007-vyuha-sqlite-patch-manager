import sqlite3
from pathlib import Path


def connect(path):
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def tables(conn):
    rows = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()
    return [r[0] for r in rows]


def columns(conn, table):
    return conn.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34)*2)}")').fetchall()


def pk_columns(conn, table):
    return [r[1] for r in sorted(columns(conn, table), key=lambda x: x[5]) if r[5] > 0]


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def row_key(row, pk_indexes):
    return tuple(row[i] for i in pk_indexes)


def compare_table(source_conn, target_conn, table):
    scols = columns(source_conn, table)
    tcols = columns(target_conn, table)

    scol_names = [r[1] for r in scols]
    tcol_names = [r[1] for r in tcols]

    added_cols = sorted(set(scol_names) - set(tcol_names))
    deleted_cols = sorted(set(tcol_names) - set(scol_names))

    changed_cols = []
    common_cols = sorted(set(scol_names) & set(tcol_names))
    smap = {r[1]: r for r in scols}
    tmap = {r[1]: r for r in tcols}
    for c in common_cols:
        # Compare type, NOT NULL, default and PK position.
        if smap[c][2:] != tmap[c][2:]:
            changed_cols.append(c)

    spk = pk_columns(source_conn, table)
    tpk = pk_columns(target_conn, table)

    # If no PK exists, use all common columns as a conservative row identity.
    if spk and spk == tpk:
        key_cols = spk
    elif not spk and not tpk:
        key_cols = [c for c in scol_names if c in tcol_names]
    else:
        key_cols = []

    if not key_cols:
        return {
            "name": table,
            "added": 0,
            "changed": 0,
            "deleted": 0,
            "source_rows": source_conn.execute(
                f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0],
            "target_rows": target_conn.execute(
                f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0],
            "added_columns": added_cols,
            "changed_columns": changed_cols,
            "deleted_columns": deleted_cols,
            "row_identity": None,
        }

    common = [c for c in scol_names if c in tcol_names]
    source_sql = f"SELECT {','.join(quote_ident(c) for c in common)} FROM {quote_ident(table)}"
    target_sql = source_sql

    sr = source_conn.execute(source_sql).fetchall()
    tr = target_conn.execute(target_sql).fetchall()

    sidx = {c: i for i, c in enumerate(common)}
    pk_idx = [sidx[c] for c in key_cols]

    sdict = {row_key(r, pk_idx): r for r in sr}
    tdict = {row_key(r, pk_idx): r for r in tr}

    added = set(sdict) - set(tdict)
    deleted = set(tdict) - set(sdict)
    changed = set()

    for k in set(sdict) & set(tdict):
        if sdict[k] != tdict[k]:
            changed.add(k)

    return {
        "name": table,
        "added": len(added),
        "changed": len(changed),
        "deleted": len(deleted),
        "source_rows": len(sr),
        "target_rows": len(tr),
        "added_columns": added_cols,
        "changed_columns": changed_cols,
        "deleted_columns": deleted_cols,
        "row_identity": key_cols,
    }



def _schema_column_definition(row):
    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    return {
        "name": row[1],
        "type": row[2] or "",
        "notnull": bool(row[3]),
        "default": row[4],
        "pk": row[5],
    }


def compare_schema(source_path, target_path):
    """Compare the structural schema of Patch and Base SQLite databases.

    The comparison covers user tables, column order/name, declared type,
    NOT NULL, default value, and primary-key position. Row data is ignored.
    """
    with connect(source_path) as s, connect(target_path) as t:
        patch_tables = set(tables(s))
        base_tables = set(tables(t))

        tables_added = sorted(patch_tables - base_tables)
        tables_deleted = sorted(base_tables - patch_tables)
        table_mismatches = []

        for name in sorted(patch_tables & base_tables):
            pc = [_schema_column_definition(r) for r in columns(s, name)]
            bc = [_schema_column_definition(r) for r in columns(t, name)]
            mismatches = []

            if len(pc) != len(bc):
                mismatches.append({
                    "kind": "column_count",
                    "message": f"Column count differs: Patch {len(pc)}, Base {len(bc)}."
                })

            max_len = max(len(pc), len(bc))
            for i in range(max_len):
                if i >= len(pc):
                    mismatches.append({
                        "kind": "column_missing_in_patch",
                        "column": bc[i]["name"],
                        "message": f'Column "{bc[i]["name"]}" exists in Base but not in Patch.'
                    })
                    continue
                if i >= len(bc):
                    mismatches.append({
                        "kind": "column_missing_in_base",
                        "column": pc[i]["name"],
                        "message": f'Column "{pc[i]["name"]}" exists in Patch but not in Base.'
                    })
                    continue

                a, b = pc[i], bc[i]
                differences = []
                if a["name"] != b["name"]:
                    differences.append(f'name Patch="{a["name"]}" Base="{b["name"]}"')
                if a["type"].upper() != b["type"].upper():
                    differences.append(f'type Patch="{a["type"] or "(none)"}" Base="{b["type"] or "(none)"}"')
                if a["notnull"] != b["notnull"]:
                    differences.append(f'NOT NULL Patch={"Yes" if a["notnull"] else "No"} Base={"Yes" if b["notnull"] else "No"}')
                if a["default"] != b["default"]:
                    differences.append(f'default Patch={a["default"]!r} Base={b["default"]!r}')
                if a["pk"] != b["pk"]:
                    differences.append(f'PK position Patch={a["pk"]} Base={b["pk"]}')
                if differences:
                    mismatches.append({
                        "kind": "column_definition",
                        "column": a["name"],
                        "position": i + 1,
                        "message": "; ".join(differences),
                    })

            if mismatches:
                table_mismatches.append({"name": name, "mismatches": mismatches})

        return {
            "matches": not tables_added and not tables_deleted and not table_mismatches,
            "tables_added": tables_added,
            "tables_deleted": tables_deleted,
            "table_mismatches": table_mismatches,
        }


def compare_databases(source_path, target_path):
    with connect(source_path) as s, connect(target_path) as t:
        st = set(tables(s))
        tt = set(tables(t))

        table_added = sorted(st - tt)
        table_deleted = sorted(tt - st)
        common = sorted(st & tt)

        table_details = []

        for name in table_added:
            count = s.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]
            table_details.append({
                "name": name, "added": count, "changed": 0, "deleted": 0,
                "source_rows": count, "target_rows": 0,
                "added_columns": [r[1] for r in columns(s, name)],
                "changed_columns": [], "deleted_columns": [],
                "row_identity": pk_columns(s, name) or None,
                "table_status": "added"
            })

        for name in table_deleted:
            count = t.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]
            table_details.append({
                "name": name, "added": 0, "changed": 0, "deleted": count,
                "source_rows": 0, "target_rows": count,
                "added_columns": [], "changed_columns": [],
                "deleted_columns": [r[1] for r in columns(t, name)],
                "row_identity": pk_columns(t, name) or None,
                "table_status": "deleted"
            })

        for name in common:
            d = compare_table(s, t, name)
            d["table_status"] = "common"
            table_details.append(d)

        # Categorize tables for the dashboard filter.
        for item in table_details:
            item["change_types"] = []

            if item.get("table_status", "") == "added":
                item["change_types"].append("tables_added")
            if item.get("added_columns", []):
                item["change_types"].append("columns_added")
            if item.get("added", 0):
                item["change_types"].append("rows_added")
            if item.get("changed", 0):
                item["change_types"].append("rows_changed")
            if item.get("deleted", 0):
                item["change_types"].append("rows_deleted")

        table_details.sort(key=lambda x: x["name"])

        return {
            "tables": table_details,
            "summary": {
                "tables_added": len(table_added),
                "tables_changed": sum(
                    1 for x in table_details
                    if x["table_status"] == "common" and (
                        x["added"] or x["changed"] or x["deleted"] or
                        x["added_columns"] or x["changed_columns"] or x["deleted_columns"]
                    )
                ),
                "tables_deleted": len(table_deleted),
                "columns_added": sum(len(x["added_columns"]) for x in table_details),
                "columns_changed": sum(len(x["changed_columns"]) for x in table_details),
                "columns_deleted": sum(len(x["deleted_columns"]) for x in table_details),
                "rows_added": sum(x["added"] for x in table_details),
                "rows_changed": sum(x["changed"] for x in table_details),
                "rows_deleted": sum(x["deleted"] for x in table_details),
            }
        }


def get_table_changes(source_path, target_path, table):
    with connect(source_path) as s, connect(target_path) as t:
        if table not in set(tables(s)) | set(tables(t)):
            raise ValueError("Unknown table")

        if table not in tables(s):
            tc = columns(t, table)
            rows = t.execute(f"SELECT * FROM {quote_ident(table)}").fetchall()
            return {
                "columns": [x[1] for x in tc],
                "rows": [{"values": list(r), "status": "deleted", "changed": []} for r in rows],
                "status": "deleted",
                "message": "Table exists only in Target."
            }

        if table not in tables(t):
            sc = columns(s, table)
            rows = s.execute(f"SELECT * FROM {quote_ident(table)}").fetchall()
            return {
                "columns": [x[1] for x in sc],
                "rows": [{"values": list(r), "status": "added", "changed": []} for r in rows],
                "status": "added",
                "message": "Table exists only in Source."
            }

        sc = columns(s, table)
        tc = columns(t, table)
        common = [x[1] for x in sc if x[1] in {y[1] for y in tc}]
        display_cols = [x[1] for x in sc]

        spk = pk_columns(s, table)
        tpk = pk_columns(t, table)
        key_cols = spk if spk and spk == tpk else ([c for c in common] if not spk and not tpk else [])
        if not key_cols:
            return {
                "columns": display_cols,
                "rows": [],
                "status": "uncomparable",
                "message": "Rows cannot be safely matched because the Source and Target primary keys differ."
            }

        select_s = f"SELECT {','.join(quote_ident(c) for c in common)} FROM {quote_ident(table)}"
        select_t = select_s
        sr = s.execute(select_s).fetchall()
        tr = t.execute(select_t).fetchall()
        idx = {c: i for i, c in enumerate(common)}
        pk_idx = [idx[c] for c in key_cols]

        sdict = {row_key(r, pk_idx): r for r in sr}
        tdict = {row_key(r, pk_idx): r for r in tr}

        output = []
        for k in sorted(set(sdict) | set(tdict), key=str):
            if k not in tdict:
                output.append({"values": list(sdict[k]), "status": "added", "changed": []})
            elif k not in sdict:
                output.append({"values": list(tdict[k]), "status": "deleted", "changed": []})
            else:
                changed = [
                    common[i] for i in range(len(common))
                    if sdict[k][i] != tdict[k][i]
                ]
                # Display Source values for changed rows; target-only columns are omitted.
                output.append({
                    "values": list(sdict[k]),
                    "status": "changed" if changed else "unchanged",
                    "changed": changed
                })

        return {
            "columns": display_cols,
            "rows": output,
            "status": "common",
            "message": f"Row identity: {', '.join(key_cols)}"
        }
