#!/usr/bin/env python3
"""
migrate_to_markdown.py — Convert NoteStack note content from HTML to Markdown.

NoteStack switched from a custom Qt rich-text editor (which stored content as
HTML) to Toast UI Editor (which stores content as Markdown).  Run this script
once on any database that was created with the old editor to convert all note
and trash content to Markdown.

Usage
-----
    python migrate_to_markdown.py [--db-path PATH] [--dry-run]

Options
-------
--db-path PATH
    Path to notestack.db.
    Default: the standard user-data location for the current OS.

--dry-run
    Show what would be converted without writing any changes.

Requirements
------------
    pip install html2text

Back up notestack.db before running this script.
"""
from __future__ import annotations

import argparse
import os
import platform
import sqlite3
import sys


# ─── DB path helpers ──────────────────────────────────────────────────────────

def _default_db_path() -> str:
    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
    elif system == "darwin":
        base = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support"
        )
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, "ABasu_apps", "NoteStack", "notestack.db")


# ─── HTML detection ───────────────────────────────────────────────────────────

_HTML_TAGS = ("<html", "<body", "<p>", "<p ", "<div", "<span", "<img", "<table", "<ul", "<ol")

def _is_html(text: str) -> bool:
    lower = text.lower()
    return any(tag in lower for tag in _HTML_TAGS)


# ─── Conversion ───────────────────────────────────────────────────────────────

def _get_converter():
    """Return a configured html2text.HTML2Text instance."""
    try:
        import html2text  # type: ignore
    except ImportError:
        sys.exit(
            "html2text is not installed.\n"
            "Run:  pip install html2text\n"
            "then re-run this script."
        )
    h = html2text.HTML2Text()
    h.ignore_links       = False
    h.body_width         = 0        # disable line-wrapping
    h.protect_links      = True
    h.wrap_links         = False
    h.unicode_snob       = True     # prefer Unicode over HTML entities
    h.images_to_alt      = False    # keep image references
    return h


def _to_markdown(converter, html: str) -> str:
    return converter.handle(html).strip()


# ─── Migration ────────────────────────────────────────────────────────────────

# Tables and columns that hold note content.
_CONTENT_COLUMNS = [
    ("prompts", "id", "content"),
    ("trash",   "id", "content"),
]


def migrate(db_path: str, dry_run: bool) -> None:
    if not os.path.isfile(db_path):
        sys.exit(f"Database not found: {db_path}")

    converter = _get_converter()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total_converted = 0

    for table, pk, col in _CONTENT_COLUMNS:
        # Guard: table might not exist in all schema versions.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            print(f"  {table}: table not found — skipping")
            continue

        rows = conn.execute(f"SELECT {pk}, {col} FROM {table}").fetchall()
        converted = 0

        for row in rows:
            row_id  = row[pk]
            content = row[col] or ""

            if not _is_html(content):
                continue  # already Markdown or plain text

            markdown = _to_markdown(converter, content)

            if dry_run:
                print(
                    f"  [dry-run] {table} id={row_id}: "
                    f"{len(content)} chars (HTML) → {len(markdown)} chars (Markdown)"
                )
            else:
                conn.execute(
                    f"UPDATE {table} SET {col}=? WHERE {pk}=?",
                    (markdown, row_id),
                )
            converted += 1
            total_converted += 1

        print(f"  {table}: {converted}/{len(rows)} rows converted")

    if not dry_run:
        conn.commit()
        print(f"\nDone — {total_converted} note(s) converted to Markdown.")
    else:
        print(f"\nDry run complete — {total_converted} note(s) would be converted.  No changes written.")

    conn.close()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    default_path = _default_db_path()

    parser = argparse.ArgumentParser(
        description="Convert NoteStack note content from HTML to Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db-path",
        default=default_path,
        metavar="PATH",
        help=f"Path to notestack.db  (default: {default_path})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview conversions without writing any changes",
    )
    args = parser.parse_args()

    print(f"Database : {args.db_path}")

    if not args.dry_run:
        print(
            "\nWARNING: This will modify your database in-place.\n"
            "         Back up notestack.db before continuing.\n"
        )
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted — no changes written.")
            return

    migrate(args.db_path, args.dry_run)


if __name__ == "__main__":
    main()
