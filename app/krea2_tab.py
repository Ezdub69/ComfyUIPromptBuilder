"""Structured Krea2 prompt builder: fields hold state, not a conversation.

Free-text fields (character/wardrobe/pose/scene) cover the infinitely
varied, specific content Krea2 prompts need; picker fields (medium, shot
size, camera angle, mood) cover the finite, reusable vocabulary where a
click beats typing. On Generate, every field's current value is collected
into one labeled fact list and sent as a single stateless LLM call (see
llm_client.py / krea2_prompt_template.py) - correcting a detail means
editing that field and regenerating, not trying to get an LLM to correctly
diff a conversational instruction against a wall of prior prose. That's the
whole reason this tab exists instead of another chat-based rewriter.
"""

import json

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QCheckBox, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app import db, llm_client, krea2_prompt_template
from app.lm_studio_tab import BASE_URL_SETTING_KEY, KREA2_MODEL_SETTING_KEY
from app.spellcheck import SpellCheckHighlighter, SpellCheckTextEdit
from app.widgets import MultiTagPicker, SingleTagPicker


class _CallableWorker(QThread):
    """Runs one zero-arg callable off the UI thread. A synchronous HTTP call
    on PySide6's main thread would freeze the whole app for the duration of
    the request - local LLM calls can take anywhere from a couple seconds to
    a few minutes depending on model size."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as e:
            self.failed.emit(str(e))
        else:
            self.succeeded.emit(result)


class Krea2Tab(QWidget):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._gen_worker = None
        self._vary_worker = None
        self._vary_buttons = []
        self._saved_rows = []
        self._models_by_id = {}  # populated by set_available_models() once the LM Studio tab detects

        root = QVBoxLayout(self)

        # --- Model row -------------------------------------------------------
        # Server address, Detect Models, and which model this tab uses all
        # live on the LM Studio tab now (previously duplicated here and on
        # the Krea 2 Assistant tab, each with its own server field and
        # picker that could drift out of sync). This tab just shows what
        # it's been assigned - refreshed via refresh_model_label() whenever
        # that tab detects or the assignment changes.
        model_row = QHBoxLayout()
        self.model_label = QLabel()
        model_row.addWidget(self.model_label)
        self.nsfw_checkbox = QCheckBox("Explicit / uncensored")
        self.nsfw_checkbox.setChecked(db.get_setting(conn, "krea2_nsfw_default", "1") == "1")
        self.nsfw_checkbox.toggled.connect(
            lambda checked: db.set_setting(conn, "krea2_nsfw_default", "1" if checked else "0")
        )
        model_row.addWidget(self.nsfw_checkbox)
        model_row.addStretch(1)
        root.addLayout(model_row)
        self.refresh_model_label()

        # --- Fields ----------------------------------------------------
        fields_box = QGroupBox("Fields")
        form = QFormLayout(fields_box)

        self.medium = SingleTagPicker(conn, "krea2_medium", show_weight=False)
        form.addRow("Medium / style:", self.medium)

        self._spell_highlighters = []  # keep references - Qt won't, and they'd be GC'd

        self.character_edit = SpellCheckTextEdit()
        self.character_edit.setMaximumHeight(70)
        self.character_edit.setPlaceholderText(
            "Who's in the image - name, identity, physical description...")
        self._spell_highlighters.append(SpellCheckHighlighter(self.character_edit.document()))
        form.addRow("Character / Subject:",
                     self._make_field_row(self.character_edit, "character", "Character / Subject"))

        self.wardrobe_edit = SpellCheckTextEdit()
        self.wardrobe_edit.setMaximumHeight(70)
        self.wardrobe_edit.setPlaceholderText("Clothing, or state of dress...")
        self._spell_highlighters.append(SpellCheckHighlighter(self.wardrobe_edit.document()))
        form.addRow("Wardrobe / Clothing:",
                     self._make_field_row(self.wardrobe_edit, "wardrobe", "Wardrobe / Clothing"))

        self.pose_edit = SpellCheckTextEdit()
        self.pose_edit.setMaximumHeight(70)
        self.pose_edit.setPlaceholderText("Stance, limb positions, gaze, actions...")
        self._spell_highlighters.append(SpellCheckHighlighter(self.pose_edit.document()))
        form.addRow("Pose & Interaction:",
                     self._make_field_row(self.pose_edit, "pose", "Pose & Interaction"))

        self.scene_edit = SpellCheckTextEdit()
        self.scene_edit.setMaximumHeight(70)
        self.scene_edit.setPlaceholderText("Setting, background, spatial layout...")
        self._spell_highlighters.append(SpellCheckHighlighter(self.scene_edit.document()))
        form.addRow("Scene / Environment:",
                     self._make_field_row(self.scene_edit, "scene", "Scene / Environment"))

        self.shot_size = SingleTagPicker(conn, "krea2_shot_size", show_weight=False)
        form.addRow("Shot size:", self.shot_size)

        self.camera_angle = SingleTagPicker(conn, "krea2_camera_angle", show_weight=False)
        form.addRow("Camera angle:", self.camera_angle)

        self.lens = SingleTagPicker(conn, "krea2_lens", show_weight=False)
        form.addRow("Lens:", self.lens)

        self.camera = SingleTagPicker(conn, "krea2_camera", show_weight=False)
        form.addRow("Camera:", self.camera)

        self.aperture = SingleTagPicker(conn, "krea2_aperture", show_weight=False)
        form.addRow("Aperture:", self.aperture)

        self.lighting = MultiTagPicker(conn, "krea2_lighting", show_weight=False)
        form.addRow("Lighting:", self.lighting)

        self.genre = MultiTagPicker(conn, "krea2_genre", show_weight=False)
        form.addRow("Genre / aesthetic:", self.genre)

        self.mood = MultiTagPicker(conn, "krea2_mood", show_weight=False)
        form.addRow("Mood / atmosphere:", self.mood)

        root.addWidget(fields_box)

        # --- Generate ----------------------------------------------------
        gen_row = QHBoxLayout()
        self.generate_btn = QPushButton("Generate")
        self.generate_btn.clicked.connect(self.generate)
        gen_row.addWidget(self.generate_btn)
        self.status_label = QLabel("")
        gen_row.addWidget(self.status_label)
        gen_row.addStretch(1)
        root.addLayout(gen_row)

        # --- Output ----------------------------------------------------
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Generated Prompt:"))
        out_row.addStretch(1)
        copy_prompt_btn = QPushButton("Copy")
        copy_prompt_btn.clicked.connect(lambda: self._copy(self.output_edit))
        out_row.addWidget(copy_prompt_btn)
        root.addLayout(out_row)
        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setMinimumHeight(160)
        root.addWidget(self.output_edit)

        # --- Save / Load -------------------------------------------------
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("Name:"))
        self.save_name_edit = QLineEdit()
        save_row.addWidget(self.save_name_edit, stretch=1)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_current)
        save_row.addWidget(save_btn)

        self.load_combo = QComboBox()
        save_row.addWidget(self.load_combo, stretch=1)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self.load_selected)
        save_row.addWidget(load_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_saved)
        save_row.addWidget(refresh_btn)
        root.addLayout(save_row)

        self.refresh_saved()

    def reload_pickers(self):
        """Re-pull option lists from the DB - call when the Library tab may
        have added/edited/moved a krea2_* tag since these were built."""
        self.medium.reload()
        self.shot_size.reload()
        self.camera_angle.reload()
        self.lens.reload()
        self.camera.reload()
        self.aperture.reload()
        self.lighting.reload()
        self.genre.reload()
        self.mood.reload()

    def _make_field_row(self, edit, field_key, field_label):
        """Wraps a free-text field with a "Vary" button that asks the LLM
        for a fresh alternative for just this field, using the other fields
        as context - see _vary_field(). This is the "just change the scene"
        case: a smaller, targeted regenerate that only touches one field
        instead of the whole prompt."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, stretch=1)
        vary_btn = QPushButton("Vary")
        vary_btn.setToolTip(f"Ask the LLM for a different {field_label}, keeping the other fields the same")
        vary_btn.clicked.connect(lambda: self._vary_field(field_key, field_label, edit))
        layout.addWidget(vary_btn)
        self._vary_buttons.append(vary_btn)
        return container

    def _set_busy(self, busy):
        self.generate_btn.setEnabled(not busy)
        for b in self._vary_buttons:
            b.setEnabled(not busy)
        self.status_label.setText("Working..." if busy else "")

    # --- Model assignment (owned by the LM Studio tab) ----------------------

    def refresh_model_label(self):
        model = db.get_setting(self.conn, KREA2_MODEL_SETTING_KEY)
        if not model:
            self.model_label.setText("Model: none assigned yet - set on the LM Studio tab")
            return
        info = self._models_by_id.get(model)
        if info is None:
            # Persisted from a previous session (or never detected this
            # one) - showing the bare name with no status would look like
            # a confirmed, ready-to-use model when it might be unloaded,
            # or might not even exist on the server any more.
            status, ready = "not detected this session", False
        elif info.get("loaded"):
            status, ready = "loaded", True
        elif info.get("loaded") is False:
            status, ready = "not currently loaded - will load automatically when used", False
        else:
            status, ready = "load status unknown", False
        # The "go change it on the LM Studio tab" hint is only useful when
        # something might need attention - once it's confirmed loaded and
        # ready, repeating it every time is just noise.
        hint = "" if ready else " - set on the LM Studio tab"
        self.model_label.setText(f"Model: {model} ({status}){hint}")

    def set_available_models(self, models):
        """Connected to the LM Studio tab's models_detected signal - this
        tab doesn't keep its own model list (no local picker to populate
        any more), just a cache of model -> loaded status for the label."""
        self._models_by_id = {m["id"]: m for m in models}
        self.refresh_model_label()

    # --- Generate ------------------------------------------------------

    def _field_values(self):
        """Ordered (key, label, value) triples for every currently-populated
        field. Shared by _collect_facts() (full generate) and _vary_field()
        (which needs the same list minus the one field being varied)."""
        values = []
        medium = self.medium.get_selection()
        if medium:
            values.append(("medium", "Medium", medium["value"]))
        character = self.character_edit.toPlainText().strip()
        if character:
            values.append(("character", "Character / Subject", character))
        wardrobe = self.wardrobe_edit.toPlainText().strip()
        if wardrobe:
            values.append(("wardrobe", "Wardrobe / Clothing", wardrobe))
        pose = self.pose_edit.toPlainText().strip()
        if pose:
            values.append(("pose", "Pose & Interaction", pose))
        scene = self.scene_edit.toPlainText().strip()
        if scene:
            values.append(("scene", "Scene / Environment", scene))
        shot_size = self.shot_size.get_selection()
        if shot_size:
            values.append(("shot_size", "Shot size", shot_size["value"]))
        camera_angle = self.camera_angle.get_selection()
        if camera_angle:
            values.append(("camera_angle", "Camera angle", camera_angle["value"]))
        lens = self.lens.get_selection()
        if lens:
            values.append(("lens", "Lens", lens["value"]))
        camera = self.camera.get_selection()
        if camera:
            values.append(("camera", "Camera", camera["value"]))
        aperture = self.aperture.get_selection()
        if aperture:
            values.append(("aperture", "Aperture", aperture["value"]))
        lighting = self.lighting.get_selection()
        if lighting:
            values.append(("lighting", "Lighting", ", ".join(l["value"] for l in lighting)))
        genre = self.genre.get_selection()
        if genre:
            values.append(("genre", "Genre / aesthetic", ", ".join(g["value"] for g in genre)))
        mood = self.mood.get_selection()
        if mood:
            values.append(("mood", "Mood", ", ".join(m["value"] for m in mood)))
        return values

    def _collect_facts(self, exclude_key=None):
        return "\n".join(
            f"{label}: {value}" for key, label, value in self._field_values() if key != exclude_key
        )

    def generate(self):
        base_url = db.get_setting(self.conn, BASE_URL_SETTING_KEY, llm_client.DEFAULT_BASE_URL)
        model = db.get_setting(self.conn, KREA2_MODEL_SETTING_KEY)
        if not model:
            QMessageBox.warning(
                self, "No model assigned",
                "Go to the LM Studio tab, click Detect Models, and assign one to Krea 2 Prompt Builder.",
            )
            return
        facts = self._collect_facts()
        if not facts:
            QMessageBox.warning(self, "Nothing to generate", "Fill in at least one field first.")
            return

        system_prompt = krea2_prompt_template.build_system_prompt(self.nsfw_checkbox.isChecked())
        facts_text = f"Facts for this image:\n\n{facts}\n\nProduce the Krea 2 prompt now."

        self._set_busy(True)
        self._gen_worker = _CallableWorker(
            lambda: llm_client.complete(base_url, model, system_prompt, facts_text)
        )
        self._gen_worker.succeeded.connect(self._on_generate_succeeded)
        self._gen_worker.failed.connect(self._on_generate_failed)
        self._gen_worker.start()

    def _on_generate_succeeded(self, prompt_text):
        self._set_busy(False)
        self.output_edit.setPlainText(prompt_text)

    def _on_generate_failed(self, error):
        self._set_busy(False)
        QMessageBox.critical(self, "Generation failed", error)

    # --- Vary a single field --------------------------------------------

    def _vary_field(self, field_key, field_label, text_edit):
        base_url = db.get_setting(self.conn, BASE_URL_SETTING_KEY, llm_client.DEFAULT_BASE_URL)
        model = db.get_setting(self.conn, KREA2_MODEL_SETTING_KEY)
        if not model:
            QMessageBox.warning(
                self, "No model assigned",
                "Go to the LM Studio tab, click Detect Models, and assign one to Krea 2 Prompt Builder.",
            )
            return

        context_facts = self._collect_facts(exclude_key=field_key)
        current_value = text_edit.toPlainText().strip()
        system_prompt = krea2_prompt_template.build_vary_field_prompt(
            field_label, self.nsfw_checkbox.isChecked()
        )
        user_message = (
            f"Other fields for context:\n\n{context_facts or '(none given)'}\n\n"
            f"Current {field_label} (replace this with something genuinely different): "
            f"{current_value or '(empty - suggest something fitting)'}"
        )

        self._set_busy(True)
        self._vary_worker = _CallableWorker(
            lambda: llm_client.complete(base_url, model, system_prompt, user_message, max_tokens=300)
        )
        self._vary_worker.succeeded.connect(lambda text: self._on_vary_succeeded(text_edit, text))
        self._vary_worker.failed.connect(self._on_vary_failed)
        self._vary_worker.start()

    def _on_vary_succeeded(self, text_edit, text):
        self._set_busy(False)
        text_edit.setPlainText(text.strip())

    def _on_vary_failed(self, error):
        self._set_busy(False)
        QMessageBox.critical(self, "Vary failed", error)

    # --- Copy ------------------------------------------------------

    def _copy(self, text_edit):
        QApplication.clipboard().setText(text_edit.toPlainText())

    # --- Save / Load -------------------------------------------------

    def get_state(self):
        return {
            "medium": self.medium.get_selection(),
            "character": self.character_edit.toPlainText(),
            "wardrobe": self.wardrobe_edit.toPlainText(),
            "pose": self.pose_edit.toPlainText(),
            "scene": self.scene_edit.toPlainText(),
            "shot_size": self.shot_size.get_selection(),
            "camera_angle": self.camera_angle.get_selection(),
            "lens": self.lens.get_selection(),
            "camera": self.camera.get_selection(),
            "aperture": self.aperture.get_selection(),
            "lighting": self.lighting.get_selection(),
            "genre": self.genre.get_selection(),
            "mood": self.mood.get_selection(),
            "nsfw": self.nsfw_checkbox.isChecked(),
        }

    def set_state(self, state):
        self.medium.set_selection(state.get("medium"))
        self.character_edit.setPlainText(state.get("character", ""))
        self.wardrobe_edit.setPlainText(state.get("wardrobe", ""))
        self.pose_edit.setPlainText(state.get("pose", ""))
        self.scene_edit.setPlainText(state.get("scene", ""))
        self.shot_size.set_selection(state.get("shot_size"))
        self.camera_angle.set_selection(state.get("camera_angle"))
        self.lens.set_selection(state.get("lens"))
        self.camera.set_selection(state.get("camera"))
        self.aperture.set_selection(state.get("aperture"))
        self.lighting.set_selection(state.get("lighting"))
        self.genre.set_selection(state.get("genre"))
        self.mood.set_selection(state.get("mood"))
        self.nsfw_checkbox.setChecked(state.get("nsfw", True))

    def save_current(self):
        name = self.save_name_edit.text().strip()
        if not name:
            name, ok = QInputDialog.getText(self, "Save prompt", "Name for this saved prompt:")
            if not ok or not name.strip():
                return
            name = name.strip()
        db.save_krea2_prompt(
            self.conn,
            name=name,
            fields_json=json.dumps(self.get_state()),
            generated_prompt=self.output_edit.toPlainText(),
        )
        self.refresh_saved()
        QMessageBox.information(self, "Saved", f"Saved '{name}'.")

    def refresh_saved(self):
        self._saved_rows = db.list_krea2_prompts(self.conn)
        current = self.load_combo.currentText()
        self.load_combo.blockSignals(True)
        self.load_combo.clear()
        for row in self._saved_rows:
            self.load_combo.addItem(row["name"])
        idx = self.load_combo.findText(current)
        self.load_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.load_combo.blockSignals(False)

    def load_selected(self):
        name = self.load_combo.currentText()
        row = next((r for r in self._saved_rows if r["name"] == name), None)
        if not row:
            QMessageBox.information(self, "No selection", "Select a saved prompt first.")
            return
        self.set_state(json.loads(row["fields_json"]))
        self.output_edit.setPlainText(row["generated_prompt"] or "")
        self.save_name_edit.setText(row["name"])
