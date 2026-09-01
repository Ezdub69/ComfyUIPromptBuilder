"""SQLite layer for the ComfyUI Prompt Builder.

Schema: sections -> subcategories -> tags (currently just the "krea2"
section's picker fields - medium, shot size, camera angle, lens, camera,
aperture, lighting, genre/aesthetic, mood - used by krea2_tab.py), a flat
settings table, and krea2_saved_prompts for saved Krea 2 tab state/output.

The old comma-tag Builder tab (and its saved_prompts/saved_characters
tables and ~1,200-tag Danbooru-style library) was removed 2026-08-30 - it
wasn't useful for Krea 2 prompting. This module still supports adding new
picker categories for the Krea 2 tab later: add an entry to SECTIONS/
SUBCATEGORIES (or just use the Library tab once a section exists), tags are
manageable generically via add_tag/update_tag/move_tag/delete_tag.
"""

import json
import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DB_PATH = APP_DIR / "data" / "promptbuilder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subcategories (
    id INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES sections(id),
    key TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    select_mode TEXT NOT NULL CHECK(select_mode IN ('single','multi')),
    supports_weight INTEGER NOT NULL DEFAULT 1,
    gendered INTEGER NOT NULL DEFAULT 0,
    ui_sort_order INTEGER NOT NULL,
    build_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    subcategory_id INTEGER NOT NULL REFERENCES subcategories(id),
    value TEXT NOT NULL,
    label TEXT NOT NULL,
    gender_scope TEXT,
    default_weight REAL NOT NULL DEFAULT 1.0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS krea2_saved_prompts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    generated_prompt TEXT,
    negative_prompt TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS chat_saved_outputs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    system_prompt_name TEXT,
    user_text TEXT,
    output_text TEXT NOT NULL,
    notes TEXT
);
"""

# (section_key, section_label, section_sort_order)
SECTIONS = [
    ("krea2", "Krea 2", 1),
]

# (subcategory_key, section_key, label, select_mode, gendered, ui_sort_order, build_order)
# build_order is unused for Krea 2 - the Krea 2 tab assembles a labeled fact
# list itself and hands it to an LLM, it doesn't go through a fixed-order
# string assembler the way the old Builder tab did.
SUBCATEGORIES = [
    ("krea2_medium", "krea2", "Medium / style", "single", 0, 1, 1),
    ("krea2_shot_size", "krea2", "Shot size", "single", 0, 2, 2),
    ("krea2_camera_angle", "krea2", "Camera angle", "single", 0, 3, 3),
    ("krea2_lens", "krea2", "Lens", "single", 0, 4, 4),
    ("krea2_camera", "krea2", "Camera", "single", 0, 5, 5),
    ("krea2_aperture", "krea2", "Aperture", "single", 0, 6, 6),
    ("krea2_lighting", "krea2", "Lighting", "multi", 0, 7, 7),
    ("krea2_genre", "krea2", "Genre / aesthetic", "multi", 0, 8, 8),
    ("krea2_mood", "krea2", "Mood / atmosphere", "multi", 0, 9, 9),
]

# Seed options for the four Krea2 picker fields above. Small, curated lists -
# unlike the booru-tag vocabulary these deliberately favor short natural-
# language phrases, since they get woven into flowing prose rather than
# comma-joined.
KREA2_SEED_TAGS = {
    "krea2_medium": [
        "cinematic photograph", "editorial photograph", "candid photograph",
        "cinematic film still", "digital painting", "concept art",
        "anime illustration", "3D render", "vintage film photograph",
        "fantasy oil painting", "portrait photograph",
    ],
    "krea2_shot_size": [
        "extreme close-up", "close-up", "medium close-up", "medium shot",
        "medium wide shot", "wide shot", "extreme wide shot", "full-body shot",
        "establishing shot", "two-shot", "cowboy shot", "choker shot",
    ],
    "krea2_camera_angle": [
        "eye-level angle", "low-angle", "high-angle", "over-the-shoulder",
        "bird's-eye view", "dutch angle", "front-facing, square to camera",
        "three-quarter angle", "worm's-eye view", "extreme high angle",
        "extreme low angle",
    ],
    # Tied loosely to shot size (wide shots pair with wide lenses, close-ups
    # with portrait/macro lenses) but kept as its own field rather than
    # auto-derived - the pairing is a rough convention, not a fixed rule.
    "krea2_lens": [
        "wide-angle lens, 14-24mm", "standard lens, 35-50mm",
        "portrait lens, 85-135mm", "macro lens, 100mm",
    ],
    "krea2_camera": [
        "Sony A7R IV", "Canon EOS R5", "Fujifilm X-T4", "Leica M10",
        "Hasselblad X1D medium format", "DSLR", "Polaroid instant camera",
        "disposable camera",
    ],
    "krea2_aperture": [
        "f/1.4", "f/2", "f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16", "f/22",
    ],
    "krea2_lighting": [
        "soft daylight", "harsh midday sun", "cloudy diffuse light", "golden hour",
        "moonlight", "candlelight", "chiaroscuro", "spotlight", "rim light",
        "silhouette lighting", "high contrast lighting", "neon glow",
        "studio softbox lighting", "tungsten warm light", "firelight", "backlit",
    ],
    "krea2_genre": [
        "cyberpunk", "steampunk", "gothic", "fantasy", "minimalist",
        "futuristic", "vintage/retro",
    ],
    "krea2_mood": [
        "romantic", "sensual", "dramatic", "tense", "playful", "melancholic",
        "regal", "intimate", "commanding", "serene", "ominous", "triumphant",
        "seductive", "erotic", "sultry", "lustful", "provocative", "teasing",
        "yearning", "submissive", "dominant", "shameless", "vulnerable", "defiant",
        "peaceful", "chaotic", "exciting", "surreal", "whimsical", "gritty", "epic",
    ],
}


def get_connection(path=None):
    """path defaults to the real app database - pass an explicit path (e.g.
    a throwaway temp file) for tests, so they never read or write the
    user's actual saved prompts/settings."""
    path = Path(path) if path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    if conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0:
        _seed_structure(conn)
    if conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0:
        for subcat_key, values in KREA2_SEED_TAGS.items():
            _insert_tags(conn, subcat_key, values)
        conn.commit()
    _dedupe_existing_tags(conn)
    # A plain UNIQUE table constraint on (subcategory_id, value, gender_scope)
    # doesn't actually dedupe rows where gender_scope is NULL - SQL treats
    # every NULL as distinct from every other NULL for uniqueness purposes.
    # A unique index over COALESCE(gender_scope, '') sidesteps that. Must
    # run after dedupe - creating it over already-duplicated data would fail.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_dedupe "
        "ON tags(subcategory_id, value, COALESCE(gender_scope, ''))"
    )
    conn.commit()
    _migrate_v2(conn)


def _dedupe_existing_tags(conn):
    """One-time (but safe to re-run) cleanup for databases created before the
    NULL-uniqueness bug above was found: every initialize_db() call had been
    re-inserting the gap-filled tags (age_modifiers, freckles, dimples_of_venus)
    since their gender_scope is NULL. Also cleans up genuine pre-existing
    underscore/space near-duplicates carried over from nodes.py's own source
    lists (e.g. 'collared_shirt' and 'collared shirt'), preferring the
    underscore-joined literal Danbooru-tag form when exactly one of a pair
    has one - that's the form this checkpoint was actually trained on."""
    rows = conn.execute(
        """SELECT t.id, t.value, t.subcategory_id, t.gender_scope,
                  LOWER(REPLACE(t.value, '_', ' ')) AS norm
           FROM tags t"""
    ).fetchall()
    groups = {}
    for r in rows:
        key = (r["subcategory_id"], r["gender_scope"], r["norm"])
        groups.setdefault(key, []).append(r)

    for group in groups.values():
        if len(group) <= 1:
            continue
        underscored = [r for r in group if "_" in r["value"]]
        keep = underscored[0] if len(underscored) == 1 else min(group, key=lambda r: r["id"])
        for r in group:
            if r["id"] != keep["id"]:
                conn.execute("DELETE FROM tags WHERE id = ?", (r["id"],))
    conn.commit()


def _migrate_v2(conn):
    """Idempotent migration, safe to run on every startup.

    Ensures the current SECTIONS/SUBCATEGORIES/KREA2_SEED_TAGS exist (so a
    new picker category added to those lists in the future just appears on
    next launch), and - as a one-time cleanup for databases created before
    2026-08-30 - drops the old comma-tag Builder tab's tables and prunes any
    non-krea2 sections/subcategories/tags left over from it."""
    existing_section_keys = {row["key"] for row in conn.execute("SELECT key FROM sections")}
    for key, label, sort_order in SECTIONS:
        if key not in existing_section_keys:
            conn.execute(
                "INSERT INTO sections (key, label, sort_order) VALUES (?, ?, ?)",
                (key, label, sort_order),
            )
    conn.commit()

    section_ids = {row["key"]: row["id"] for row in conn.execute("SELECT id, key FROM sections")}
    existing_keys = {row["key"] for row in conn.execute("SELECT key FROM subcategories")}

    for key, section_key, label, select_mode, gendered, ui_order, build_order in SUBCATEGORIES:
        if key not in existing_keys:
            conn.execute(
                """INSERT INTO subcategories
                   (section_id, key, label, select_mode, supports_weight, gendered, ui_sort_order, build_order)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (section_ids[section_key], key, label, select_mode, gendered, ui_order, build_order),
            )
        else:
            conn.execute(
                """UPDATE subcategories
                   SET label = ?, select_mode = ?, gendered = ?, ui_sort_order = ?, build_order = ?
                   WHERE key = ?""",
                (label, select_mode, gendered, ui_order, build_order, key),
            )
    conn.commit()

    for subcat_key, values in KREA2_SEED_TAGS.items():
        _insert_tags(conn, subcat_key, values)
    conn.commit()

    # One-time carry-over: the Krea 2 Prompt Builder and Krea 2 Assistant
    # tabs used to each have their own LM Studio server address setting
    # (krea2_llm_base_url / pe_llm_base_url) - now there's one shared
    # address, owned by the LM Studio tab. Seed it from whichever old one
    # was set rather than making existing users retype their address.
    #
    # Must actually delete the legacy keys once carried over, not just
    # leave them sitting there - otherwise this "if not get_setting(new)"
    # check keeps re-triggering forever on every future startup once
    # something deliberately clears the new key back to "" (e.g. Unload
    # All Models), silently resurrecting a value the user just cleared.
    for legacy_key in ("krea2_llm_base_url", "pe_llm_base_url"):
        legacy_value = get_setting(conn, legacy_key)
        if legacy_value is not None:
            if not get_setting(conn, "lm_studio_base_url"):
                set_setting(conn, "lm_studio_base_url", legacy_value)
            _delete_setting(conn, legacy_key)

    # One-time carry-over: the "pe_*" settings prefix was a leftover from
    # this tab's original name (before it became Krea 2 Assistant) - moves
    # any existing values to their krea2_assistant_* replacements so
    # existing installs don't lose their saved model picks/context/folder.
    # Same "must delete the legacy key" reasoning as above.
    for legacy_key, new_key in (
        ("pe_vision_model", "krea2_assistant_vision_model"),
        ("pe_rewrite_model", "krea2_assistant_rewrite_model"),
        ("pe_max_context", "krea2_assistant_max_context"),
        ("pe_last_image_dir", "krea2_assistant_last_image_dir"),
    ):
        legacy_value = get_setting(conn, legacy_key)
        if legacy_value is not None:
            if not get_setting(conn, new_key):
                set_setting(conn, new_key, legacy_value)
            _delete_setting(conn, legacy_key)

    # One-time prune: remove any section (and its subcategories/tags) not in
    # the current SECTIONS list - i.e. everything from the old Builder tab's
    # library on a pre-2026-08-30 database. Cascades tags -> subcategories ->
    # sections manually since the schema doesn't declare ON DELETE CASCADE.
    keep_keys = {key for key, _, _ in SECTIONS}
    stale_section_ids = [
        row["id"] for row in conn.execute("SELECT id, key FROM sections")
        if row["key"] not in keep_keys
    ]
    for section_id in stale_section_ids:
        conn.execute(
            """DELETE FROM tags WHERE subcategory_id IN
               (SELECT id FROM subcategories WHERE section_id = ?)""",
            (section_id,),
        )
        conn.execute("DELETE FROM subcategories WHERE section_id = ?", (section_id,))
        conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
    conn.commit()

    # One-time drop: the old Builder tab's save tables, no longer used.
    conn.execute("DROP TABLE IF EXISTS saved_prompts")
    conn.execute("DROP TABLE IF EXISTS saved_characters")
    conn.commit()


def _seed_structure(conn):
    for key, label, sort_order in SECTIONS:
        conn.execute(
            "INSERT INTO sections (key, label, sort_order) VALUES (?, ?, ?)",
            (key, label, sort_order),
        )
    section_ids = {row["key"]: row["id"] for row in conn.execute("SELECT id, key FROM sections")}
    for key, section_key, label, select_mode, gendered, ui_order, build_order in SUBCATEGORIES:
        conn.execute(
            """INSERT INTO subcategories
               (section_id, key, label, select_mode, supports_weight, gendered, ui_sort_order, build_order)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (section_ids[section_key], key, label, select_mode, gendered, ui_order, build_order),
        )
    conn.commit()


def _insert_tags(conn, subcat_key, values, gender_scope=None):
    subcat_id = conn.execute(
        "SELECT id FROM subcategories WHERE key = ?", (subcat_key,)
    ).fetchone()["id"]
    for i, value in enumerate(values):
        conn.execute(
            """INSERT OR IGNORE INTO tags (subcategory_id, value, label, gender_scope, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (subcat_id, value, value, gender_scope, i),
        )


# --- Query helpers -----------------------------------------------------

def list_sections(conn):
    return conn.execute("SELECT * FROM sections ORDER BY sort_order").fetchall()


def list_subcategories(conn, section_key=None):
    if section_key:
        return conn.execute(
            """SELECT sc.* FROM subcategories sc
               JOIN sections s ON s.id = sc.section_id
               WHERE s.key = ? ORDER BY sc.ui_sort_order""",
            (section_key,),
        ).fetchall()
    return conn.execute("SELECT * FROM subcategories ORDER BY ui_sort_order").fetchall()


def get_subcategory(conn, key):
    return conn.execute("SELECT * FROM subcategories WHERE key = ?", (key,)).fetchone()


def list_tags(conn, subcategory_key, gender=None, active_only=True):
    sql = """SELECT t.* FROM tags t
             JOIN subcategories sc ON sc.id = t.subcategory_id
             WHERE sc.key = ?"""
    params = [subcategory_key]
    if active_only:
        sql += " AND t.is_active = 1"
    if gender:
        sql += " AND (t.gender_scope IS NULL OR t.gender_scope = ?)"
        params.append(gender)
    sql += " ORDER BY LOWER(t.value)"
    return conn.execute(sql, params).fetchall()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _delete_setting(conn, key):
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


# --- Library editing CRUD ----------------------------------------------

def add_tag(conn, subcategory_key, value, label=None, gender_scope=None, weight=1.0):
    subcat = get_subcategory(conn, subcategory_key)
    if subcat is None:
        raise ValueError(f"Unknown subcategory: {subcategory_key}")
    conn.execute(
        """INSERT INTO tags (subcategory_id, value, label, gender_scope, default_weight, sort_order)
           VALUES (?, ?, ?, ?, ?, 9999)""",
        (subcat["id"], value, label or value, gender_scope, weight),
    )
    conn.commit()


def update_tag(conn, tag_id, value=None, label=None, gender_scope=None, default_weight=None):
    fields, params = [], []
    if value is not None:
        fields.append("value = ?"); params.append(value)
    if label is not None:
        fields.append("label = ?"); params.append(label)
    if gender_scope is not None:
        fields.append("gender_scope = ?"); params.append(None if gender_scope == "" else gender_scope)
    if default_weight is not None:
        fields.append("default_weight = ?"); params.append(default_weight)
    if not fields:
        return
    params.append(tag_id)
    conn.execute(f"UPDATE tags SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def move_tag(conn, tag_id, new_subcategory_key):
    subcat = get_subcategory(conn, new_subcategory_key)
    if subcat is None:
        raise ValueError(f"Unknown subcategory: {new_subcategory_key}")
    conn.execute("UPDATE tags SET subcategory_id = ? WHERE id = ?", (subcat["id"], tag_id))
    conn.commit()


def delete_tag(conn, tag_id):
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


def list_all_tags_with_subcategory(conn):
    return conn.execute(
        """SELECT t.id, t.value, t.label, t.gender_scope, t.default_weight,
                  sc.key AS subcategory_key, sc.label AS subcategory_label,
                  s.key AS section_key, s.label AS section_label
           FROM tags t
           JOIN subcategories sc ON sc.id = t.subcategory_id
           JOIN sections s ON s.id = sc.section_id
           ORDER BY s.sort_order, sc.ui_sort_order, LOWER(t.value)"""
    ).fetchall()


# --- Krea2 saved prompts -------------------------------------------------

def save_krea2_prompt(conn, name, fields_json, generated_prompt=None, negative_prompt=None, notes=None):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO krea2_saved_prompts
           (name, created_at, updated_at, fields_json, generated_prompt, negative_prompt, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, now, now, fields_json, generated_prompt, negative_prompt, notes),
    )
    conn.commit()


def list_krea2_prompts(conn):
    return conn.execute("SELECT * FROM krea2_saved_prompts ORDER BY updated_at DESC").fetchall()


def get_krea2_prompt(conn, prompt_id):
    return conn.execute("SELECT * FROM krea2_saved_prompts WHERE id = ?", (prompt_id,)).fetchone()


def delete_krea2_prompt(conn, prompt_id):
    conn.execute("DELETE FROM krea2_saved_prompts WHERE id = ?", (prompt_id,))
    conn.commit()


# --- Krea 2 Assistant saved outputs ---------------------------------------
# One row per assistant response the user chose to keep - the "extract tags/
# description from image" and "prompt from description" results a chat turn
# produces are throwaway otherwise (LM Studio itself keeps no history once
# the app is closed).

def save_chat_output(conn, name, output_text, system_prompt_name=None, user_text=None, notes=None):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO chat_saved_outputs
           (name, created_at, system_prompt_name, user_text, output_text, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, now, system_prompt_name, user_text, output_text, notes),
    )
    conn.commit()


def list_chat_outputs(conn):
    return conn.execute("SELECT * FROM chat_saved_outputs ORDER BY created_at DESC").fetchall()


def get_chat_output(conn, output_id):
    return conn.execute("SELECT * FROM chat_saved_outputs WHERE id = ?", (output_id,)).fetchone()


def delete_chat_output(conn, output_id):
    conn.execute("DELETE FROM chat_saved_outputs WHERE id = ?", (output_id,))
    conn.commit()
