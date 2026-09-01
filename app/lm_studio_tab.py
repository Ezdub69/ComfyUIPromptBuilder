"""Single home for the LM Studio connection: server address, Detect Models,
Unload All Models, and which model each Krea 2 tab actually uses -
previously duplicated across the Krea 2 Prompt Builder and Krea 2 Assistant
tabs (each with its own server field, Detect button, and model picker(s),
all able to drift out of sync with each other). This tab now owns all of
that; the other two just display which model they've been assigned
(read-only, "set on the LM Studio tab") and read it fresh from settings
whenever they actually send a request.
"""

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from app import db, llm_client
from app.widgets import NoScrollComboBox

BASE_URL_SETTING_KEY = "lm_studio_base_url"
KREA2_MODEL_SETTING_KEY = "krea2_llm_model"
VISION_MODEL_SETTING_KEY = "krea2_assistant_vision_model"
REWRITE_MODEL_SETTING_KEY = "krea2_assistant_rewrite_model"
ASSIGNMENTS_CLEARED_SETTING_KEY = "lm_studio_assignments_cleared"


class _CallableWorker(QThread):
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


class LMStudioTab(QWidget):
    # Emitted with the raw fetch_models() list every time detection
    # succeeds (including the re-detect after an unload) - Krea2Tab and
    # Krea2AssistantTab listen for this to refresh their model-info cache
    # and status labels.
    models_detected = Signal(list)
    # Emitted whenever one of the three assignment dropdowns below changes -
    # separate from models_detected since an assignment can change without
    # a fresh detect (e.g. just picking a different already-known model).
    assignments_changed = Signal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._detect_worker = None
        self._unload_worker = None
        self._load_worker = None
        # True right after "Unload All Models" deliberately blanks the
        # assignments - suppresses the normal "auto-pick a sensible default"
        # convenience on the next Detect, so a deliberate blank slate stays
        # blank instead of immediately being refilled. Cleared the moment
        # the user manually assigns anything again. Persisted to settings
        # (not just an in-memory flag) - otherwise restarting the app after
        # an Unload loses the "stay blank" intent and the very next Detect
        # click on the new session silently refills everything again.
        self._suppress_auto_assign = db.get_setting(conn, ASSIGNMENTS_CLEARED_SETTING_KEY) == "1"

        # Closing the app doesn't touch LM Studio - whatever was loaded
        # before is still loaded after a normal restart. If there's already
        # a real assignment from a previous session, it's worth quietly
        # checking on startup whether it's still there, rather than
        # defaulting to blank "no models detected" wording that's only
        # actually true for a fresh install or right after Unload All
        # Models. See _auto_detect_on_launch, called at the end of __init__.
        has_saved_assignment = any(
            db.get_setting(conn, key)
            for key in (KREA2_MODEL_SETTING_KEY, VISION_MODEL_SETTING_KEY, REWRITE_MODEL_SETTING_KEY)
        )

        root = QVBoxLayout(self)

        conn_row = QHBoxLayout()
        conn_row.addWidget(QLabel("LM Studio server:"))
        self.server_edit = QLineEdit(db.get_setting(conn, BASE_URL_SETTING_KEY, llm_client.DEFAULT_BASE_URL))
        self.server_edit.editingFinished.connect(self._save_server_setting)
        conn_row.addWidget(self.server_edit, stretch=1)
        self.detect_btn = QPushButton("Detect Models")
        self.detect_btn.clicked.connect(self.detect_models)
        conn_row.addWidget(self.detect_btn)
        self.load_btn = QPushButton("Load Assigned Models")
        self.load_btn.setToolTip(
            "Explicitly loads whichever model is assigned to each Krea 2 tab below, "
            "so they're ready immediately instead of loading on first use."
        )
        self.load_btn.clicked.connect(self.load_assigned_models)
        conn_row.addWidget(self.load_btn)
        self.unload_btn = QPushButton("Unload All Models")
        self.unload_btn.clicked.connect(self.unload_all_models)
        conn_row.addWidget(self.unload_btn)
        root.addLayout(conn_row)

        self.status_label = QLabel(
            "Checking whether your previously-assigned models are still loaded in LM Studio..."
            if has_saved_assignment else
            "No models detected yet - click Detect Models, then assign one to each Krea 2 "
            "tab below."
        )
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        # --- Model assignments -----------------------------------------------
        # What each Krea 2 tab actually uses - chosen here, not on those
        # tabs, so there's exactly one place to look and one place to
        # change it.
        assign_box = QGroupBox("Model assignments")
        assign_form = QFormLayout(assign_box)

        self.krea2_model_combo = NoScrollComboBox()
        self.krea2_model_combo.setMinimumWidth(280)
        self.krea2_model_combo.currentIndexChanged.connect(
            lambda _i: self._on_assignment_changed(self.krea2_model_combo, KREA2_MODEL_SETTING_KEY)
        )
        assign_form.addRow("Krea 2 Prompt Builder:", self.krea2_model_combo)

        self.vision_model_combo = NoScrollComboBox()
        self.vision_model_combo.setMinimumWidth(280)
        self.vision_model_combo.currentIndexChanged.connect(
            lambda _i: self._on_assignment_changed(self.vision_model_combo, VISION_MODEL_SETTING_KEY)
        )
        assign_form.addRow("Krea 2 Assistant - Vision:", self.vision_model_combo)

        self.rewrite_model_combo = NoScrollComboBox()
        self.rewrite_model_combo.setMinimumWidth(280)
        self.rewrite_model_combo.currentIndexChanged.connect(
            lambda _i: self._on_assignment_changed(self.rewrite_model_combo, REWRITE_MODEL_SETTING_KEY)
        )
        assign_form.addRow("Krea 2 Assistant - Rewrite:", self.rewrite_model_combo)

        root.addWidget(assign_box)

        # Preload each combo with its last-saved assignment (as the only
        # item) so the other tabs' labels show something sensible even
        # before the first Detect Models click of this session - or the
        # explicit "nothing assigned" placeholder if there isn't one (a
        # fresh install, or right after Unload All Models cleared it).
        for combo, key in (
            (self.krea2_model_combo, KREA2_MODEL_SETTING_KEY),
            (self.vision_model_combo, VISION_MODEL_SETTING_KEY),
            (self.rewrite_model_combo, REWRITE_MODEL_SETTING_KEY),
        ):
            saved = db.get_setting(conn, key)
            combo.addItem(saved if saved else "(none assigned)", saved or None)
        self._refresh_load_btn_enabled()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Model", "Type", "Status", "Context length"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        root.addWidget(self.table, stretch=1)

        if has_saved_assignment:
            self._auto_detect_on_launch()

    # --- Auto-detect on launch ------------------------------------------------

    def _auto_detect_on_launch(self):
        """Same call as a manual Detect Models click, just fired
        automatically at startup - so "already loaded, nothing to do" is
        discovered without a click, and "not loaded any more" is found out
        immediately rather than only once the user goes looking. A failure
        here (LM Studio not running yet, wrong address, etc.) is shown
        inline rather than as a popup - this is a quiet background check,
        not something the user asked for right this second."""
        base_url = self.base_url()
        if not base_url:
            return
        self._set_busy(True)
        self.detect_btn.setText("Detecting...")
        self._detect_worker = _CallableWorker(lambda: llm_client.fetch_models(base_url))
        self._detect_worker.succeeded.connect(self._on_detect_succeeded)
        self._detect_worker.failed.connect(self._on_auto_detect_failed)
        self._detect_worker.start()

    def _on_auto_detect_failed(self, error):
        self._set_busy(False)
        self.detect_btn.setText("Detect Models")
        self.status_label.setText(
            "Couldn't reach LM Studio automatically on startup - click Detect Models once "
            f"it's running. ({error})"
        )

    # --- Settings persistence --------------------------------------------

    def _save_server_setting(self):
        db.set_setting(self.conn, BASE_URL_SETTING_KEY, self.server_edit.text().strip())

    def base_url(self):
        return self.server_edit.text().strip()

    # --- Model assignments ---------------------------------------------------

    def _on_assignment_changed(self, combo, setting_key):
        value = combo.currentData()
        db.set_setting(self.conn, setting_key, value or "")
        if value:
            # A real, manual assignment - the "just cleared everything"
            # moment is over, resume normal auto-pick convenience for
            # whichever of the other two combos still isn't assigned. Must
            # persist this too, not just the in-memory flag - otherwise a
            # restart forgets the manual pick was made and treats the
            # session as still "just cleared" again.
            self._suppress_auto_assign = False
            db.set_setting(self.conn, ASSIGNMENTS_CLEARED_SETTING_KEY, "0")
        self._refresh_load_btn_enabled()
        self.assignments_changed.emit()

    def _refresh_load_btn_enabled(self):
        has_assignment = any(
            db.get_setting(self.conn, key)
            for key in (KREA2_MODEL_SETTING_KEY, VISION_MODEL_SETTING_KEY, REWRITE_MODEL_SETTING_KEY)
        )
        self.load_btn.setEnabled(has_assignment)

    def _populate_assignment_combo(self, combo, candidates, setting_key):
        """Item text is the model id, plus " (loaded)" for ones LM Studio
        reports as already running - but the underlying value stored and
        sent to the API (combo.currentData()) is always the bare id. A
        blank "(none assigned)" placeholder is always item 0 (data None),
        so "nothing picked" is a real, visible state instead of silently
        landing on whatever happens to be first in the list."""
        saved = db.get_setting(self.conn, setting_key)
        current_id = combo.currentData() or saved or None
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(none assigned)", None)
        for m in candidates:
            label = f"{m['id']} (loaded)" if m.get("loaded") else m["id"]
            combo.addItem(label, m["id"])
        combo.blockSignals(False)

        idx = combo.findData(current_id) if current_id else -1
        if idx < 0 and not self._suppress_auto_assign:
            # No remembered pick (or it's gone) - default to a loaded model
            # over an unloaded one, so the common case needs no extra wait.
            # Not done right after an explicit Unload All Models, though -
            # that's meant to leave a real blank slate, not just relabel it.
            loaded = next((m for m in candidates if m.get("loaded")), None)
            default_id = loaded["id"] if loaded else (candidates[0]["id"] if candidates else None)
            idx = combo.findData(default_id) if default_id else 0
        if idx < 0:
            idx = 0  # explicit blank state
        combo.setCurrentIndex(idx)
        db.set_setting(self.conn, setting_key, combo.currentData() or "")

    # --- Detect ------------------------------------------------------------

    def detect_models(self):
        base_url = self.base_url()
        if not base_url:
            QMessageBox.warning(self, "No server address", "Enter a server address first.")
            return
        self._save_server_setting()
        self._set_busy(True)
        self.detect_btn.setText("Detecting...")
        self._detect_worker = _CallableWorker(lambda: llm_client.fetch_models(base_url))
        self._detect_worker.succeeded.connect(self._on_detect_succeeded)
        self._detect_worker.failed.connect(self._on_detect_failed)
        self._detect_worker.start()

    def _on_detect_succeeded(self, models):
        self._set_busy(False)
        self.detect_btn.setText("Detect Models")
        self._populate_table(models)

        loaded = [m for m in models if m["loaded"]]
        # loaded == [] is ambiguous on its own: it means either "the server
        # told us, and the answer is zero" or "this server can't tell us at
        # all" (the plain /v1/models fallback, where loaded is None for
        # every model, not False) - those need different wording, not the
        # same "didn't report" message regardless of which is true.
        knows_load_state = any(m["loaded"] is not None for m in models)
        if not models:
            self.status_label.setText(
                "Connected, but the server reported no chat/vision models at all."
            )
        elif loaded:
            names = ", ".join(m["id"] for m in loaded)
            self.status_label.setText(f"{len(models)} model(s) total - currently loaded: {names}")
        elif knows_load_state:
            self.status_label.setText(f"{len(models)} model(s) total - none currently loaded.")
        else:
            self.status_label.setText(
                f"{len(models)} model(s) available (this server doesn't report load state)."
            )

        vision_candidates = [m for m in models if m.get("type") == "vlm"] or models
        self._populate_assignment_combo(self.krea2_model_combo, models, KREA2_MODEL_SETTING_KEY)
        self._populate_assignment_combo(self.vision_model_combo, vision_candidates, VISION_MODEL_SETTING_KEY)
        self._populate_assignment_combo(self.rewrite_model_combo, models, REWRITE_MODEL_SETTING_KEY)
        self._refresh_load_btn_enabled()

        self.models_detected.emit(models)
        self.assignments_changed.emit()

    def _on_detect_failed(self, error):
        self._set_busy(False)
        self.detect_btn.setText("Detect Models")
        QMessageBox.critical(self, "Connection failed", error)

    def _populate_table(self, models):
        self.table.setRowCount(len(models))
        for row, m in enumerate(models):
            status = "Loaded" if m["loaded"] else ("Available" if m["loaded"] is False else "Unknown")
            type_label = "Vision" if m.get("type") == "vlm" else "Text"
            context = str(m["context_length"]) if m.get("context_length") else ""
            for col, text in enumerate([m["id"], type_label, status, context]):
                item = QTableWidgetItem(text)
                self.table.setItem(row, col, item)

    # --- Unload --------------------------------------------------------------

    def unload_all_models(self):
        base_url = self.base_url()
        if not base_url:
            QMessageBox.warning(self, "No server address", "Enter a server address first.")
            return
        confirm = QMessageBox.question(
            self, "Unload all models",
            "Unload every currently loaded model from LM Studio, and clear the model "
            "assignments below (back to a blank first-launch state)?\n\n"
            "Nothing is deleted from LM Studio itself - this just unloads what's in memory "
            "and forgets which model each Krea 2 tab was using. Use \"Load Assigned Models\" "
            "afterward once you've picked new ones, or just start using a Krea 2 tab and its "
            "assigned model will load automatically the moment it's needed.",
        )
        if confirm != QMessageBox.Yes:
            return
        self._set_busy(True)
        self.unload_btn.setText("Unloading...")
        self._unload_worker = _CallableWorker(lambda: llm_client.unload_all_models(base_url))
        self._unload_worker.succeeded.connect(self._on_unload_succeeded)
        self._unload_worker.failed.connect(self._on_unload_failed)
        self._unload_worker.start()

    def _on_unload_succeeded(self, instance_ids):
        self.unload_btn.setText("Unload All Models")
        self._clear_all_assignments()
        if not instance_ids:
            self._set_busy(False)
            self.status_label.setText(
                "Nothing was loaded. Model assignments cleared - go to the sections below "
                "to assign one to each Krea 2 tab."
            )
            return
        self.status_label.setText(
            f"Unloaded {len(instance_ids)} model(s) and cleared assignments. Refreshing..."
        )
        # Re-detect so the table (and the two Krea 2 tabs, via models_detected)
        # immediately reflect the now-empty loaded state instead of showing
        # stale "Loaded" rows until the next manual Detect click.
        self.detect_models()

    def _on_unload_failed(self, error):
        self._set_busy(False)
        self.unload_btn.setText("Unload All Models")
        QMessageBox.critical(
            self, "Unload failed",
            f"{error}\n\nUnloading requires LM Studio 0.4.0 or newer (the v1 REST API). "
            "Older versions have no equivalent unload endpoint.",
        )

    def _clear_all_assignments(self):
        """Resets to the same blank state a first launch would show - used
        by Unload All Models, since unloading everything but leaving the
        Krea 2 tabs still confidently pointing at specific models felt
        inconsistent with "start fresh"."""
        for combo, key in (
            (self.krea2_model_combo, KREA2_MODEL_SETTING_KEY),
            (self.vision_model_combo, VISION_MODEL_SETTING_KEY),
            (self.rewrite_model_combo, REWRITE_MODEL_SETTING_KEY),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none assigned)", None)
            combo.blockSignals(False)
            db.set_setting(self.conn, key, "")
        self._suppress_auto_assign = True
        db.set_setting(self.conn, ASSIGNMENTS_CLEARED_SETTING_KEY, "1")
        self._refresh_load_btn_enabled()
        self.assignments_changed.emit()

    # --- Load assigned models --------------------------------------------------

    def load_assigned_models(self):
        base_url = self.base_url()
        if not base_url:
            QMessageBox.warning(self, "No server address", "Enter a server address first.")
            return
        model_ids = sorted({
            db.get_setting(self.conn, key)
            for key in (KREA2_MODEL_SETTING_KEY, VISION_MODEL_SETTING_KEY, REWRITE_MODEL_SETTING_KEY)
            if db.get_setting(self.conn, key)
        })
        if not model_ids:
            QMessageBox.information(
                self, "Nothing assigned",
                "No models are assigned yet - pick one for each Krea 2 tab in the "
                "sections below first, then Load Assigned Models.",
            )
            return
        self._set_busy(True)
        self.load_btn.setText("Loading...")
        self._load_worker = _CallableWorker(lambda: self._load_models(base_url, model_ids))
        self._load_worker.succeeded.connect(self._on_load_succeeded)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.start()

    def _load_models(self, base_url, model_ids):
        # LM Studio's load endpoint isn't a no-op for a model that's
        # already loaded - it tries to load a SECOND instance, which can
        # fail with a resource-exhaustion 500 even though the original
        # instance is fine and nothing was actually wrong. Skip anything
        # already loaded rather than asking LM Studio to duplicate it.
        current = llm_client.fetch_models(base_url)
        loaded_ids = {m["id"] for m in current if m.get("loaded")}
        results = []
        for model_id in model_ids:
            if model_id in loaded_ids:
                results.append((model_id, None))
                continue
            try:
                llm_client.load_model(base_url, model_id)
                results.append((model_id, None))
            except Exception as e:
                results.append((model_id, str(e)))
        return results

    def _on_load_succeeded(self, results):
        self.load_btn.setText("Load Assigned Models")
        failed = [(model_id, error) for model_id, error in results if error]
        if failed:
            details = "\n".join(f"- {model_id}: {error}" for model_id, error in failed)
            QMessageBox.warning(
                self, "Some models failed to load",
                f"{len(failed)} of {len(results)} failed:\n\n{details}",
            )
        self.status_label.setText(
            f"Loaded {len(results) - len(failed)} of {len(results)} assigned model(s). Refreshing..."
        )
        # Re-detect so the table and both Krea 2 tabs' labels immediately
        # show the newly-loaded status instead of waiting for a manual
        # Detect Models click.
        self.detect_models()

    def _on_load_failed(self, error):
        self._set_busy(False)
        self.load_btn.setText("Load Assigned Models")
        QMessageBox.critical(
            self, "Load failed",
            f"{error}\n\nLoading requires LM Studio 0.4.0 or newer (the v1 REST API). "
            "Older versions have no equivalent load endpoint.",
        )

    # --- Busy state ----------------------------------------------------------

    def _set_busy(self, busy):
        self.detect_btn.setEnabled(not busy)
        self.unload_btn.setEnabled(not busy)
        if busy:
            self.load_btn.setEnabled(False)
        else:
            self._refresh_load_btn_enabled()  # not just "not busy" - stays disabled if nothing's assigned
