"""Library editor: edit existing tags, move them between (sub)categories,
and add brand-new tags, all from within the app."""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app import db

COLUMNS = ["id", "section", "subcategory", "value", "label", "gender_scope", "default_weight"]


class TagDialog(QDialog):
    def __init__(self, conn, parent=None, subcategory_key=None, value="", label="",
                 gender_scope=None, weight=1.0, allow_subcategory_choice=True):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Tag")
        form = QFormLayout(self)

        self.subcat_combo = QComboBox()
        self._subcat_keys = []
        for sc in db.list_subcategories(conn):
            self._subcat_keys.append(sc["key"])
            self.subcat_combo.addItem(f"{sc['label']} ({sc['key']})")
        if subcategory_key and subcategory_key in self._subcat_keys:
            self.subcat_combo.setCurrentIndex(self._subcat_keys.index(subcategory_key))
        self.subcat_combo.setEnabled(allow_subcategory_choice)
        form.addRow("Subcategory:", self.subcat_combo)

        self.value_edit = QLineEdit(value)
        form.addRow("Tag value (literal prompt text):", self.value_edit)

        self.label_edit = QLineEdit(label)
        form.addRow("Display label:", self.label_edit)

        self.gender_combo = QComboBox()
        self.gender_combo.addItems(["(any)", "female", "male"])
        if gender_scope in ("female", "male"):
            self.gender_combo.setCurrentText(gender_scope)
        form.addRow("Gender scope:", self.gender_combo)

        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(0.0, 3.0)
        self.weight_spin.setSingleStep(0.05)
        self.weight_spin.setValue(weight or 1.0)
        form.addRow("Default weight:", self.weight_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_data(self):
        gender = self.gender_combo.currentText()
        return {
            "subcategory_key": self._subcat_keys[self.subcat_combo.currentIndex()],
            "value": self.value_edit.text().strip(),
            "label": self.label_edit.text().strip() or self.value_edit.text().strip(),
            "gender_scope": None if gender == "(any)" else gender,
            "weight": self.weight_spin.value(),
        }


class LibraryTab(QWidget):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn

        root = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by value, label, subcategory or section...")
        self.search_edit.textChanged.connect(self.refresh)
        search_row.addWidget(self.search_edit)
        root.addLayout(search_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, stretch=1)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add tag")
        self.add_btn.clicked.connect(self.add_tag)
        self.edit_btn = QPushButton("Edit selected")
        self.edit_btn.clicked.connect(self.edit_tag)
        self.move_btn = QPushButton("Move to category...")
        self.move_btn.clicked.connect(self.move_tag)
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self.delete_tag)
        for b in (self.add_btn, self.edit_btn, self.move_btn, self.delete_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._rows = []
        self.refresh()

    def refresh(self):
        query = self.search_edit.text().strip().lower()
        all_rows = db.list_all_tags_with_subcategory(self.conn)
        if query:
            all_rows = [
                r for r in all_rows
                if query in r["value"].lower()
                or query in r["label"].lower()
                or query in r["subcategory_label"].lower()
                or query in r["section_label"].lower()
            ]
        self._rows = all_rows
        self.table.setRowCount(len(all_rows))
        for i, r in enumerate(all_rows):
            values = [
                str(r["id"]), r["section_label"], r["subcategory_label"],
                r["value"], r["label"], r["gender_scope"] or "", str(r["default_weight"]),
            ]
            for j, v in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(v))

    def _selected_row(self):
        idxs = self.table.selectionModel().selectedRows()
        if not idxs:
            return None
        return self._rows[idxs[0].row()]

    def add_tag(self):
        dlg = TagDialog(self.conn, self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.result_data()
            if not data["value"]:
                QMessageBox.warning(self, "Missing value", "Tag value cannot be empty.")
                return
            db.add_tag(self.conn, data["subcategory_key"], data["value"], data["label"],
                       data["gender_scope"], data["weight"])
            self.refresh()

    def edit_tag(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "No selection", "Select a tag to edit first.")
            return
        dlg = TagDialog(
            self.conn, self, subcategory_key=row["subcategory_key"], value=row["value"],
            label=row["label"], gender_scope=row["gender_scope"], weight=row["default_weight"],
            allow_subcategory_choice=False,
        )
        if dlg.exec() == QDialog.Accepted:
            data = dlg.result_data()
            db.update_tag(self.conn, row["id"], value=data["value"], label=data["label"],
                          gender_scope=data["gender_scope"] or "", default_weight=data["weight"])
            self.refresh()

    def move_tag(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "No selection", "Select a tag to move first.")
            return
        dlg = TagDialog(
            self.conn, self, subcategory_key=row["subcategory_key"], value=row["value"],
            label=row["label"], gender_scope=row["gender_scope"], weight=row["default_weight"],
            allow_subcategory_choice=True,
        )
        dlg.setWindowTitle("Move tag to a different category/subcategory")
        dlg.value_edit.setEnabled(False)
        dlg.label_edit.setEnabled(False)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.result_data()
            db.move_tag(self.conn, row["id"], data["subcategory_key"])
            self.refresh()

    def delete_tag(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "No selection", "Select a tag to delete first.")
            return
        confirm = QMessageBox.question(
            self, "Delete tag", f"Delete tag '{row['value']}' from {row['subcategory_label']}?"
        )
        if confirm == QMessageBox.Yes:
            db.delete_tag(self.conn, row["id"])
            self.refresh()
