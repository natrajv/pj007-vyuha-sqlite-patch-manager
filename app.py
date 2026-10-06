import os
import sqlite3
import uuid
import shutil
import json
import math
import fnmatch
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash

from merger.comparator import compare_databases, compare_schema, get_table_changes
from merger.merger import merge_databases

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE = BASE_DIR / "workspace"
WORKSPACE.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

CONFIG_PATH = BASE_DIR / "config.json"
with CONFIG_PATH.open("r", encoding="utf-8") as f:
    APP_CONFIG = json.load(f)

APP_NAME = "Vyuha SQLite Patch Manager"
APP_VERSION = str(APP_CONFIG.get("version", "1.0.0"))
PAGINATION_SIZE = max(1, int(APP_CONFIG.get("pagination_size", 10)))


@app.context_processor
def inject_app_config():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
    }


def db_paths():
    sid = session.get("session_id")
    if not sid:
        return None, None, None
    d = WORKSPACE / sid
    return d, d / "source.sqlite", d / "target.sqlite"


@app.route("/")
def index():
    stats = session.get("stats")
    return render_template("index.html", stats=stats)


@app.post("/upload")
def upload():
    source = request.files.get("source_db")
    target = request.files.get("target_db")

    if not source or not target or not source.filename or not target.filename:
        flash("Please select both Patch and Base SQLite databases.", "danger")
        return redirect(url_for("index"))

    if not source.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        flash("Patch SQLite database must be a SQLite database file.", "danger")
        return redirect(url_for("index"))

    if not target.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        flash("Base SQLite database must be a SQLite database file.", "danger")
        return redirect(url_for("index"))

    sid = uuid.uuid4().hex
    d = WORKSPACE / sid
    d.mkdir(parents=True, exist_ok=True)

    source_path = d / "source.sqlite"
    target_path = d / "target.sqlite"
    source.save(source_path)
    target.save(target_path)

    try:
        stats = compare_databases(source_path, target_path)
        schema_result = compare_schema(source_path, target_path)
    except Exception as exc:
        shutil.rmtree(d, ignore_errors=True)
        flash(f"Database comparison failed: {exc}", "danger")
        return redirect(url_for("index"))

    session["session_id"] = sid
    session["stats"] = stats
    session["schema_match"] = schema_result.get("matches", False)
    session["schema_report"] = None
    session["patch_name"] = source.filename
    session["base_name"] = target.filename
    session.pop("merged", None)
    return redirect(url_for("dashboard"))


@app.post("/compare-schema")
def compare_schema_route():
    d, source, target = db_paths()
    if not source or not source.exists():
        return redirect(url_for("index"))

    try:
        report = compare_schema(source, target)
        session["schema_report"] = report
    except Exception as exc:
        session["schema_report"] = {
            "matches": False,
            "error": str(exc),
            "tables_added": [],
            "tables_deleted": [],
            "table_mismatches": [],
        }

    return redirect(url_for("dashboard",
        table_filter=request.form.get("table_filter", "all"),
        table_name_filter=request.form.get("table_name_filter", ""),
    ))


@app.route("/dashboard")
def dashboard():
    d, source, target = db_paths()
    if not source or not source.exists():
        return redirect(url_for("index"))

    stats = session.get("stats") or compare_databases(source, target)
    session["stats"] = stats

    options = session.get("patch_options", {
        "change_structure": False,
        "delete_rows": False,
        "update_rows": False,
    })

    summary = stats["summary"]
    impact = {
        "tables_added": summary["tables_added"] if options["change_structure"] else 0,
        "tables_changed": 0,
        "tables_deleted": 0,
        "columns_added": summary["columns_added"] if options["change_structure"] else 0,
        "columns_changed": 0,
        "columns_deleted": 0,
        "rows_added": summary["rows_added"],
        "rows_changed": summary["rows_changed"] if options["update_rows"] else 0,
        "rows_deleted": summary["rows_deleted"] if options["delete_rows"] else 0,
    }

    # Server-side table filtering and pagination.
    table_filter = request.args.get("table_filter", "all")
    table_name_filter = request.args.get("table_name_filter", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    allowed_filters = {
        "all", "tables_added", "columns_added",
        "rows_added", "rows_changed", "rows_deleted"
    }
    if table_filter not in allowed_filters:
        table_filter = "all"

    filtered_tables = []
    pattern = table_name_filter.lower()
    for table in stats.get("tables", []):
        change_types = set(table.get("change_types", []))
        type_match = table_filter == "all" or table_filter in change_types
        name_match = not pattern or fnmatch.fnmatchcase(
            table.get("name", "").lower(), pattern
        )
        if type_match and name_match:
            filtered_tables.append(table)

    total_tables = len(filtered_tables)
    total_pages = max(1, math.ceil(total_tables / PAGINATION_SIZE))
    page = min(page, total_pages)
    start_index = (page - 1) * PAGINATION_SIZE
    page_tables = filtered_tables[start_index:start_index + PAGINATION_SIZE]

    pagination = {
        "page": page,
        "size": PAGINATION_SIZE,
        "total": total_tables,
        "pages": total_pages,
        "start": start_index + 1 if total_tables else 0,
        "end": min(start_index + PAGINATION_SIZE, total_tables),
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }

    return render_template(
        "dashboard.html",
        stats=stats,
        impact=impact,
        options=options,
        patch_name=session.get("patch_name"),
        base_name=session.get("base_name"),
        merged=session.get("merged", False),
        schema_report=session.get("schema_report"),
        table_rows=page_tables,
        pagination=pagination,
    )


@app.route("/changes")
def changes():
    d, source, target = db_paths()
    if not source or not source.exists():
        return redirect(url_for("index"))

    table_names = sorted(set(
        [x["name"] for x in session.get("stats", {}).get("tables", [])]
    ))
    selected = request.args.get("table")
    detail = None

    if selected and selected in table_names:
        detail = get_table_changes(source, target, selected)

    return render_template(
        "changes.html",
        tables=table_names,
        selected=selected,
        detail=detail,
        table_filter=request.args.get("table_filter", "all"),
        table_name_filter=request.args.get("table_name_filter", ""),
        page=request.args.get("page", "1"),
    )


@app.post("/merge")
def merge():
    d, source, target = db_paths()
    if not source or not source.exists():
        return redirect(url_for("index"))

    change_structure = request.form.get("change_structure") == "yes"
    delete_rows = request.form.get("delete_rows") == "yes"
    update_rows = request.form.get("update_rows") == "yes"

    session["patch_options"] = {
        "change_structure": change_structure,
        "delete_rows": delete_rows,
        "update_rows": update_rows,
    }

    merged = d / "merged.sqlite"
    try:
        merge_databases(source, target, merged, change_structure=change_structure, delete_rows=delete_rows, update_rows=update_rows)
        stats = compare_databases(source, merged)
        session["stats"] = stats
        session["merged"] = True
        flash("Merge completed. The original Patch and Base databases were not modified.", "success")
    except Exception as exc:
        flash(f"Merge failed: {exc}", "danger")
    return redirect(url_for("dashboard"))


@app.post("/refresh-dashboard")
def refresh_dashboard():
    d, source, target = db_paths()
    if not source or not source.exists():
        return redirect(url_for("index"))

    session["patch_options"] = {
        "change_structure": request.form.get("change_structure", "yes") == "yes",
        "delete_rows": request.form.get("delete_rows") == "yes",
        "update_rows": request.form.get("update_rows") == "yes",
    }

    # Re-read the Patch/Base comparison and redisplay the dashboard.
    session["stats"] = compare_databases(source, target)
    schema_result = compare_schema(source, target)
    session["schema_match"] = schema_result.get("matches", False)
    session["schema_report"] = None
    return redirect(url_for(
        "dashboard",
        table_filter=request.form.get("table_filter", "all"),
        table_name_filter=request.form.get("table_name_filter", ""),
    ))


@app.route("/download")
def download():
    d, source, target = db_paths()
    merged = d / "merged.sqlite" if d else None
    if not merged or not merged.exists():
        flash("No patched database is available. Run Merge Now first.", "warning")
        return redirect(url_for("dashboard"))

    return send_file(
        merged,
        as_attachment=True,
        download_name="merged.sqlite",
        mimetype="application/x-sqlite3",
    )


@app.post("/reset")
def reset():
    d, _, _ = db_paths()
    if d:
        shutil.rmtree(d, ignore_errors=True)
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
