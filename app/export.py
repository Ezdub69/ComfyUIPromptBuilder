"""Export a saved Krea 2 prompt, or a saved Krea 2 Assistant output, to a
plain .txt file for opening/copy-pasting outside the app."""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = APP_DIR / "SavedPrompts"


def _safe_filename(name):
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "prompt"
    return cleaned


def _unique_path(directory, stem):
    path = directory / f"{stem}.txt"
    counter = 2
    while path.exists():
        path = directory / f"{stem}_{counter}.txt"
        counter += 1
    return path


def render_text(name, generated_prompt, negative_prompt=None, notes=None):
    lines = [f"Prompt: {name}", ""]
    lines.append("--- Generated Prompt ---")
    lines.append(generated_prompt or "")
    if negative_prompt:
        lines.append("")
        lines.append("--- Negative Prompt ---")
        lines.append(negative_prompt)
    if notes:
        lines.append("")
        lines.append("--- Notes ---")
        lines.append(notes)
    return "\n".join(lines)


def export_to_txt(name, generated_prompt, negative_prompt=None, notes=None):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _unique_path(EXPORT_DIR, _safe_filename(name))
    path.write_text(render_text(name, generated_prompt, negative_prompt, notes), encoding="utf-8")
    return path


def export_saved_row_to_txt(row):
    return export_to_txt(
        row["name"],
        row["generated_prompt"],
        row["negative_prompt"],
        row["notes"],
    )


def render_chat_output_text(name, output_text, system_prompt_name=None, user_text=None, notes=None):
    lines = [f"Prompt: {name}", ""]
    if system_prompt_name:
        lines.append(f"System Prompt: {system_prompt_name}")
        lines.append("")
    if user_text:
        lines.append("--- Input ---")
        lines.append(user_text)
        lines.append("")
    lines.append("--- Output ---")
    lines.append(output_text or "")
    if notes:
        lines.append("")
        lines.append("--- Notes ---")
        lines.append(notes)
    return "\n".join(lines)


def export_chat_output_to_txt(name, output_text, system_prompt_name=None, user_text=None, notes=None):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _unique_path(EXPORT_DIR, _safe_filename(name))
    path.write_text(
        render_chat_output_text(name, output_text, system_prompt_name, user_text, notes),
        encoding="utf-8",
    )
    return path


def export_chat_output_row_to_txt(row):
    return export_chat_output_to_txt(
        row["name"],
        row["output_text"],
        row["system_prompt_name"],
        row["user_text"],
        row["notes"],
    )
