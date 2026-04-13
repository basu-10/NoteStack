"""
Create folder modal used by the sidebar context actions.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
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


class CreateFolderModal(QDialog):
    created = pyqtSignal(str, object)

    def __init__(self, *, parent_label: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(500)

        self._parent_label = parent_label
        self._color = ""
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
        title = QLabel("Create Folder")
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

        parent_lbl = QLabel("LOCATION")
        parent_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(parent_lbl)
        v.addSpacing(8)

        parent_value = QLabel(self._parent_label)
        parent_value.setObjectName("ContentSubtitle")
        v.addWidget(parent_value)

        v.addSpacing(18)
        name_lbl = QLabel("NAME")
        name_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(name_lbl)
        v.addSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("ModalInput")
        self._name_input.setFixedHeight(40)
        self._name_input.setPlaceholderText("Folder name…")
        self._name_input.returnPressed.connect(self._on_create)
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
        footer.addStretch()

        create_btn = QPushButton("Create")
        create_btn.setObjectName("BtnPrimary")
        create_btn.setFixedHeight(36)
        create_btn.setFixedWidth(104)
        create_btn.clicked.connect(self._on_create)
        footer.addWidget(create_btn)

        v.addLayout(footer)
        outer.addWidget(panel)

        self._name_input.setFocus()

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

    def _on_create(self):
        if not self._save_payload():
            return
        self.accept()

    def _snapshot_state(self) -> tuple:
        return (self._name_input.text().strip(), self._color or "")

    def _save_payload(self) -> bool:
        name = self._name_input.text().strip()
        if not name:
            return False
        self.created.emit(name, self._color or None)
        self._last_saved_snapshot = self._snapshot_state()
        return True

    def _confirm_close_with_unsaved_changes(self) -> bool:
        if self._snapshot_state() == self._last_saved_snapshot:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved folder")
        box.setText("You have unsaved folder details.")
        box.setInformativeText("Create folder before closing?")
        create_btn = box.addButton("Create", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(create_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_btn:
            return False
        if clicked == create_btn:
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
