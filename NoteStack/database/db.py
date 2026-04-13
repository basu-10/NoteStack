"""
Database layer for NoteStack.
Handles all SQLite operations: notes, folders, tags.
"""
import hashlib
import colorsys
import os
import platform
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Optional

APP_VENDOR = "ABasu_apps"
APP_NAME = "NoteStack"
TEMPLATE_DB_NAME = "notestack_template.db"
SEARCH_FTS_TABLE = "prompts_fts"
TRASH_EXPIRY_HOURS = 48

DEFAULT_ENTITY_COLORS = [
    "#4F6EF7",
    "#22C55E",
    "#F59E0B",
    "#EC4899",
    "#06B6D4",
    "#A855F7",
    "#EF4444",
    "#84CC16",
    "#F97316",
    "#14B8A6",
    "#8B5CF6",
    "#3B82F6",
    "#10B981",
    "#EAB308",
    "#D946EF",
]


def _install_base_dir() -> str:
    # db.py lives at {root}/NoteStack/database/db.py in both dev and installed layout.
    # Going 3 levels up reaches the repo root (dev) or {app} install dir (installed),
    # which is where notestack_template.db is placed by the installer.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _user_data_dir() -> str:
    system_name = platform.system().lower()

    if system_name == "windows":
        base_dir = os.environ.get("LOCALAPPDATA")
        if not base_dir:
            base_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif system_name == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base_dir = os.environ.get("XDG_DATA_HOME")
        if not base_dir:
            base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

    path = os.path.join(base_dir, APP_VENDOR, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


USER_DATA_DIR = _user_data_dir()
DB_PATH = os.path.join(USER_DATA_DIR, "notestack.db")
LEGACY_DB_PATH = os.path.join(USER_DATA_DIR, "promptvault.db")


def _template_db_candidates() -> list[str]:
    return [
        os.path.join(_install_base_dir(), TEMPLATE_DB_NAME),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), TEMPLATE_DB_NAME),
    ]


def ensure_runtime_db():
    if os.path.exists(DB_PATH):
        return

    if os.path.exists(LEGACY_DB_PATH):
        shutil.copy2(LEGACY_DB_PATH, DB_PATH)
        return

    for candidate in _template_db_candidates():
        if os.path.exists(candidate):
            shutil.copy2(candidate, DB_PATH)
            return


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _fts_table_ready(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, SEARCH_FTS_TABLE)


def _create_search_index(conn: sqlite3.Connection):
    try:
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {SEARCH_FTS_TABLE}
            USING fts5(
                prompt_id UNINDEXED,
                title,
                content,
                tags,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
    except sqlite3.OperationalError:
        # FTS5 is optional: fall back to LIKE search if unavailable.
        return


def _prompt_search_payload(conn: sqlite3.Connection, prompt_id: int) -> Optional[tuple[int, str, str, str]]:
    row = conn.execute(
        """
        SELECT
            p.id AS prompt_id,
            p.title,
            p.content,
            COALESCE((
                SELECT GROUP_CONCAT(t.name, ' ')
                FROM tags t
                JOIN prompt_tags pt ON pt.tag_id = t.id
                WHERE pt.prompt_id = p.id
            ), '') AS tags
        FROM prompts p
        WHERE p.id = ?
        """,
        (prompt_id,),
    ).fetchone()
    if not row:
        return None
    return int(row["prompt_id"]), row["title"], row["content"], row["tags"]


def _refresh_prompt_search_index(conn: sqlite3.Connection, prompt_id: int):
    if not _fts_table_ready(conn):
        return

    payload = _prompt_search_payload(conn, prompt_id)
    conn.execute(f"DELETE FROM {SEARCH_FTS_TABLE} WHERE prompt_id=?", (prompt_id,))
    if payload is None:
        return

    conn.execute(
        f"INSERT INTO {SEARCH_FTS_TABLE}(prompt_id, title, content, tags) VALUES (?,?,?,?)",
        payload,
    )


def _refresh_prompts_search_index(conn: sqlite3.Connection, prompt_ids: list[int]):
    if not prompt_ids or not _fts_table_ready(conn):
        return
    for prompt_id in prompt_ids:
        _refresh_prompt_search_index(conn, prompt_id)


def _refresh_search_index_for_tag(conn: sqlite3.Connection, tag_id: int):
    if not _fts_table_ready(conn):
        return
    rows = conn.execute(
        "SELECT prompt_id FROM prompt_tags WHERE tag_id=?",
        (tag_id,),
    ).fetchall()
    prompt_ids = [int(r["prompt_id"]) for r in rows]
    _refresh_prompts_search_index(conn, prompt_ids)


def _rebuild_search_index(conn: sqlite3.Connection):
    if not _fts_table_ready(conn):
        return

    conn.execute(f"DELETE FROM {SEARCH_FTS_TABLE}")
    rows = conn.execute("SELECT id FROM prompts").fetchall()
    for row in rows:
        _refresh_prompt_search_index(conn, int(row["id"]))


def _tokenize_search_keyword(keyword: str) -> list[str]:
    parts = re.findall(r"[^\s]+", (keyword or "").strip().lower())
    tokens: list[str] = []
    for part in parts:
        normalized = part.strip().lstrip("#").strip("\"'")
        if normalized:
            tokens.append(normalized)
    return tokens


def _build_fts_match_expression(keyword: str) -> str:
    tokens = _tokenize_search_keyword(keyword)
    if not tokens:
        return ""
    return " AND ".join(f'"{token.replace("\"", "\"\"")}"*' for token in tokens)


def _build_keyword_filter(conn: sqlite3.Connection, keyword: str) -> tuple[str, list]:
    fts_expr = _build_fts_match_expression(keyword)
    if _fts_table_ready(conn) and fts_expr:
        try:
            conn.execute(
                f"SELECT prompt_id FROM {SEARCH_FTS_TABLE} WHERE {SEARCH_FTS_TABLE} MATCH ? LIMIT 1",
                (fts_expr,),
            ).fetchall()
            return (
                f"p.id IN (SELECT prompt_id FROM {SEARCH_FTS_TABLE} WHERE {SEARCH_FTS_TABLE} MATCH ?)",
                [fts_expr],
            )
        except sqlite3.OperationalError:
            pass

    like = f"%{keyword}%"
    return (
        """(
            p.title LIKE ?
            OR p.content LIKE ?
            OR EXISTS (
                SELECT 1
                FROM prompt_tags pt
                JOIN tags t ON t.id = pt.tag_id
                WHERE pt.prompt_id = p.id AND t.name LIKE ?
            )
        )""",
        [like, like, like],
    )


def _normalize_color(color: str | None) -> str | None:
    if not color:
        return None
    value = str(color).strip()
    if not value:
        return None
    if not value.startswith("#"):
        value = f"#{value}"
    value = value[:7]
    if len(value) != 7:
        return None
    return value.upper()


def _collect_entity_colors(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"SELECT color FROM {table} WHERE color IS NOT NULL AND TRIM(color) <> ''").fetchall()
    return {normalized for row in rows if (normalized := _normalize_color(row["color"]))}


def _generate_distinct_color(seed_index: int) -> str:
    hue = (seed_index * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.62, 0.92)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def _next_unique_color(conn: sqlite3.Connection, table: str) -> str:
    used = _collect_entity_colors(conn, table)
    for color in DEFAULT_ENTITY_COLORS:
        normalized = _normalize_color(color)
        if normalized and normalized not in used:
            return normalized

    idx = 0
    while True:
        candidate = _generate_distinct_color(len(used) + idx)
        if candidate not in used:
            return candidate
        idx += 1


def initialize_db():
    ensure_runtime_db()
    conn = get_connection()
    c = conn.cursor()

    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            parent_id   INTEGER REFERENCES folders(id) ON DELETE SET NULL,
            color       TEXT DEFAULT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS prompts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT NOT NULL,
            content          TEXT NOT NULL,
            folder_id        INTEGER REFERENCES folders(id) ON DELETE SET NULL,
            is_favorite      INTEGER NOT NULL DEFAULT 0,
            last_accessed_at TEXT DEFAULT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tags (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS prompt_tags (
            prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
            tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (prompt_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS trash (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            folder_id   INTEGER,
            folder_name TEXT,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            tag_names   TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            deleted_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    if not _column_exists(conn, "folders", "parent_id"):
        c.execute("ALTER TABLE folders ADD COLUMN parent_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
    if not _column_exists(conn, "folders", "color"):
        c.execute("ALTER TABLE folders ADD COLUMN color TEXT DEFAULT NULL")
    if not _column_exists(conn, "prompts", "last_accessed_at"):
        c.execute("ALTER TABLE prompts ADD COLUMN last_accessed_at TEXT DEFAULT NULL")
    if not _column_exists(conn, "tags", "color"):
        c.execute("ALTER TABLE tags ADD COLUMN color TEXT DEFAULT NULL")

    _create_search_index(conn)

    c.execute("UPDATE folders SET color=? WHERE color IS NULL OR TRIM(color)=''", ("",))
    folder_rows = c.execute("SELECT id FROM folders ORDER BY id").fetchall()
    for row in folder_rows:
        folder_color = c.execute("SELECT color FROM folders WHERE id=?", (row["id"],)).fetchone()["color"]
        if folder_color and str(folder_color).strip():
            continue
        next_color = _next_unique_color(conn, "folders")
        c.execute("UPDATE folders SET color=? WHERE id=?", (next_color, row["id"]))

    c.execute("UPDATE tags SET color=? WHERE color IS NULL OR TRIM(color)=''", ("",))
    tag_rows = c.execute("SELECT id FROM tags ORDER BY id").fetchall()
    for row in tag_rows:
        tag_color = c.execute("SELECT color FROM tags WHERE id=?", (row["id"],)).fetchone()["color"]
        if tag_color and str(tag_color).strip():
            continue
        next_color = _next_unique_color(conn, "tags")
        c.execute("UPDATE tags SET color=? WHERE id=?", (next_color, row["id"]))

    _rebuild_search_index(conn)

    conn.commit()
    conn.close()


# ─── Folder helpers ───────────────────────────────────────────────────────────

def get_all_folders() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, parent_id, color FROM folders ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_folders_tree() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, parent_id, color FROM folders ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_folder(name: str, parent_id: Optional[int] = None, color: Optional[str] = None) -> int:
    conn = get_connection()
    normalized = _normalize_color(color) if color else None
    folder_color = normalized or _next_unique_color(conn, "folders")
    cur = conn.execute(
        "INSERT OR IGNORE INTO folders (name, parent_id, color) VALUES (?,?,?)",
        (name, parent_id, folder_color),
    )
    conn.commit()
    folder_id = cur.lastrowid or conn.execute(
        "SELECT id FROM folders WHERE name=?", (name,)
    ).fetchone()["id"]
    conn.close()
    return folder_id


def delete_folder(folder_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    conn.commit()
    conn.close()


def rename_folder(folder_id: int, new_name: str, parent_id: Optional[int] = None):
    conn = get_connection()
    if parent_id is None:
        conn.execute("UPDATE folders SET name=? WHERE id=?", (new_name, folder_id))
    else:
        conn.execute(
            "UPDATE folders SET name=?, parent_id=? WHERE id=?",
            (new_name, parent_id, folder_id),
        )
    conn.commit()
    conn.close()


def set_folder_parent(folder_id: int, parent_id: Optional[int]):
    conn = get_connection()
    conn.execute("UPDATE folders SET parent_id=? WHERE id=?", (parent_id, folder_id))
    conn.commit()
    conn.close()


def set_folder_color(folder_id: int, color: Optional[str]):
    conn = get_connection()
    conn.execute("UPDATE folders SET color=? WHERE id=?", (_normalize_color(color), folder_id))
    conn.commit()
    conn.close()


# ─── Tag helpers ──────────────────────────────────────────────────────────────

def get_all_tags() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.name, t.color, COUNT(pt.prompt_id) AS count
           FROM tags t LEFT JOIN prompt_tags pt ON t.id=pt.tag_id
           GROUP BY t.id ORDER BY t.name"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ensure_tag(name: str, conn: sqlite3.Connection) -> int:
    name = name.strip().lower().lstrip("#")
    color = _next_unique_color(conn, "tags")
    conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?,?)", (name, color))
    return conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]


def create_tag(name: str, color: Optional[str] = None) -> int:
    normalized_name = name.strip().lower().lstrip("#")
    if not normalized_name:
        return 0
    conn = get_connection()
    normalized_color = _normalize_color(color) if color else None
    tag_color = normalized_color or _next_unique_color(conn, "tags")
    conn.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?,?)", (normalized_name, tag_color))
    row = conn.execute("SELECT id FROM tags WHERE name=?", (normalized_name,)).fetchone()
    tag_id = int(row["id"]) if row else 0
    if tag_id and normalized_color:
        conn.execute("UPDATE tags SET color=? WHERE id=?", (normalized_color, tag_id))
    conn.commit()
    conn.close()
    return tag_id


def rename_tag(tag_id: int, new_name: str):
    name = new_name.strip().lower().lstrip("#")
    if not name:
        return
    conn = get_connection()
    conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
    _refresh_search_index_for_tag(conn, tag_id)
    conn.commit()
    conn.close()


def delete_tag(tag_id: int):
    conn = get_connection()
    rows = conn.execute("SELECT prompt_id FROM prompt_tags WHERE tag_id=?", (tag_id,)).fetchall()
    prompt_ids = [int(r["prompt_id"]) for r in rows]
    conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    _refresh_prompts_search_index(conn, prompt_ids)
    conn.commit()
    conn.close()


def set_tag_color(tag_id: int, color: Optional[str]):
    conn = get_connection()
    conn.execute("UPDATE tags SET color=? WHERE id=?", (_normalize_color(color), tag_id))
    conn.commit()
    conn.close()


# ─── Prompt helpers ───────────────────────────────────────────────────────────

def _attach_tags(prompt: dict, conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT t.name, t.color FROM tags t
           JOIN prompt_tags pt ON t.id=pt.tag_id
           WHERE pt.prompt_id=?
           ORDER BY t.name""",
        (prompt["id"],),
    ).fetchall()
    prompt["tags"] = [r["name"] for r in rows]
    prompt["tag_colors"] = {r["name"]: r["color"] for r in rows}
    return prompt


def get_prompts(
    *,
    folder_id: Optional[int] = None,
    include_subfolders: bool = False,
    favorites_only: bool = False,
    keyword: str = "",
    tag_names: Optional[list] = None,
    sort: str = "newest",
) -> list[dict]:
    conn = get_connection()
    clauses = ["1=1"]
    params: list = []

    if folder_id is not None:
        if include_subfolders:
            subtree_rows = conn.execute(
                """
                WITH RECURSIVE subtree(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT f.id FROM folders f JOIN subtree s ON f.parent_id = s.id
                )
                SELECT id FROM subtree
                """,
                (folder_id,),
            ).fetchall()
            subtree_ids = [r["id"] for r in subtree_rows]
            placeholders = ",".join("?" * len(subtree_ids))
            clauses.append(f"p.folder_id IN ({placeholders})")
            params.extend(subtree_ids)
        else:
            clauses.append("p.folder_id = ?")
            params.append(folder_id)
    if favorites_only:
        clauses.append("p.is_favorite = 1")
    if keyword:
        keyword_clause, keyword_params = _build_keyword_filter(conn, keyword)
        clauses.append(keyword_clause)
        params += keyword_params
    if tag_names:
        placeholders = ",".join("?" * len(tag_names))
        clauses.append(
            f"""p.id IN (
                SELECT pt.prompt_id FROM prompt_tags pt
                JOIN tags t ON t.id=pt.tag_id
                WHERE LOWER(t.name) IN ({placeholders})
                GROUP BY pt.prompt_id
                HAVING COUNT(DISTINCT t.id)=?
            )"""
        )
        params += [n.lower() for n in tag_names]
        params.append(len(tag_names))

    if sort == "oldest":
        order = "p.created_at ASC"
    elif sort == "alpha":
        order = "p.title ASC"
    elif sort == "alpha_desc":
        order = "p.title DESC"
    else:
        order = "p.created_at DESC"

    sql = f"""
        SELECT p.id, p.title, p.content, p.folder_id,
               p.is_favorite, p.last_accessed_at, p.created_at, p.updated_at,
               f.name AS folder_name, f.color AS folder_color
        FROM prompts p LEFT JOIN folders f ON p.folder_id=f.id
        WHERE {' AND '.join(clauses)}
        ORDER BY {order}
    """
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        _attach_tags(r, conn)
    conn.close()
    return rows


def get_prompt(prompt_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        """SELECT p.*, f.name AS folder_name, f.color AS folder_color
           FROM prompts p LEFT JOIN folders f ON p.folder_id=f.id
           WHERE p.id=?""",
        (prompt_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    prompt = dict(row)
    _attach_tags(prompt, conn)
    conn.close()
    return prompt


def create_prompt(*, title: str, content: str, folder_id: Optional[int], tag_names: list) -> int:
    conn = get_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cur = conn.execute(
        "INSERT INTO prompts (title, content, folder_id, created_at, updated_at) VALUES (?,?,?,?,?)",
        (title, content, folder_id, now, now),
    )
    prompt_id = int(cur.lastrowid or 0)
    for name in tag_names:
        if name.strip():
            tag_id = ensure_tag(name, conn)
            conn.execute("INSERT OR IGNORE INTO prompt_tags VALUES (?,?)", (prompt_id, tag_id))
    _refresh_prompt_search_index(conn, prompt_id)
    conn.commit()
    conn.close()
    return prompt_id


def update_prompt(prompt_id: int, *, title: str, content: str, folder_id: Optional[int], tag_names: list):
    conn = get_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.execute(
        "UPDATE prompts SET title=?, content=?, folder_id=?, updated_at=? WHERE id=?",
        (title, content, folder_id, now, prompt_id),
    )
    conn.execute("DELETE FROM prompt_tags WHERE prompt_id=?", (prompt_id,))
    for name in tag_names:
        if name.strip():
            tag_id = ensure_tag(name, conn)
            conn.execute("INSERT OR IGNORE INTO prompt_tags VALUES (?,?)", (prompt_id, tag_id))
    _refresh_prompt_search_index(conn, prompt_id)
    conn.commit()
    conn.close()


def touch_prompt(prompt_id: int):
    conn = get_connection()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    conn.execute("UPDATE prompts SET last_accessed_at=? WHERE id=?", (now, prompt_id))
    conn.commit()
    conn.close()


def get_recent_prompts(limit: int = 10) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, title, content, folder_id, last_accessed_at, updated_at
        FROM prompts
        WHERE COALESCE(last_accessed_at, '') <> ''
        ORDER BY last_accessed_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_favorite(prompt_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT is_favorite FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    new_val = 0 if row["is_favorite"] else 1
    conn.execute("UPDATE prompts SET is_favorite=?, updated_at=datetime('now') WHERE id=?", (new_val, prompt_id))
    conn.commit()
    conn.close()
    return bool(new_val)


def delete_prompt(prompt_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
    _refresh_prompt_search_index(conn, prompt_id)
    conn.commit()
    conn.close()


def _make_placeholders(values: list[int]) -> str:
    return ",".join(["?"] * len(values))


def bulk_move_prompts(prompt_ids: list[int], folder_id: Optional[int]):
    if not prompt_ids:
        return
    conn = get_connection()
    placeholders = _make_placeholders(prompt_ids)
    conn.execute(
        f"UPDATE prompts SET folder_id=?, updated_at=datetime('now') WHERE id IN ({placeholders})",
        [folder_id, *prompt_ids],
    )
    conn.commit()
    conn.close()


def bulk_add_tag(prompt_ids: list[int], tag_name: str):
    if not prompt_ids or not tag_name.strip():
        return
    conn = get_connection()
    tag_id = ensure_tag(tag_name, conn)
    for pid in prompt_ids:
        conn.execute("INSERT OR IGNORE INTO prompt_tags(prompt_id, tag_id) VALUES (?,?)", (pid, tag_id))
    _refresh_prompts_search_index(conn, prompt_ids)
    conn.commit()
    conn.close()


def bulk_delete_prompts(prompt_ids: list[int]):
    if not prompt_ids:
        return
    conn = get_connection()
    placeholders = _make_placeholders(prompt_ids)
    conn.execute(f"DELETE FROM prompts WHERE id IN ({placeholders})", prompt_ids)
    if _fts_table_ready(conn):
        conn.execute(
            f"DELETE FROM {SEARCH_FTS_TABLE} WHERE prompt_id IN ({placeholders})",
            prompt_ids,
        )
    conn.commit()
    conn.close()


def bulk_copy_prompts(
    prompt_ids: list[int],
    folder_id: Optional[int],
    *,
    preserve_folder: bool = False,
) -> list[int]:
    """Duplicate selected prompts into the given folder, preserving tags.

    When *preserve_folder* is True the ``folder_id`` argument is ignored and
    each duplicate inherits the source note's own ``folder_id`` instead.
    Returns new prompt IDs.
    """
    if not prompt_ids:
        return []
    conn = get_connection()
    placeholders = _make_placeholders(prompt_ids)
    rows = conn.execute(
        f"SELECT id, title, content, folder_id, is_favorite FROM prompts WHERE id IN ({placeholders})",
        prompt_ids,
    ).fetchall()

    new_ids: list[int] = []
    for row in rows:
        dest_folder = row["folder_id"] if preserve_folder else folder_id
        cursor = conn.execute(
            "INSERT INTO prompts (title, content, folder_id, is_favorite, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (row["title"], row["content"], dest_folder, row["is_favorite"]),
        )
        new_id = cursor.lastrowid
        new_ids.append(new_id)
        tag_rows = conn.execute(
            "SELECT tag_id FROM prompt_tags WHERE prompt_id=?", (row["id"],)
        ).fetchall()
        for tag_row in tag_rows:
            conn.execute(
                "INSERT OR IGNORE INTO prompt_tags(prompt_id, tag_id) VALUES (?,?)",
                (new_id, tag_row["tag_id"]),
            )

    if new_ids:
        _refresh_prompts_search_index(conn, new_ids)
    conn.commit()
    conn.close()
    return new_ids


def _build_folder_path(folder_id: int | None, conn: sqlite3.Connection) -> str:
    """Walk parent_id chain to build 'NoteStack/A/B'.  Returns 'NoteStack' for root notes."""
    if not folder_id:
        return "NoteStack"
    segments: list[str] = []
    current_id: int | None = folder_id
    visited: set[int] = set()
    while current_id is not None:
        if current_id in visited:
            break
        visited.add(current_id)
        row = conn.execute(
            "SELECT name, parent_id FROM folders WHERE id=?", (current_id,)
        ).fetchone()
        if row is None:
            break
        segments.append(row["name"])
        current_id = row["parent_id"]
    segments.reverse()
    return "NoteStack/" + "/".join(segments)


def _ensure_folder_path(path: str, conn: sqlite3.Connection) -> int | None:
    """Create folder hierarchy from 'NoteStack/A/B'. Returns leaf folder_id, or None for root."""
    parts = [p.strip() for p in path.strip("/").split("/") if p.strip()]
    if parts and parts[0].lower() == "notestack":
        parts = parts[1:]
    if not parts:
        return None
    parent_id: int | None = None
    leaf_id: int | None = None
    for segment in parts:
        if parent_id is None:
            row = conn.execute(
                "SELECT id FROM folders WHERE name=? AND parent_id IS NULL", (segment,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM folders WHERE name=? AND parent_id=?", (segment, parent_id)
            ).fetchone()
        if row:
            leaf_id = row["id"]
        else:
            color = _next_unique_color(conn, "folders")
            cur = conn.execute(
                "INSERT INTO folders (name, parent_id, color) VALUES (?,?,?)",
                (segment, parent_id, color),
            )
            leaf_id = cur.lastrowid
        parent_id = leaf_id
    return leaf_id


def export_prompts(prompt_ids: Optional[list[int]] = None) -> list[dict]:
    conn = get_connection()
    params: list = []
    where = ""
    if prompt_ids:
        placeholders = _make_placeholders(prompt_ids)
        where = f"WHERE p.id IN ({placeholders})"
        params = [*prompt_ids]

    rows = conn.execute(
        f"""
        SELECT p.id, p.title, p.content, p.created_at, p.updated_at,
               p.is_favorite, p.last_accessed_at,
               p.folder_id,
               f.name AS folder
        FROM prompts p
        LEFT JOIN folders f ON p.folder_id = f.id
        {where}
        ORDER BY p.created_at DESC
        """,
        params,
    ).fetchall()

    out: list[dict] = []
    for row in rows:
        prompt = dict(row)
        prompt["folder_path"] = _build_folder_path(prompt.get("folder_id"), conn)
        tags = conn.execute(
            """
            SELECT t.name FROM tags t
            JOIN prompt_tags pt ON pt.tag_id=t.id
            WHERE pt.prompt_id=?
            ORDER BY t.name
            """,
            (prompt["id"],),
        ).fetchall()
        prompt["tags"] = [t["name"] for t in tags]
        out.append(prompt)
    conn.close()
    return out


def _prompt_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()


def import_prompts(rows: list[dict]) -> int:
    if not rows:
        return 0

    conn = get_connection()
    imported = 0
    existing = conn.execute("SELECT title, content FROM prompts").fetchall()
    existing_hashes = {_prompt_hash(r["title"], r["content"]) for r in existing}

    for row in rows:
        title = (row.get("title") or "").strip()
        content = (row.get("content") or "").strip()
        if not title or not content:
            continue
        h = _prompt_hash(title, content)
        if h in existing_hashes:
            continue

        folder_id = None
        folder_path = (row.get("folder_path") or "").strip()
        folder_name = (row.get("folder") or "").strip()
        if folder_path:
            folder_id = _ensure_folder_path(folder_path, conn)
        elif folder_name:
            folder_id = _ensure_folder_path("NoteStack/" + folder_name, conn)

        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        created_at = row.get("created_at") or now
        updated_at = row.get("updated_at") or created_at
        is_favorite = 1 if row.get("is_favorite") else 0
        last_accessed_at = row.get("last_accessed_at")

        cur = conn.execute(
            """
            INSERT INTO prompts (
                title, content, folder_id, is_favorite, last_accessed_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (title, content, folder_id, is_favorite, last_accessed_at, created_at, updated_at),
        )
        prompt_id = cur.lastrowid

        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split("|") if t.strip()]
        for tag in tags:
            tag_id = ensure_tag(str(tag), conn)
            conn.execute("INSERT OR IGNORE INTO prompt_tags(prompt_id, tag_id) VALUES (?,?)", (prompt_id, tag_id))

        existing_hashes.add(h)
        imported += 1

    if imported:
        _rebuild_search_index(conn)

    conn.commit()
    conn.close()
    return imported


def get_stats():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    favs = conn.execute("SELECT COUNT(*) FROM prompts WHERE is_favorite=1").fetchone()[0]
    folders = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
    tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    conn.close()
    return {"total": total, "favorites": favs, "folders": folders, "tags": tags}


# ─── Trash helpers ────────────────────────────────────────────────────────────

def move_to_trash(prompt_id: int) -> int:
    """Move a single prompt to trash. Returns the new trash row id (0 on failure)."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT p.*, f.name AS folder_name
        FROM prompts p LEFT JOIN folders f ON p.folder_id = f.id
        WHERE p.id = ?
        """,
        (prompt_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return 0

    tag_rows = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN prompt_tags pt ON t.id = pt.tag_id
        WHERE pt.prompt_id = ? ORDER BY t.name
        """,
        (prompt_id,),
    ).fetchall()
    tag_names = "|".join(r["name"] for r in tag_rows)

    # Store deleted_at in UTC so purge_expired_trash (which uses SQLite's
    # julianday('now') — also UTC) makes the correct 48-hour comparison.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """
        INSERT INTO trash (title, content, folder_id, folder_name, is_favorite,
                           tag_names, created_at, updated_at, deleted_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            row["title"], row["content"], row["folder_id"], row["folder_name"],
            row["is_favorite"], tag_names, row["created_at"], row["updated_at"], now,
        ),
    )
    trash_id = int(cur.lastrowid or 0)

    conn.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
    if _fts_table_ready(conn):
        conn.execute(f"DELETE FROM {SEARCH_FTS_TABLE} WHERE prompt_id=?", (prompt_id,))

    conn.commit()
    conn.close()
    return trash_id


def bulk_move_to_trash(prompt_ids: list[int]) -> list[int]:
    """Move multiple prompts to trash. Returns list of new trash row ids."""
    if not prompt_ids:
        return []

    conn = get_connection()
    trash_ids: list[int] = []
    # UTC — consistent with julianday('now') used in purge_expired_trash.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for prompt_id in prompt_ids:
        row = conn.execute(
            """
            SELECT p.*, f.name AS folder_name
            FROM prompts p LEFT JOIN folders f ON p.folder_id = f.id
            WHERE p.id = ?
            """,
            (prompt_id,),
        ).fetchone()
        if row is None:
            continue

        tag_rows = conn.execute(
            """
            SELECT t.name FROM tags t
            JOIN prompt_tags pt ON t.id = pt.tag_id
            WHERE pt.prompt_id = ? ORDER BY t.name
            """,
            (prompt_id,),
        ).fetchall()
        tag_names = "|".join(r["name"] for r in tag_rows)

        cur = conn.execute(
            """
            INSERT INTO trash (title, content, folder_id, folder_name, is_favorite,
                               tag_names, created_at, updated_at, deleted_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                row["title"], row["content"], row["folder_id"], row["folder_name"],
                row["is_favorite"], tag_names, row["created_at"], row["updated_at"], now,
            ),
        )
        trash_ids.append(int(cur.lastrowid or 0))

    if prompt_ids:
        placeholders = _make_placeholders(prompt_ids)
        conn.execute(f"DELETE FROM prompts WHERE id IN ({placeholders})", prompt_ids)
        if _fts_table_ready(conn):
            conn.execute(
                f"DELETE FROM {SEARCH_FTS_TABLE} WHERE prompt_id IN ({placeholders})",
                prompt_ids,
            )

    conn.commit()
    conn.close()
    return [tid for tid in trash_ids if tid > 0]


def get_trash_items() -> list[dict]:
    """Return all items currently in trash, newest-deleted first."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, title, content, folder_id, folder_name, is_favorite,
               tag_names, created_at, updated_at, deleted_at
        FROM trash ORDER BY deleted_at DESC
        """
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        item = dict(r)
        item["tags"] = [t for t in (item.get("tag_names") or "").split("|") if t]
        result.append(item)
    return result


def get_trash_count() -> int:
    """Return the number of items currently in trash."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
    conn.close()
    return int(count)


def restore_from_trash(trash_id: int) -> int:
    """Restore a trash item back to prompts. Returns new prompt id (0 on failure)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM trash WHERE id=?", (trash_id,)).fetchone()
    if row is None:
        conn.close()
        return 0

    folder_id = row["folder_id"]
    if folder_id is not None:
        exists = conn.execute("SELECT id FROM folders WHERE id=?", (folder_id,)).fetchone()
        if not exists:
            folder_id = None

    cur = conn.execute(
        """
        INSERT INTO prompts (title, content, folder_id, is_favorite, created_at, updated_at)
        VALUES (?,?,?,?,?,?)
        """,
        (row["title"], row["content"], folder_id, row["is_favorite"],
         row["created_at"], row["updated_at"]),
    )
    new_id = int(cur.lastrowid or 0)

    tag_names = [t for t in (row["tag_names"] or "").split("|") if t]
    for name in tag_names:
        tag_id = ensure_tag(name, conn)
        conn.execute(
            "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag_id) VALUES (?,?)",
            (new_id, tag_id),
        )

    conn.execute("DELETE FROM trash WHERE id=?", (trash_id,))
    _refresh_prompt_search_index(conn, new_id)
    conn.commit()
    conn.close()
    return new_id


def restore_all_from_trash() -> int:
    """Restore all trash items to prompts in a single atomic transaction.

    Returns the number of items restored.
    """
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trash").fetchall()
    if not rows:
        conn.close()
        return 0

    new_ids: list[int] = []
    for row in rows:
        folder_id = row["folder_id"]
        if folder_id is not None:
            exists = conn.execute(
                "SELECT id FROM folders WHERE id=?", (folder_id,)
            ).fetchone()
            if not exists:
                folder_id = None

        cur = conn.execute(
            """
            INSERT INTO prompts (title, content, folder_id, is_favorite, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                row["title"], row["content"], folder_id, row["is_favorite"],
                row["created_at"], row["updated_at"],
            ),
        )
        new_id = int(cur.lastrowid or 0)
        if not new_id:
            continue
        new_ids.append(new_id)

        tag_names = [t for t in (row["tag_names"] or "").split("|") if t]
        for name in tag_names:
            tag_id = ensure_tag(name, conn)
            conn.execute(
                "INSERT OR IGNORE INTO prompt_tags (prompt_id, tag_id) VALUES (?,?)",
                (new_id, tag_id),
            )

    conn.execute("DELETE FROM trash")
    for new_id in new_ids:
        _refresh_prompt_search_index(conn, new_id)
    conn.commit()
    conn.close()
    return len(new_ids)


def clear_trash():
    """Permanently delete all items in trash."""
    conn = get_connection()
    conn.execute("DELETE FROM trash")
    conn.commit()
    conn.close()


def purge_expired_trash():
    """Permanently delete trash items that have exceeded the 48-hour retention window."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM trash WHERE (julianday('now') - julianday(deleted_at)) * 86400 >= ?",
        (TRASH_EXPIRY_HOURS * 3600,),
    )
    conn.commit()
    conn.close()
