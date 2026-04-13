"""
Inline item edit modal for sidebar folders/tags.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ui.icon_utils import make_icon


class ItemEditModal(QDialog):
    saved = pyqtSignal(str, object)
    deleted = pyqtSignal()

    def __init__(self, *, title: str, item_name: str, item_color: str | None, delete_label: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(520)

        self._title = title
        self._initial_name = item_name
        self._color = item_color or ""
        self._delete_label = delete_label
        self._build()
        self._last_saved_snapshot = self._snapshot_state()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 24, 30, 24)
        v.setSpacing(0)

        header = QHBoxLayout()
        title = QLabel(self._title)
        title.setObjectName("ModalTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)

        v.addSpacing(16)
        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)
        v.addSpacing(18)

        name_lbl = QLabel("NAME")
        name_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(name_lbl)
        v.addSpacing(8)

        self._name_input = QLineEdit(self._initial_name)
        self._name_input.setObjectName("ModalInput")
        self._name_input.setFixedHeight(40)
        self._name_input.selectAll()
        v.addWidget(self._name_input)

        v.addSpacing(18)
        color_lbl = QLabel("COLOR")
        color_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(color_lbl)
        v.addSpacing(8)

        color_row = QHBoxLayout()
        color_row.setSpacing(10)

        self._color_preview = QFrame()
        self._color_preview.setFixedSize(40, 40)
        self._color_preview.setStyleSheet(self._swatch_style(self._color))

        pick_btn = QPushButton("Choose Color")
        pick_btn.setObjectName("BtnSecondary")
        pick_btn.setFixedHeight(40)
        pick_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        pick_btn.clicked.connect(self._pick_color)

        color_row.addWidget(self._color_preview)
        color_row.addWidget(pick_btn)
        color_row.addStretch()
        v.addLayout(color_row)

        v.addSpacing(20)
        footer = QHBoxLayout()

        delete_btn = QPushButton(self._delete_label)
        delete_btn.setObjectName("BtnDanger")
        delete_btn.setFixedHeight(36)
        delete_btn.setIcon(make_icon("trash.png", "#FFFFFF", 15))
        delete_btn.setIconSize(QSize(15, 15))
        delete_btn.clicked.connect(self._on_delete)

        footer.addWidget(delete_btn)
        footer.addStretch()

        save_btn = QPushButton("Save")
        save_btn.setObjectName("BtnPrimary")
        save_btn.setFixedHeight(36)
        save_btn.setFixedWidth(94)
        save_btn.setIcon(make_icon("export.png", "#FFFFFF", 15))
        save_btn.setIconSize(QSize(15, 15))
        save_btn.clicked.connect(self._on_save)
        footer.addWidget(save_btn)

        v.addLayout(footer)
        outer.addWidget(panel)

    def _swatch_style(self, color: str | None) -> str:
        if color:
            return f"background:{color}; border:1px solid #2A2A45; border-radius:8px;"
        return "background:transparent; border:1px solid #2A2A45; border-radius:8px;"

    def _pick_color(self):
        picked = QColorDialog.getColor(QColor(self._color or "#4F6EF7"), self, "Pick Color")
        if not picked.isValid():
            return
        self._color = picked.name()
        self._color_preview.setStyleSheet(self._swatch_style(self._color))

    def _on_save(self):
        if not self._save_payload():
            return
        self.accept()

    def _on_delete(self):
        self.deleted.emit()
        self.accept()

    def _snapshot_state(self) -> tuple:
        return (self._name_input.text().strip(), self._color or "")

    def _save_payload(self) -> bool:
        name = self._name_input.text().strip()
        if not name:
            return False
        self.saved.emit(name, self._color or None)
        self._last_saved_snapshot = self._snapshot_state()
        return True

    def _confirm_close_with_unsaved_changes(self) -> bool:
        if self._snapshot_state() == self._last_saved_snapshot:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText("You have unsaved edits.")
        box.setInformativeText("Save changes before closing?")
        save_btn = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_btn:
            return False
        if clicked == save_btn:
            if not self._save_payload():
                return False
            self.accept()
            return False
        return True

    def reject(self):
        if not self._confirm_close_with_unsaved_changes():
            return
        super().reject()

    def closeEvent(self, event):
        if not self._confirm_close_with_unsaved_changes():
            event.ignore()
            return
        super().closeEvent(event)
