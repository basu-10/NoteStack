"""
Bulk Export modal — lets the user choose an export format for selected notes.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class BulkExportModal(QDialog):
    """Emits one of export_json / export_txt / export_clipboard and then closes."""

    export_json = pyqtSignal()
    export_txt = pyqtSignal()
    export_clipboard = pyqtSignal()

    def __init__(self, count: int, parent=None, show_clipboard: bool = True, subtitle_override: str | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(380)
        self._show_clipboard = show_clipboard
        self._build(count, subtitle_override)

    def _build(self, count: int, subtitle_override: str | None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Export Notes")
        title.setObjectName("ModalTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)

        v.addSpacing(6)
        subtitle_text = subtitle_override if subtitle_override is not None else f"{count} note{'s' if count != 1 else ''} selected"
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("ContentSubtitle")
        v.addWidget(subtitle)

        v.addSpacing(16)
        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)
        v.addSpacing(20)

        # ── Format label ─────────────────────────────────────────────────────
        fmt_lbl = QLabel("CHOOSE FORMAT")
        fmt_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(fmt_lbl)
        v.addSpacing(12)

        # ── Action buttons ───────────────────────────────────────────────────
        button_specs = [
            ("Save as JSON", "Export to a .json file", self._on_json),
            ("Save as TXT", "Export to a plain-text .txt file", self._on_txt),
        ]
        if self._show_clipboard:
            button_specs.append(("Copy to Clipboard", "Copy all notes as plain text", self._on_clipboard))
        for label, tooltip, slot in button_specs:
            btn = QPushButton(label)
            btn.setObjectName("BtnSecondary")
            btn.setFixedHeight(40)
            btn.setToolTip(tooltip)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(slot)
            v.addWidget(btn)
            v.addSpacing(8)

        v.addSpacing(8)
        divider2 = QFrame()
        divider2.setObjectName("Divider")
        v.addWidget(divider2)
        v.addSpacing(14)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("BtnSecondary")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setFixedWidth(90)
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        v.addLayout(footer)

        outer.addWidget(panel)

    # ── Slots ────────────────────────────────────────────────────────────────

    def _on_json(self):
        self.export_json.emit()
        self.accept()

    def _on_txt(self):
        self.export_txt.emit()
        self.accept()

    def _on_clipboard(self):
        self.export_clipboard.emit()
        self.accept()
