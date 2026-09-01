"""Saved prompts, browsable in one place: Krea 2 Prompt Builder saves
(fields + output) in one section, Krea 2 Assistant saves (chat outputs) in a
second section below it. Each krea2_saved_prompts/chat_saved_outputs row
still lives in its own table (different shapes - see db.py) - this tab just
presents both.
"""

import json

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app import db, export


def _picker_text(value):
    if not value:
        return None
    if isinstance(value, list):
        parts = [v["value"] for v in value if v.get("value")]
        return ", ".join(parts) if parts else None
    return value.get("value")


class SavedTab(QWidget):
    load_requested = Signal(dict)  # Krea 2 field state -> Krea2Tab.set_state
    load_chat_output_requested = Signal(str)  # output text -> Krea 2 Assistant input box

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._entries = []  # [("krea2", row) | ("chat", row) | ("header", label)]

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._show_preview)
        left.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.load_btn = QPushButton("Load")
        self.load_btn.clicked.connect(self.load_selected)
        self.export_btn = QPushButton("Export to .txt")
        self.export_btn.clicked.connect(self.export_selected)
        self.open_folder_btn = QPushButton("Open .txt folder")
        self.open_folder_btn.clicked.connect(self.open_export_folder)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_selected)
        for b in (self.refresh_btn, self.load_btn, self.export_btn, self.open_folder_btn, self.delete_btn):
            btn_row.addWidget(b)
        left.addLayout(btn_row)
        root.addLayout(left, stretch=1)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        root.addWidget(self.preview, stretch=2)

        self.refresh()

    def _add_header(self, label):
        self._entries.append(("header", label))
        item = QListWidgetItem(label)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
        self.list_widget.addItem(item)

    def refresh(self):
        self._entries = []
        self.list_widget.clear()

        self._add_header("— Krea 2 Prompts —")
        krea2_rows = db.list_krea2_prompts(self.conn)
        for row in krea2_rows:
            self._entries.append(("krea2", row))
            self.list_widget.addItem(QListWidgetItem(f"    {row['name']}  ({row['updated_at']})"))
        if not krea2_rows:
            self._entries.append(("header", None))
            self.list_widget.addItem(self._disabled_item("    (none yet)"))

        self._add_header("— Krea 2 Assistant Outputs —")
        chat_rows = db.list_chat_outputs(self.conn)
        for row in chat_rows:
            self._entries.append(("chat", row))
            self.list_widget.addItem(QListWidgetItem(f"    {row['name']}  ({row['created_at']})"))
        if not chat_rows:
            self._entries.append(("header", None))
            self.list_widget.addItem(self._disabled_item("    (none yet)"))

    def _disabled_item(self, text):
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
        return item

    def _selected_entry(self):
        idx = self.list_widget.currentRow()
        if idx < 0 or idx >= len(self._entries):
            return None, None
        return self._entries[idx]

    def _show_preview(self, _idx):
        kind, row = self._selected_entry()
        if kind == "krea2":
            self.preview.setPlainText(self._krea2_preview_text(row))
        elif kind == "chat":
            self.preview.setPlainText(self._chat_preview_text(row))
        else:
            self.preview.setPlainText("")

    def _krea2_preview_text(self, row):
        fields = json.loads(row["fields_json"])
        lines = []
        if row["generated_prompt"]:
            lines.append("--- Generated Prompt ---")
            lines.append(row["generated_prompt"])
            lines.append("")
        if row["negative_prompt"]:
            lines.append("--- Negative Prompt ---")
            lines.append(row["negative_prompt"])
            lines.append("")
        lines.append("--- Fields ---")
        field_labels = [
            ("medium", "Medium / style"),
            ("character", "Character / Subject"),
            ("wardrobe", "Wardrobe / Clothing"),
            ("pose", "Pose & Interaction"),
            ("scene", "Scene / Environment"),
            ("shot_size", "Shot size"),
            ("camera_angle", "Camera angle"),
            ("mood", "Mood / atmosphere"),
        ]
        for key, label in field_labels:
            value = fields.get(key)
            text = _picker_text(value) if key in ("medium", "shot_size", "camera_angle", "mood") else value
            if text:
                lines.append(f"{label}: {text}")
        lines.append(f"Explicit / uncensored: {'yes' if fields.get('nsfw') else 'no'}")
        if row["notes"]:
            lines.append("")
            lines.append("--- Notes ---")
            lines.append(row["notes"])
        return "\n".join(lines)

    def _chat_preview_text(self, row):
        lines = []
        if row["system_prompt_name"]:
            lines.append(f"System Prompt: {row['system_prompt_name']}")
            lines.append("")
        if row["user_text"]:
            lines.append("--- Input ---")
            lines.append(row["user_text"])
            lines.append("")
        lines.append("--- Output ---")
        lines.append(row["output_text"] or "")
        if row["notes"]:
            lines.append("")
            lines.append("--- Notes ---")
            lines.append(row["notes"])
        return "\n".join(lines)

    def load_selected(self):
        kind, row = self._selected_entry()
        if kind is None:
            QMessageBox.information(self, "No selection", "Select a saved prompt first.")
            return
        if kind == "krea2":
            self.load_requested.emit(json.loads(row["fields_json"]))
        elif kind == "chat":
            self.load_chat_output_requested.emit(row["output_text"])

    def export_selected(self):
        kind, row = self._selected_entry()
        if kind is None:
            QMessageBox.information(self, "No selection", "Select a saved prompt first.")
            return
        if kind == "krea2":
            path = export.export_saved_row_to_txt(row)
        else:
            path = export.export_chat_output_row_to_txt(row)
        QMessageBox.information(self, "Exported", f"Wrote:\n{path}")

    def open_export_folder(self):
        export.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(export.EXPORT_DIR)))

    def delete_selected(self):
        kind, row = self._selected_entry()
        if kind is None:
            QMessageBox.information(self, "No selection", "Select a saved prompt first.")
            return
        confirm = QMessageBox.question(self, "Delete saved prompt", f"Delete '{row['name']}'?")
        if confirm != QMessageBox.Yes:
            return
        if kind == "krea2":
            db.delete_krea2_prompt(self.conn, row["id"])
        else:
            db.delete_chat_output(self.conn, row["id"])
        self.refresh()
