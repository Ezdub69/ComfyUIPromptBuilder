"""Loads the built-in system prompts used by the Krea 2 Assistant tab: one
vision prompt (caption an attached image, faithfully and without censoring
explicit content) and one rewrite prompt (convert/edit a prompt into Krea 2
format). Originally drew on a wider set of prompts covering several other
model ecosystems (SDXL, SD 1.5, Z-Image, FLUX.2 Klein, Qwen-Image) and a
free-text "generate" mode - those were dropped once the app consolidated
around Krea 2 as its only target, since the Krea 2 Prompt Builder tab's
structured fields already cover text-to-prompt generation better than a
chat-based mode ever could.

Data lives in data/prompt_registry.json - the live, editable copy (loaded
below and what the app actually uses). data/prompt_registry_defaults.json
is a separate, never-modified reference copy of the same content, purely so
"Reset to Default" (see save_prompt_text/reset_prompt_text) has something to
restore from after someone edits a prompt's text in the app.
"""

import json
import os
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "prompt_registry.json"
DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "data" / "prompt_registry_defaults.json"

with open(DATA_PATH, encoding="utf-8") as _f:
    PROMPT_REGISTRY = json.load(_f)

BY_NAME = {entry["name"]: entry for entry in PROMPT_REGISTRY}

MODE_LABELS = {
    "vision": "Vision — requires image",
    "generate": "Generate — text to prompt",
    "rewrite": "Rewrite — paste your prompt",
}


def get(name):
    return BY_NAME.get(name)


def names_by_mode():
    """Ordered {mode: [name, ...]} - preserves registry order within each
    mode, for building a grouped dropdown."""
    grouped = {"vision": [], "generate": [], "rewrite": []}
    for entry in PROMPT_REGISTRY:
        grouped.setdefault(entry["mode"], []).append(entry["name"])
    return grouped


def save_prompt_text(name, new_text):
    """Edits one entry's system prompt text in place and persists the whole
    registry back to data/prompt_registry.json - the Krea 2 Assistant tab's
    "Save Changes" action. BY_NAME's entries are the same dict objects held
    in PROMPT_REGISTRY (not copies), so this also takes effect immediately
    for anything already holding a reference to that entry, with no app
    restart needed."""
    entry = BY_NAME.get(name)
    if entry is None:
        raise KeyError(name)
    entry["text"] = new_text
    _write_registry()


def get_default_text(name):
    """The original, shipped text for one entry - independent of whatever
    is currently saved in the live registry."""
    with open(DEFAULTS_PATH, encoding="utf-8") as f:
        defaults = json.load(f)
    entry = next((e for e in defaults if e["name"] == name), None)
    return entry["text"] if entry else None


def reset_prompt_text(name):
    """Overwrites an entry's live text with its shipped default and
    persists that - the "Reset to Default" action. Returns the restored
    text so the caller can refresh its display without a second lookup."""
    default_text = get_default_text(name)
    if default_text is None:
        raise KeyError(name)
    save_prompt_text(name, default_text)
    return default_text


def _write_registry():
    tmp_path = DATA_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(PROMPT_REGISTRY, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, DATA_PATH)
