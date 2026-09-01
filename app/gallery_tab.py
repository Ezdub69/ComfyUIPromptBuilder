"""Native image gallery: browse a folder tree of generated PNGs, see
thumbnails for whichever folder is selected (not everything under it at
once), click one for a larger preview plus its generation metadata (prompt,
negative prompt, seed, model, LoRAs, sampler settings) parsed from the
embedded PNG chunks - see comfy_metadata.py for the actual parsing.

Deliberately scoped down from a full asset manager (no search, tagging,
favorites, collections, or live filesystem watching) - just a visual
gallery navigable by folder, and its metadata alongside it.
"""

import os

from PySide6.QtCore import QDir, QSize, QSortFilterProxyModel, QThread, Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFileSystemModel, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTreeView, QVBoxLayout, QWidget,
)

from app import comfy_metadata, db

THUMB_SIZE = 140
PREVIEW_MAX_SIZE = 360
BATCH_SIZE = 40


class _RootVisibleProxyModel(QSortFilterProxyModel):
    """QTreeView.setRootIndex() hides that index's own row - only its
    children show up, with no way to click back to the root folder itself
    once you've navigated into a subfolder. This proxy sits in front of
    QFileSystemModel so the tree's actual Qt root can be the CHOSEN folder's
    *parent* (making the chosen folder a real, visible, selectable row) while
    filtering out everything else at that parent level - i.e. the chosen
    folder's siblings never appear, only itself and its own descendants."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scope_parent_path = None
        self._scope_folder_path = None

    def set_scope(self, folder_path):
        if folder_path is None:
            self._scope_folder_path = None
            self._scope_parent_path = None
        else:
            self._scope_folder_path = os.path.normcase(os.path.normpath(folder_path))
            self._scope_parent_path = os.path.normcase(os.path.normpath(os.path.dirname(folder_path)))
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if self._scope_folder_path is None:
            return True
        source_model = self.sourceModel()
        parent_path = os.path.normcase(os.path.normpath(source_model.filePath(source_parent)))
        if parent_path != self._scope_parent_path:
            return True  # not at the filtered level - let descendants through freely
        index = source_model.index(source_row, 0, source_parent)
        row_path = os.path.normcase(os.path.normpath(source_model.filePath(index)))
        return row_path == self._scope_folder_path


class _ScanWorker(QThread):
    """Lists PNGs directly inside one folder - not its subfolders, that's
    what the folder tree is for - and generates thumbnails off the UI
    thread. QImage decode/scale is the expensive part and is thread-safe;
    only the final QImage->QPixmap conversion needs the main thread."""

    found_batch = Signal(list)  # [(path, QImage thumbnail), ...]
    finished_scan = Signal(int)  # total PNGs found in this folder

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._abort = False

    def request_abort(self):
        self._abort = True

    def run(self):
        try:
            names = os.listdir(self._folder)
        except OSError:
            self.finished_scan.emit(0)
            return
        paths = [os.path.join(self._folder, n) for n in names if n.lower().endswith(".png")]
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        batch = []
        for path in paths:
            if self._abort:
                return
            img = QImage(path)
            if img.isNull():
                continue
            thumb = img.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            batch.append((path, thumb))
            if len(batch) >= BATCH_SIZE:
                self.found_batch.emit(batch)
                batch = []
        if batch:
            self.found_batch.emit(batch)
        self.finished_scan.emit(len(paths))


class GalleryTab(QWidget):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._scan_worker = None
        self._found_count = 0
        self._current_folder = None

        root = QVBoxLayout(self)

        # --- Root folder row ---------------------------------------------
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Root folder:"))
        self.folder_edit = QLineEdit(db.get_setting(conn, "gallery_last_folder", ""))
        folder_row.addWidget(self.folder_edit, stretch=1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_folder)
        folder_row.addWidget(browse_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_current_folder)
        folder_row.addWidget(self.refresh_btn)
        self.status_label = QLabel("")
        folder_row.addWidget(self.status_label)
        root.addLayout(folder_row)

        # --- Main split: folder tree | thumbnail grid | preview + metadata ---
        splitter = QSplitter(Qt.Horizontal)

        self.fs_model = QFileSystemModel()
        self.fs_model.setFilter(QDir.Dirs | QDir.NoDotAndDotDot)
        self.tree_proxy = _RootVisibleProxyModel()
        self.tree_proxy.setSourceModel(self.fs_model)
        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_proxy)
        self.tree_view.setHeaderHidden(True)
        for col in range(1, 4):
            self.tree_view.hideColumn(col)
        self.tree_view.clicked.connect(self._on_tree_clicked)
        splitter.addWidget(self.tree_view)

        self.thumb_list = QListWidget()
        self.thumb_list.setViewMode(QListWidget.IconMode)
        self.thumb_list.setIconSize(QPixmap(THUMB_SIZE, THUMB_SIZE).size())
        self.thumb_list.setResizeMode(QListWidget.Adjust)
        self.thumb_list.setWrapping(True)
        self.thumb_list.setSpacing(6)
        self.thumb_list.setMovement(QListWidget.Static)
        self.thumb_list.currentItemChanged.connect(self._on_selection_changed)
        # Default IconMode selection highlight is nearly invisible against a
        # thumbnail - a clear border + tinted background makes the current
        # selection obvious at a glance.
        self.thumb_list.setStyleSheet(
            "QListWidget::item:selected { border: 3px solid #16a34a; "
            "background: #bbf7d0; border-radius: 4px; } "
            "QListWidget::item:selected:!active { border: 3px solid #16a34a; "
            "background: #bbf7d0; }"
        )
        splitter.addWidget(self.thumb_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.preview_label = QLabel("Select an image")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(PREVIEW_MAX_SIZE)
        right_layout.addWidget(self.preview_label)

        self.meta_tabs = QTabWidget()
        meta_widget = QWidget()
        self.meta_form = QFormLayout(meta_widget)
        self.meta_tabs.addTab(meta_widget, "Metadata")
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setReadOnly(True)
        self.meta_tabs.addTab(self.raw_edit, "Raw")
        right_layout.addWidget(self.meta_tabs, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, stretch=1)

        if self.folder_edit.text().strip():
            self._set_root_folder(self.folder_edit.text().strip())

    # --- Folder tree -----------------------------------------------------------

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose root folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self._set_root_folder(folder)

    def _set_root_folder(self, folder):
        if not os.path.isdir(folder):
            self.status_label.setText("Choose a valid folder first.")
            return
        db.set_setting(self.conn, "gallery_last_folder", folder)
        folder = os.path.normpath(folder)
        parent = os.path.dirname(folder)

        if not parent or os.path.normcase(parent) == os.path.normcase(folder):
            # Drive root or similar degenerate case with no real parent to
            # anchor on - falls back to the old hidden-root behavior; only
            # affects picking an actual drive root (e.g. "C:\") as the folder.
            self.tree_proxy.set_scope(None)
            self.fs_model.setRootPath(folder)
            self.tree_view.setRootIndex(self.tree_proxy.mapFromSource(self.fs_model.index(folder)))
        else:
            self.fs_model.setRootPath(parent)
            self.tree_proxy.set_scope(folder)
            parent_source_index = self.fs_model.index(parent)
            self.tree_view.setRootIndex(self.tree_proxy.mapFromSource(parent_source_index))
            target_index = self.tree_proxy.mapFromSource(self.fs_model.index(folder))
            self.tree_view.setCurrentIndex(target_index)
            self.tree_view.expand(target_index)

        self._scan_folder(folder)

    def _on_tree_clicked(self, index):
        self._scan_folder(self.fs_model.filePath(self.tree_proxy.mapToSource(index)))

    def refresh_current_folder(self):
        if self._current_folder:
            self._scan_folder(self._current_folder)

    # --- Scanning ----------------------------------------------------------

    def _scan_folder(self, folder):
        self._current_folder = folder
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.request_abort()
            self._scan_worker.wait(2000)

        self.thumb_list.clear()
        self._found_count = 0
        self.status_label.setText("Scanning...")
        self.refresh_btn.setEnabled(False)

        self._scan_worker = _ScanWorker(folder)
        self._scan_worker.found_batch.connect(self._on_found_batch)
        self._scan_worker.finished_scan.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_found_batch(self, batch):
        for path, qimage in batch:
            item = QListWidgetItem(QPixmap.fromImage(qimage), "")
            # No text is set, but QListWidgetItem still reserves a label
            # line beneath the icon by default - shows up as a blank
            # rounded-rect strip under every thumbnail. Pinning the size
            # hint to just the icon removes that dead space.
            item.setSizeHint(QSize(THUMB_SIZE, THUMB_SIZE))
            item.setData(Qt.UserRole, path)
            item.setToolTip(os.path.basename(path))
            self.thumb_list.addItem(item)
        self._found_count += len(batch)
        self.status_label.setText(f"{self._found_count} found...")

    def _on_scan_finished(self, total):
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(f"{total} image(s) in this folder")

    # --- Selection / metadata -------------------------------------------------

    def _on_selection_changed(self, current, _previous):
        if current is None:
            return
        path = current.data(Qt.UserRole)
        self._show_preview(path)
        self._show_metadata(path)

    def _show_preview(self, path):
        pix = QPixmap(path)
        if pix.isNull():
            self.preview_label.setText("(failed to load)")
            return
        scaled = pix.scaled(PREVIEW_MAX_SIZE, PREVIEW_MAX_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def _show_metadata(self, path):
        while self.meta_form.rowCount():
            self.meta_form.removeRow(0)

        structured, raw, source = comfy_metadata.extract_metadata(path)

        if source is None:
            self.meta_form.addRow(QLabel("No recognizable generation metadata found in this file."))
            self.raw_edit.setPlainText("")
            return

        field_order = [
            ("prompt", "Prompt"),
            ("negative_prompt", "Negative Prompt"),
            ("model", "Model"),
            ("loras", "LoRAs"),
            ("seed", "Seed"),
            ("steps", "Steps"),
            ("cfg", "CFG"),
            ("sampler", "Sampler"),
            ("scheduler", "Scheduler"),
            ("denoise", "Denoise"),
        ]
        for key, label in field_order:
            if key not in structured:
                continue
            value = structured[key]
            text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            value_label = QLabel(text)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.meta_form.addRow(QLabel(f"{label}:"), value_label)

        self.meta_form.addRow(QLabel("Source:"), QLabel(source))
        self.raw_edit.setPlainText(raw)
