"""
Settings modal for NoteStack.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ui.modals.about_modal import AboutModal
from ui.modals.whats_new_modal import VersionHistoryModal
from ui.styles import get_theme_options, normalize_theme


class SettingsModal(QDialog):
    theme_changed = pyqtSignal(str)
    import_req = pyqtSignal()
    export_req = pyqtSignal()

    def __init__(self, current_theme: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(540)

        self._current_theme = normalize_theme(current_theme)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 24, 30, 24)
        v.setSpacing(0)

        header = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("ModalTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.accept)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)

        v.addSpacing(16)
        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)
        v.addSpacing(18)

        theme_lbl = QLabel("THEME")
        theme_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(theme_lbl)
        v.addSpacing(8)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)

        self._theme_combo = QComboBox()
        self._theme_combo.setObjectName("ModalCombo")
        self._theme_combo.setFixedHeight(40)

        options = get_theme_options()
        for key, label in options:
            self._theme_combo.addItem(label, key)

        idx = self._theme_combo.findData(self._current_theme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)

        apply_btn = QPushButton("Apply Theme")
        apply_btn.setObjectName("BtnPrimary")
        apply_btn.setFixedHeight(40)
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        apply_btn.clicked.connect(self._apply_theme)

        theme_row.addWidget(self._theme_combo, 1)
        theme_row.addWidget(apply_btn)
        v.addLayout(theme_row)

        v.addSpacing(18)
        data_lbl = QLabel("DATA")
        data_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(data_lbl)
        v.addSpacing(8)

        data_row = QHBoxLayout()
        data_row.setSpacing(10)

        import_btn = QPushButton("Import")
        import_btn.setObjectName("BtnSecondary")
        import_btn.setFixedHeight(38)
        import_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        import_btn.clicked.connect(self.import_req.emit)

        export_btn = QPushButton("Export")
        export_btn.setObjectName("BtnSecondary")
        export_btn.setFixedHeight(38)
        export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        export_btn.clicked.connect(self.export_req.emit)

        data_row.addWidget(import_btn)
        data_row.addWidget(export_btn)
        v.addLayout(data_row)

        v.addSpacing(18)
        whats_new_lbl = QLabel("ABOUT")
        whats_new_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(whats_new_lbl)
        v.addSpacing(8)

        whats_new_btn = QPushButton("Version History")
        whats_new_btn.setObjectName("BtnSecondary")
        whats_new_btn.setFixedHeight(38)
        whats_new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        whats_new_btn.clicked.connect(self._open_version_history)

        about_btn = QPushButton("About")
        about_btn.setObjectName("BtnSecondary")
        about_btn.setFixedHeight(38)
        about_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        about_btn.clicked.connect(self._open_about)

        about_row = QHBoxLayout()
        about_row.setSpacing(10)
        about_row.addWidget(whats_new_btn)
        about_row.addWidget(about_btn)
        v.addLayout(about_row)

        v.addSpacing(20)
        footer = QHBoxLayout()
        footer.addStretch()

        done_btn = QPushButton("Done")
        done_btn.setObjectName("BtnSecondary")
        done_btn.setFixedHeight(36)
        done_btn.setFixedWidth(94)
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)
        v.addLayout(footer)

        outer.addWidget(panel)

    def _apply_theme(self):
        theme_key = self._theme_combo.currentData()
        if not isinstance(theme_key, str):
            return
        self._current_theme = normalize_theme(theme_key)
        self.theme_changed.emit(self._current_theme)

    def _open_version_history(self):
        dlg = VersionHistoryModal(parent=self)
        dlg.exec()

    def _open_about(self):
        dlg = AboutModal(parent=self)
        dlg.exec()
