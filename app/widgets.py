"""Reusable picker widgets used by the Krea 2 tab and the Library tab.

Multi-select uses a compact summary + "Select..." button that opens a popup
dialog for browsing/checking options, rather than an inline fixed-height
list, so the popup's own scroll area doesn't fight the page's.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app import db

NONE_LABEL = "(none)"


class _NoScrollMixin:
    """Ignore mouse-wheel events unless the widget already has focus (i.e.
    was clicked into first). Without this, scrolling the page with the
    cursor merely hovering over a combo box/spin box silently changes its
    value instead of scrolling the page.

    Checking hasFocus() alone isn't enough: QComboBox/QSpinBox/QDoubleSpinBox
    default to Qt.WheelFocus, which grants the widget focus AS PART OF
    processing the wheel event itself - so hasFocus() is already True by the
    time our override runs, even on a pure hover-and-scroll with no click.
    Downgrading to Qt.StrongFocus (click or Tab only, no wheel) is the actual
    fix; the hasFocus() check then behaves as originally intended."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollComboBox(_NoScrollMixin, QComboBox):
    """On top of the hover/wheel-focus fix above: a combo box keeps keyboard
    focus after you've picked a value, so if your mouse is still resting on
    it while you keep scrolling down the page, the next wheel tick would
    nudge it again even though you're done with it. Releasing focus the
    moment a choice is made means scrolling past it afterward does nothing
    until you deliberately click it again."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activated.connect(lambda _idx: self.clearFocus())


class NoScrollDoubleSpinBox(_NoScrollMixin, QDoubleSpinBox):
    pass


class NoScrollSpinBox(_NoScrollMixin, QSpinBox):
    pass


class SingleTagPicker(QWidget):
    """A dropdown, optionally with a weight spinbox, for a single-select
    subcategory. show_weight=False drops the '(tag:weight)' spinbox
    entirely - meaningful for the Danbooru/Pony builder tab, meaningless for
    plain-prose targets like Krea2 where weight syntax isn't a thing."""

    changed = Signal()

    def __init__(self, conn, subcategory_key, gender=None, show_weight=True, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.subcategory_key = subcategory_key
        self._gender = gender
        self.show_weight = show_weight

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = NoScrollComboBox()
        self.combo.setMinimumWidth(220)
        layout.addWidget(self.combo, stretch=1)

        if show_weight:
            self.weight_spin = NoScrollDoubleSpinBox()
            self.weight_spin.setRange(0.0, 3.0)
            self.weight_spin.setSingleStep(0.05)
            self.weight_spin.setValue(1.0)
            self.weight_spin.setPrefix("w: ")
            layout.addWidget(self.weight_spin)
            self.weight_spin.valueChanged.connect(lambda _val: self.changed.emit())
        else:
            self.weight_spin = None

        self.combo.currentIndexChanged.connect(lambda _idx: self.changed.emit())

        self.reload(gender)

    def reload(self, gender=None):
        self._gender = gender
        current = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(NONE_LABEL)
        for tag in db.list_tags(self.conn, self.subcategory_key, gender=gender):
            self.combo.addItem(tag["value"])
        idx = self.combo.findText(current)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo.blockSignals(False)

    def get_selection(self):
        value = self.combo.currentText()
        if not value or value == NONE_LABEL:
            return None
        weight = self.weight_spin.value() if self.weight_spin else None
        return {"value": value, "weight": weight}

    def set_selection(self, sel):
        if not sel:
            self.combo.setCurrentIndex(0)
            return
        idx = self.combo.findText(sel.get("value", ""))
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        if self.weight_spin:
            self.weight_spin.setValue(sel.get("weight") or 1.0)


class WeightableCheckBox(QCheckBox):
    """A checkbox that opens a weight prompt on double-click, if checked -
    unless show_weight is False, in which case double-click is just a no-op
    (weight syntax doesn't apply, e.g. for the Krea2 tab)."""

    weightEdited = Signal()

    def __init__(self, value, weight=1.0, show_weight=True, parent=None):
        super().__init__(value, parent)
        self.value = value
        self.weight = weight
        self.show_weight = show_weight
        self._refresh_label()

    def _refresh_label(self):
        if not self.show_weight or self.weight == 1.0:
            self.setText(self.value)
        else:
            self.setText(f"{self.value}  [w={self.weight}]")

    def mouseDoubleClickEvent(self, event):
        if self.show_weight and self.isChecked():
            value, ok = QInputDialog.getDouble(
                self, "Set weight", f"Weight for '{self.value}'", self.weight, 0.0, 3.0, 2
            )
            if ok:
                self.weight = value
                self._refresh_label()
                self.weightEdited.emit()
        event.accept()


class MultiSelectDialog(QDialog):
    def __init__(self, title, options, selected_weights, show_weight=True, parent=None):
        """options: list of tag values. selected_weights: {value: weight} for
        currently-checked tags."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 480)
        self.show_weight = show_weight

        root = QVBoxLayout(self)
        hint_text = ("Check to select; double-click a checked tag to set its weight."
                     if show_weight else "Check to select.")
        hint = QLabel(hint_text)
        hint.setStyleSheet("color: gray; font-size: 10px;")
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        self.boxes = []
        columns = 3
        for i, value in enumerate(options):
            box = WeightableCheckBox(value, selected_weights.get(value, 1.0), show_weight=show_weight)
            box.setChecked(value in selected_weights)
            self.boxes.append(box)
            grid.addWidget(box, i // columns, i % columns)
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def get_result(self):
        if not self.show_weight:
            return [{"value": b.value, "weight": None} for b in self.boxes if b.isChecked()]
        return [
            {"value": b.value, "weight": (None if b.weight == 1.0 else b.weight)}
            for b in self.boxes
            if b.isChecked()
        ]


class MultiTagPicker(QWidget):
    """Compact summary + button opening a MultiSelectDialog to pick/weight
    several tags for a multi-select subcategory."""

    changed = Signal()

    def __init__(self, conn, subcategory_key, gender=None, show_weight=True, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.subcategory_key = subcategory_key
        self._gender = gender
        self.show_weight = show_weight
        self._selection = []  # list of {"value", "weight"}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLineEdit()
        self.summary.setReadOnly(True)
        self.select_btn = QPushButton("Select...")
        self.select_btn.clicked.connect(self._open_dialog)
        layout.addWidget(self.summary, stretch=1)
        layout.addWidget(self.select_btn)

        self._refresh_summary()

    def reload(self, gender=None):
        self._gender = gender
        valid_values = {t["value"] for t in db.list_tags(self.conn, self.subcategory_key, gender=gender)}
        self._selection = [s for s in self._selection if s["value"] in valid_values]
        self._refresh_summary()

    def _current_options(self):
        return [t["value"] for t in db.list_tags(self.conn, self.subcategory_key, gender=self._gender)]

    def _refresh_summary(self):
        if not self._selection:
            self.summary.setText("")
            self.summary.setPlaceholderText(NONE_LABEL)
        else:
            self.summary.setText(", ".join(s["value"] for s in self._selection))

    def _open_dialog(self):
        weights = {s["value"]: (s.get("weight") or 1.0) for s in self._selection}
        dlg = MultiSelectDialog(self.subcategory_key, self._current_options(), weights,
                                 show_weight=self.show_weight, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._selection = dlg.get_result()
            self._refresh_summary()
            self.changed.emit()

    def get_selection(self):
        return list(self._selection)

    def set_selection(self, sels):
        if isinstance(sels, dict):
            # backward compat: a saved prompt from before this subcategory
            # became multi-select stored a single {"value","weight"} dict.
            sels = [sels] if sels.get("value") else []
        self._selection = list(sels or [])
        self._refresh_summary()
