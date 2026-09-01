"""Chat interface to whatever model is currently loaded in LM Studio,
scoped down to the two things the Krea 2 workflow actually needs (see
prompt_registry.py) - captioning an attached image, or rewriting/converting
a prompt into Krea 2 format.

Shares this app's DB, styling, and widget conventions, and saves assistant
outputs via chat_saved_outputs (db.py) for later reuse.
"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from app import db, image_utils, llm_client, prompt_registry
from app.lm_studio_tab import BASE_URL_SETTING_KEY
from app.spellcheck import SpellCheckHighlighter, SpellCheckTextEdit

DEFAULT_PROMPT_NAME = prompt_registry.PROMPT_REGISTRY[0]["name"] if prompt_registry.PROMPT_REGISTRY else None

# Human-readable labels for each mode's disabled header row in the dropdown.
MODE_GROUP_LABELS = {
    "vision": "Vision (Image Analysis)",
    "generate": "Generation (Text-to-Prompt)",
    "rewrite": "Rewrite (Prompt-to-Prompt)",
}


class _StreamWorker(QThread):
    """Runs one streaming chat completion off the UI thread, emitting each
    text chunk as it arrives so the bot bubble can fill in live rather than
    appearing all at once when the whole response finally completes."""

    delta = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)
    aborted = Signal()

    def __init__(self, base_url, messages, model, temperature, top_p, max_tokens,
                 presence_penalty, top_k, parent=None):
        super().__init__(parent)
        self._base_url = base_url
        self._messages = messages
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._presence_penalty = presence_penalty
        self._top_k = top_k
        self._abort_flag = False

    def request_abort(self):
        self._abort_flag = True

    def run(self):
        try:
            full_text = llm_client.stream_chat_completion(
                self._base_url, self._messages, model=self._model,
                temperature=self._temperature, top_p=self._top_p,
                max_tokens=self._max_tokens, presence_penalty=self._presence_penalty,
                top_k=self._top_k,
                on_delta=lambda chunk: self.delta.emit(chunk),
                should_abort=lambda: self._abort_flag,
            )
        except llm_client.Aborted:
            self.aborted.emit()
        except Exception as e:
            self.failed.emit(str(e))
        else:
            self.finished_ok.emit(full_text)


def _estimate_tokens(text):
    return (len(text) + 3) // 4 if text else 0


def _estimate_message_tokens(messages):
    total = 0
    for m in messages:
        total += 8  # per-message framing cushion
        content = m.get("content")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += _estimate_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    total += 800  # rough vision-token cushion for one downscaled image
    return total


class ChatBubble(QFrame):
    def __init__(self, role, text, image_path=None, parent=None):
        super().__init__(parent)
        self.role = role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # Light-theme bubble styling: a red accent stripe and monospace text
        # on bot replies, subtle neutral tint on user turns.
        if role == "user":
            self.setStyleSheet(
                "ChatBubble { background: #eef2f7; border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "ChatBubble { background: #fafafa; border-radius: 8px; "
                "border-left: 3px solid #ef4444; }"
            )

        if image_path:
            pix = QPixmap(image_path)
            if not pix.isNull():
                thumb = QLabel()
                thumb.setPixmap(pix.scaledToWidth(220, Qt.SmoothTransformation))
                layout.addWidget(thumb)

        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if role == "bot":
            font = self.text_label.font()
            font.setFamily("Consolas")
            self.text_label.setFont(font)
        layout.addWidget(self.text_label)

    def set_text(self, text):
        self.text_label.setText(text)


class Krea2AssistantTab(QWidget):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._models_by_id = {}
        self._stream_worker = None
        self._waiting = False
        self._attached_image_path = None
        self._attached_image_data_url = None
        self._history = []  # [{"role": "user"|"bot", "text": str, "image": data_url|None}]
        self._saved_rows = []

        root = QVBoxLayout(self)

        # --- Model row -------------------------------------------------------
        # Server address, Detect, and which model handles Vision vs Rewrite
        # are all owned by the LM Studio tab now (previously duplicated
        # here and on the Krea 2 Prompt Builder tab, each with its own
        # server field/picker(s) that could drift out of sync). Vision mode
        # always uses whatever's assigned there as the vision model,
        # Rewrite mode always uses the rewrite model - independent of each
        # other, so two models loaded in LM Studio at once is never
        # ambiguous about which one a given message goes to.
        model_row = QHBoxLayout()
        self.model_label = QLabel()
        model_row.addWidget(self.model_label)
        model_row.addStretch(1)
        root.addLayout(model_row)
        self.refresh_model_label()

        # --- Prompt selector row --------------------------------------------
        prompt_row = QHBoxLayout()
        prompt_row.addWidget(QLabel("System prompt:"))
        self.prompt_combo = QComboBox()
        self._populate_prompt_combo()
        self.prompt_combo.currentTextChanged.connect(self._on_prompt_changed)
        prompt_row.addWidget(self.prompt_combo, stretch=1)
        self.mode_badge = QLabel("")
        prompt_row.addWidget(self.mode_badge)
        self.sys_prompt_toggle_btn = QPushButton("Show System Prompt ▸")
        self.sys_prompt_toggle_btn.setCheckable(True)
        self.sys_prompt_toggle_btn.toggled.connect(self._on_sys_prompt_toggle)
        prompt_row.addWidget(self.sys_prompt_toggle_btn)
        root.addLayout(prompt_row)

        # --- System prompt preview / edit (collapsed by default) -------------
        self.sys_prompt_panel = QWidget()
        sys_prompt_layout = QVBoxLayout(self.sys_prompt_panel)
        sys_prompt_layout.setContentsMargins(0, 0, 0, 0)
        self.sys_prompt_edit = QPlainTextEdit()
        self.sys_prompt_edit.setMinimumHeight(160)
        self.sys_prompt_edit.setMaximumHeight(280)
        sys_prompt_layout.addWidget(self.sys_prompt_edit)
        sys_prompt_btn_row = QHBoxLayout()
        sys_prompt_btn_row.addStretch(1)
        self.sys_prompt_save_btn = QPushButton("Save Changes")
        self.sys_prompt_save_btn.clicked.connect(self.save_system_prompt_edit)
        sys_prompt_btn_row.addWidget(self.sys_prompt_save_btn)
        self.sys_prompt_reset_btn = QPushButton("Reset to Default")
        self.sys_prompt_reset_btn.clicked.connect(self.reset_system_prompt)
        sys_prompt_btn_row.addWidget(self.sys_prompt_reset_btn)
        sys_prompt_layout.addLayout(sys_prompt_btn_row)
        self.sys_prompt_panel.setVisible(False)
        root.addWidget(self.sys_prompt_panel)

        # --- Image attach row ------------------------------------------------
        image_row = QHBoxLayout()
        self.attach_btn = QPushButton("Attach Image")
        self.attach_btn.clicked.connect(self.attach_image)
        image_row.addWidget(self.attach_btn)
        self.image_thumb = QLabel()
        self.image_thumb.setFixedHeight(40)
        image_row.addWidget(self.image_thumb)
        self.remove_image_btn = QPushButton("Remove Image")
        self.remove_image_btn.clicked.connect(self.clear_attachment)
        self.remove_image_btn.setVisible(False)
        image_row.addWidget(self.remove_image_btn)
        image_row.addStretch(1)
        root.addLayout(image_row)

        # --- Chat area ------------------------------------------------------
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        chat_container = QWidget()
        self.chat_layout = QVBoxLayout(chat_container)
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(chat_container)
        root.addWidget(self.chat_scroll, stretch=1)

        # --- Input row ------------------------------------------------------
        input_row = QHBoxLayout()
        self.input_edit = SpellCheckTextEdit()
        self.input_edit.setMaximumHeight(90)
        self.input_edit.setPlaceholderText("Type a message...")
        self._input_highlighter = SpellCheckHighlighter(self.input_edit.document())
        input_row.addWidget(self.input_edit, stretch=1)
        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(80)
        input_row.addWidget(self.status_label)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        input_row.addWidget(self.send_btn)
        # Separate from Send (which used to turn into "Stop" mid-response)
        # - the same button silently doubling as an abort trigger meant an
        # accidental second click while a response was still streaming
        # would cancel it, and the resulting "[Stopped]" text appended to
        # the reply reads like the model itself wrote it. A dedicated
        # button that's disabled except while actually generating can't be
        # clicked by accident the same way.
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.abort_generation)
        input_row.addWidget(self.stop_btn)
        root.addLayout(input_row)

        # --- Generation parameters -------------------------------------------
        # Trimmed down to the two knobs actually worth touching by hand
        # (temperature, max tokens) plus context size - Top K and presence
        # penalty were leftover per-model tuning nothing here ever needed
        # adjusting. Context size is pre-filled from what LM Studio reports
        # the active mode's model is actually loaded/running with (see
        # _refresh_context_for_current_mode) - it's a real property of the
        # running model, not a free generation choice, so it's set
        # automatically rather than guessed at.
        params_box = QGroupBox("Generation parameters")
        params_row = QHBoxLayout(params_box)
        params_row.addWidget(QLabel("Temperature:"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setValue(0.7)
        params_row.addWidget(self.temp_spin)

        params_row.addWidget(QLabel("Top P:"))
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.05)
        self.top_p_spin.setValue(0.9)
        params_row.addWidget(self.top_p_spin)

        params_row.addWidget(QLabel("Max tokens:"))
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(64, 8192)
        self.max_tokens_spin.setValue(1024)
        params_row.addWidget(self.max_tokens_spin)

        params_row.addWidget(QLabel("Context size:"))
        self.context_spin = QSpinBox()
        self.context_spin.setRange(512, 1000000)
        self.context_spin.setValue(int(db.get_setting(conn, "krea2_assistant_max_context", "8192")))
        params_row.addWidget(self.context_spin)

        params_row.addStretch(1)
        root.addWidget(params_box)

        # --- Bottom row: clear / save / saved outputs -----------------------
        bottom_row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear Chat")
        self.clear_btn.clicked.connect(self.clear_chat)
        bottom_row.addWidget(self.clear_btn)
        self.save_btn = QPushButton("Save Last Response")
        self.save_btn.clicked.connect(self.save_last_response)
        bottom_row.addWidget(self.save_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(QLabel("Saved outputs:"))
        self.saved_combo = QComboBox()
        self.saved_combo.setMinimumWidth(220)
        bottom_row.addWidget(self.saved_combo)
        insert_btn = QPushButton("Insert into Input")
        insert_btn.clicked.connect(self.insert_saved_output)
        bottom_row.addWidget(insert_btn)
        copy_saved_btn = QPushButton("Copy")
        copy_saved_btn.clicked.connect(self.copy_saved_output)
        bottom_row.addWidget(copy_saved_btn)
        delete_saved_btn = QPushButton("Delete")
        delete_saved_btn.clicked.connect(self.delete_saved_output)
        bottom_row.addWidget(delete_saved_btn)
        refresh_saved_btn = QPushButton("Refresh")
        refresh_saved_btn.clicked.connect(self.refresh_saved_outputs)
        bottom_row.addWidget(refresh_saved_btn)
        root.addLayout(bottom_row)

        self._apply_mode_ui()
        self.refresh_saved_outputs()

    # --- Prompt selector ---------------------------------------------------

    def _populate_prompt_combo(self):
        """Builds a grouped dropdown - QComboBox has no native <optgroup>, so
        each group gets a bold, disabled (unselectable/unclickable) header
        item instead, styled as "— Vision (Image Analysis) —" section
        labels."""
        model = QStandardItemModel(self.prompt_combo)
        grouped = prompt_registry.names_by_mode()
        for mode in ("vision", "generate", "rewrite"):
            names = grouped.get(mode, [])
            if not names:
                continue
            header = QStandardItem(f"— {MODE_GROUP_LABELS[mode]} —")
            header.setFlags(header.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            model.appendRow(header)
            for name in names:
                model.appendRow(QStandardItem(name))
        self.prompt_combo.setModel(model)
        if DEFAULT_PROMPT_NAME:
            idx = self.prompt_combo.findText(DEFAULT_PROMPT_NAME)
            if idx >= 0:
                self.prompt_combo.setCurrentIndex(idx)

    def _current_prompt_entry(self):
        return prompt_registry.get(self.prompt_combo.currentText())

    def _current_mode(self):
        entry = self._current_prompt_entry()
        return entry["mode"] if entry else "vision"

    def _on_prompt_changed(self, _name):
        self._apply_mode_ui()
        entry = self._current_prompt_entry()
        if entry:
            self.max_tokens_spin.setValue(entry.get("rec", 1024))
        self._refresh_context_for_current_mode()
        if self.sys_prompt_panel.isVisible():
            self._refresh_system_prompt_box()

    # --- System prompt preview / edit ---------------------------------------

    def _on_sys_prompt_toggle(self, checked):
        self.sys_prompt_panel.setVisible(checked)
        self.sys_prompt_toggle_btn.setText("Hide System Prompt ▾" if checked else "Show System Prompt ▸")
        if checked:
            self._refresh_system_prompt_box()

    def _refresh_system_prompt_box(self):
        entry = self._current_prompt_entry()
        self.sys_prompt_edit.setPlainText(entry["text"] if entry else "")

    def save_system_prompt_edit(self):
        entry = self._current_prompt_entry()
        if not entry:
            return
        prompt_registry.save_prompt_text(entry["name"], self.sys_prompt_edit.toPlainText())
        QMessageBox.information(self, "Saved", f"Updated the system prompt for '{entry['name']}'.")

    def reset_system_prompt(self):
        entry = self._current_prompt_entry()
        if not entry:
            return
        confirm = QMessageBox.question(
            self, "Reset to default",
            f"Discard any edits to '{entry['name']}' and restore its original built-in text?",
        )
        if confirm != QMessageBox.Yes:
            return
        default_text = prompt_registry.reset_prompt_text(entry["name"])
        self.sys_prompt_edit.setPlainText(default_text)
        QMessageBox.information(self, "Reset", f"Restored the default system prompt for '{entry['name']}'.")

    def _apply_mode_ui(self):
        mode = self._current_mode()
        self.mode_badge.setText(prompt_registry.MODE_LABELS.get(mode, ""))
        is_vision = mode == "vision"
        self.attach_btn.setEnabled(is_vision)
        if not is_vision:
            self.clear_attachment()
        if mode == "vision":
            self.input_edit.setPlaceholderText("Type 'Extract' or ask about the image...")
        elif mode == "rewrite":
            self.input_edit.setPlaceholderText("Paste the prompt you want rewritten...")
        else:
            self.input_edit.setPlaceholderText("Describe what you want a prompt for...")

    # --- Model assignment (owned by the LM Studio tab) ----------------------

    def _describe_assigned_model(self, setting_key):
        """Returns (display_text, ready) - ready is True only once the
        model is confirmed loaded, so refresh_model_label() can drop the
        "go change it on the LM Studio tab" hint when there's nothing left
        to do."""
        model = db.get_setting(self.conn, setting_key)
        if not model:
            return "(none assigned)", False
        info = self._models_by_id.get(model)
        if info is None:
            # Persisted from a previous session (or never detected this
            # one) - showing the bare name with no status would look like
            # a confirmed, ready-to-use model when it might be unloaded,
            # or might not even exist on the server any more.
            return f"{model} (not detected this session)", False
        if info.get("loaded"):
            return f"{model} (loaded)", True
        if info.get("loaded") is False:
            return f"{model} (not currently loaded)", False
        return model, False

    def refresh_model_label(self):
        vision_text, vision_ready = self._describe_assigned_model("krea2_assistant_vision_model")
        rewrite_text, rewrite_ready = self._describe_assigned_model("krea2_assistant_rewrite_model")
        # Only nag about the LM Studio tab while at least one of the two
        # still needs attention - once both are confirmed loaded, repeating
        # it every time is just noise.
        hint = "" if (vision_ready and rewrite_ready) else "   -   set on the LM Studio tab"
        self.model_label.setText(
            f"Vision model: {vision_text}   |   Rewrite model: {rewrite_text}{hint}"
        )

    def set_available_models(self, models):
        """Called by MainWindow whenever the LM Studio tab detects models -
        this tab doesn't keep its own pickers any more, just a cache of
        model -> context length/loaded status for whichever model ends up
        assigned (see _refresh_context_for_current_mode), plus its label."""
        self._models_by_id = {m["id"]: m for m in models}
        self._refresh_context_for_current_mode()
        self.refresh_model_label()

    def _current_model_name(self):
        """Which model actually handles the next message - the vision
        model in Vision mode, the rewrite model otherwise, both assigned on
        the LM Studio tab. Keeping these independent is the point: with
        more than one model loaded in LM Studio at once, this is what used
        to be ambiguous."""
        key = "krea2_assistant_vision_model" if self._current_mode() == "vision" else "krea2_assistant_rewrite_model"
        return db.get_setting(self.conn, key)

    def _refresh_context_for_current_mode(self):
        """Pulls the real running context length from LM Studio for
        whichever model is active for the current mode, rather than
        guessing at it per model family - answers "can it get settings
        directly from what model is running" for the one setting that's
        actually a server-side fact rather than a free generation choice."""
        info = self._models_by_id.get(self._current_model_name())
        if info and info.get("context_length"):
            length = max(self.context_spin.minimum(), min(self.context_spin.maximum(), info["context_length"]))
            self.context_spin.setValue(length)

    # --- Image attach --------------------------------------------------------

    def attach_image(self):
        if self._current_mode() != "vision":
            return
        start_dir = db.get_setting(self.conn, "krea2_assistant_last_image_dir", "")
        if start_dir and not os.path.isdir(start_dir):
            start_dir = ""  # remembered folder got moved/deleted - fall back to Qt's own default
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach image", start_dir, "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        db.set_setting(self.conn, "krea2_assistant_last_image_dir", os.path.dirname(path))
        try:
            data_url = image_utils.downscale_image_to_data_url(path, max_dim=768, quality=85)
        except Exception as e:
            QMessageBox.critical(self, "Image failed", str(e))
            return
        self._attached_image_path = path
        self._attached_image_data_url = data_url
        pix = QPixmap(path)
        if not pix.isNull():
            self.image_thumb.setPixmap(pix.scaledToHeight(40, Qt.SmoothTransformation))
        self.remove_image_btn.setVisible(True)
        if not self.input_edit.toPlainText().strip():
            self.input_edit.setPlainText("Extract tags/description based on system prompt.")

    def clear_attachment(self):
        self._attached_image_path = None
        self._attached_image_data_url = None
        self.image_thumb.clear()
        self.remove_image_btn.setVisible(False)

    # --- Chat rendering --------------------------------------------------------

    def _append_bubble(self, role, text, image_path=None):
        bubble = ChatBubble(role, text, image_path)
        # insert before the trailing stretch item
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_chat(self):
        self._history.clear()
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # --- Send / Stop --------------------------------------------------------

    def send_message(self):
        text = self.input_edit.toPlainText().strip()
        if not text or self._waiting:
            return
        base_url = db.get_setting(self.conn, BASE_URL_SETTING_KEY, llm_client.DEFAULT_BASE_URL)

        image_path = self._attached_image_path
        image_data_url = self._attached_image_data_url
        self.input_edit.clear()
        self.clear_attachment()

        self._history.append({"role": "user", "text": text, "image": image_data_url})
        self._append_bubble("user", text, image_path)

        self._trim_history()

        messages = self._build_messages_for_api()
        self._waiting = True
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("Analyzing..." if self._current_mode() == "vision" else "Generating...")
        bot_bubble = self._append_bubble("bot", "")
        self._bot_bubble = bot_bubble
        self._bot_text = ""

        self._stream_worker = _StreamWorker(
            base_url, messages, self._current_model_name(),
            self.temp_spin.value(), self.top_p_spin.value(), self.max_tokens_spin.value(),
            None,
            None,
        )
        self._stream_worker.delta.connect(self._on_delta)
        self._stream_worker.finished_ok.connect(self._on_stream_finished)
        self._stream_worker.failed.connect(self._on_stream_failed)
        self._stream_worker.aborted.connect(self._on_stream_aborted)
        self._stream_worker.start()

    def abort_generation(self):
        if self._stream_worker:
            self._stream_worker.request_abort()

    def _on_delta(self, chunk):
        self._bot_text += chunk
        self._bot_bubble.set_text(self._bot_text)
        self._scroll_to_bottom()

    def _finish_waiting(self):
        self._waiting = False
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("")

    def _on_stream_finished(self, full_text):
        self._history.append({"role": "bot", "text": full_text.strip(), "image": None})
        self._bot_bubble.set_text(full_text.strip())
        self._finish_waiting()

    def _on_stream_failed(self, error):
        err = f"[Error: {error}]"
        text = (self._bot_text + "\n" + err) if self._bot_text else err
        self._bot_bubble.set_text(text)
        self._history.append({"role": "bot", "text": text, "image": None})
        self._finish_waiting()
        QMessageBox.critical(self, "Generation failed", error)

    def _on_stream_aborted(self):
        text = (self._bot_text + "\n[Stopped]") if self._bot_text else "[Stopped]"
        self._bot_bubble.set_text(text)
        self._history.append({"role": "bot", "text": text, "image": None})
        self._finish_waiting()

    # --- Message assembly / context management --------------------------------

    def _build_messages_for_api(self):
        system_prompt = self._current_prompt_entry()
        system_text = system_prompt["text"] if system_prompt else ""
        messages = [{"role": "system", "content": system_text}]

        last_user_idx = None
        for i, m in enumerate(self._history):
            if m["role"] == "user":
                last_user_idx = i

        for i, m in enumerate(self._history):
            if m["role"] == "bot":
                messages.append({"role": "assistant", "content": m["text"]})
                continue
            if m["image"] and i == last_user_idx:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": m["text"] or "Analyze this image."},
                        {"type": "image_url", "image_url": {"url": m["image"]}},
                    ],
                })
            elif m["image"]:
                messages.append({
                    "role": "user",
                    "content": f"[Earlier turn included an image] {m['text'] or ''}".strip(),
                })
            else:
                messages.append({"role": "user", "content": m["text"]})
        return messages

    def _trim_history(self):
        """Drop oldest turns until the estimated request fits comfortably
        within the configured context size, so a long chat doesn't overflow
        the model's context window and error out instead of replying."""
        max_context = self.context_spin.value()
        reply_budget = self.max_tokens_spin.value()
        safety_margin = 256
        budget = max_context - reply_budget - safety_margin
        if budget <= 0:
            return
        while len(self._history) > 1:
            messages = self._build_messages_for_api()
            if _estimate_message_tokens(messages) <= budget:
                break
            self._history.pop(0)

    # --- Save / load outputs --------------------------------------------------

    def save_last_response(self):
        last_bot = next((m for m in reversed(self._history) if m["role"] == "bot"), None)
        if not last_bot:
            QMessageBox.information(self, "Nothing to save", "No response yet to save.")
            return
        last_user = next((m for m in reversed(self._history) if m["role"] == "user"), None)
        name, ok = QInputDialog.getText(self, "Save output", "Name for this saved output:")
        if not ok or not name.strip():
            return
        db.save_chat_output(
            self.conn,
            name=name.strip(),
            output_text=last_bot["text"],
            system_prompt_name=self.prompt_combo.currentText(),
            user_text=last_user["text"] if last_user else None,
        )
        self.refresh_saved_outputs()
        QMessageBox.information(self, "Saved", f"Saved '{name.strip()}'.")

    def refresh_saved_outputs(self):
        self._saved_rows = db.list_chat_outputs(self.conn)
        current = self.saved_combo.currentText()
        self.saved_combo.blockSignals(True)
        self.saved_combo.clear()
        for row in self._saved_rows:
            self.saved_combo.addItem(row["name"])
        idx = self.saved_combo.findText(current)
        self.saved_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.saved_combo.blockSignals(False)

    def _selected_saved_row(self):
        name = self.saved_combo.currentText()
        return next((r for r in self._saved_rows if r["name"] == name), None)

    def insert_saved_output(self):
        row = self._selected_saved_row()
        if not row:
            return
        self.input_edit.setPlainText(row["output_text"])

    def load_output_text(self, text):
        """Called from the Saved Prompts tab's "Load" action on a Krea 2
        Assistant entry - drops the saved output straight into the input
        box, ready to continue riffing on or send as-is to a rewrite
        prompt."""
        self.input_edit.setPlainText(text)

    def copy_saved_output(self):
        row = self._selected_saved_row()
        if not row:
            return
        QApplication.clipboard().setText(row["output_text"])

    def delete_saved_output(self):
        row = self._selected_saved_row()
        if not row:
            return
        confirm = QMessageBox.question(self, "Delete saved output", f"Delete '{row['name']}'?")
        if confirm == QMessageBox.Yes:
            db.delete_chat_output(self.conn, row["id"])
            self.refresh_saved_outputs()
