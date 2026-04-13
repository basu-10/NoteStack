"""
On-demand outline modal for heading navigation.
"""
from __future__ import annotations

import re
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


def extract_headings_from_document(doc: QTextDocument | None) -> list[tuple[int, str, int]]:
    if doc is None:
        return []

    headings: list[tuple[int, str, int]] = []
    block = doc.firstBlock()
    while block.isValid():
        text = block.text().strip()
        if text:
            level = block.blockFormat().headingLevel()
            if level <= 0:
                md_heading = re.match(r"^(#{1,6})\s+(.+)$", text)
                if md_heading:
                    level = len(md_heading.group(1))
                    text = md_heading.group(2).strip()
            if level > 0:
                headings.append((level, text, block.position()))
        block = block.next()
    return headings


class OutlineModal(QDialog):
    heading_selected = pyqtSignal(int)

    def __init__(self, headings: list[tuple[int, str, int]], title: str = "Outline", parent=None):
        super().__init__(parent)
        self._headings = headings
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(420, 320)
        self._build(title)

    def _build(self, title_text: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(0)

        hdr = QHBoxLayout()
        title = QLabel(title_text)
        title.setObjectName("ModalTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(close_btn)
        v.addLayout(hdr)
        v.addSpacing(12)

        div = QFrame()
        div.setObjectName("Divider")
        v.addWidget(div)
        v.addSpacing(12)

        self._list = QListWidget()
        self._list.setObjectName("OutlineList")
        self._list.itemClicked.connect(self._activate_item)
        self._list.itemActivated.connect(self._activate_item)
        v.addWidget(self._list, 1)

        self._populate()
        outer.addWidget(panel)

    def _populate(self):
        self._list.clear()
        if not self._headings:
            empty = QListWidgetItem("No headings found")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
            return

        for level, text, pos in self._headings:
            item = QListWidgetItem(f"{'  ' * max(0, level - 1)}{text}")
            item.setData(Qt.ItemDataRole.UserRole, pos)
            self._list.addItem(item)

    def _activate_item(self, item: QListWidgetItem):
        pos = item.data(Qt.ItemDataRole.UserRole)
        if pos is None:
            return
        self.heading_selected.emit(int(pos))
        self.accept()

    def _confirm_close_with_unsaved_changes(self) -> bool:
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
