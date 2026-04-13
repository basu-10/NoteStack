"""
Advanced Search & Filter modal — matches the mockup screenshot exactly.
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QWidget, QScrollArea, QFrame, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor

from ui.flow_layout import FlowLayout


def _hex_to_rgba(color: str, alpha: int) -> str:
    value = (color or "").strip().lstrip("#")
    if len(value) != 6:
        return "transparent"
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except ValueError:
        return "transparent"
    a = max(0, min(255, alpha))
    return f"rgba({r}, {g}, {b}, {a})"


class AdvancedSearchModal(QDialog):
    """
    Emits filters_applied(keyword, selected_tags) when the user clicks Apply.
    """
    filters_applied = pyqtSignal(str, list)   # keyword, [tag_name, ...]

    def __init__(self, all_tags: list[dict] | list[str], current_keyword: str = "",
                 current_tags: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(760)

        self._all_tags = all_tags
        self._selected_tags: set[str] = set(current_tags or [])
        self._keyword = current_keyword
        self._chip_btns: dict[str, QPushButton] = {}
        self._tag_colors: dict[str, str] = {}

        self._build()
        self._apply_initial_state()
        self._last_applied_snapshot = self._snapshot_filters()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(32, 26, 32, 26)
        v.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(10)

        icon = QLabel("⧖")
        icon.setStyleSheet("color:#4F6EF7; font-size:18px;")
        title = QLabel("Advanced Search & Filters")
        title.setObjectName("ModalTitle")

        close = QPushButton("✕")
        close.setObjectName("CloseBtn")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.reject)

        hdr.addWidget(icon)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(close)
        v.addLayout(hdr)

        # divider
        v.addSpacing(20)
        div1 = QFrame()
        div1.setObjectName("Divider")
        v.addWidget(div1)
        v.addSpacing(22)

        # ── Keyword row ──────────────────────────────────────────────────
        kw_label = QLabel("CONTAINS WORD")
        kw_label.setObjectName("ModalSectionLabel")
        v.addWidget(kw_label)
        v.addSpacing(8)

        kw_row = QHBoxLayout()
        kw_row.setSpacing(12)

        self._kw_input = QLineEdit(self._keyword)
        self._kw_input.setObjectName("ModalInput")
        self._kw_input.setPlaceholderText("e.g. 'React' or 'Email'")
        self._kw_input.setFixedHeight(42)
        kw_row.addWidget(self._kw_input, 1)

        apply_btn = QPushButton("Apply Filters")
        apply_btn.setObjectName("BtnPrimary")
        apply_btn.setFixedHeight(42)
        apply_btn.setMinimumWidth(140)
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        apply_btn.clicked.connect(self._on_apply)

        kw_row.addWidget(apply_btn)
        v.addLayout(kw_row)
        v.addSpacing(24)

        # ── Tags section ─────────────────────────────────────────────────
        tag_label = QLabel("FILTER BY TAGS (SELECT MULTIPLE)")
        tag_label.setObjectName("ModalSectionLabel")
        v.addWidget(tag_label)
        v.addSpacing(12)

        # Scrollable tag area
        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_scroll.setFixedHeight(110)
        tag_scroll.setStyleSheet("background: transparent;")

        tag_inner = QWidget()
        tag_inner.setStyleSheet("background: transparent;")
        self._tag_layout = FlowLayout(tag_inner, h_spacing=8, v_spacing=8)

        tags_sorted = sorted(self._all_tags, key=lambda t: (t.get("name", "") if isinstance(t, dict) else str(t)).lower())
        for tag_item in tags_sorted:
            if isinstance(tag_item, dict):
                tag = str(tag_item.get("name", "")).strip()
                tag_color = str(tag_item.get("color") or "").strip()
            else:
                tag = str(tag_item).strip()
                tag_color = ""
            if not tag:
                continue

            btn = QPushButton(tag)
            btn.setObjectName("FilterTagChip")
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked, t=tag: self._toggle_tag(t))
            btn.setFixedHeight(30)
            if tag_color:
                self._tag_colors[tag] = tag_color
                btn.setStyleSheet(self._chip_style(tag, False))
            self._tag_layout.addWidget(btn)
            self._chip_btns[tag] = btn

        tag_inner.setLayout(self._tag_layout)
        tag_scroll.setWidget(tag_inner)
        v.addWidget(tag_scroll)

        # ── Footer ──────────────────────────────────────────────────────
        v.addSpacing(8)
        div2 = QFrame()
        div2.setObjectName("Divider")
        v.addWidget(div2)
        v.addSpacing(14)

        footer_row = QHBoxLayout()
        footer_row.addStretch()
        clear_btn = QPushButton("Clear all filters")
        clear_btn.setObjectName("ClearFiltersBtn")
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.clicked.connect(self._on_clear)
        footer_row.addWidget(clear_btn)
        v.addLayout(footer_row)

        outer.addWidget(panel)

    def _apply_initial_state(self):
        for tag, btn in self._chip_btns.items():
            is_sel = tag in self._selected_tags
            btn.setChecked(is_sel)
            btn.setProperty("selected", "true" if is_sel else "false")
            if tag in self._tag_colors:
                btn.setStyleSheet(self._chip_style(tag, is_sel))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _chip_style(self, tag: str, selected: bool) -> str:
        color = self._tag_colors.get(tag, "")
        if not color:
            return ""
        bg = _hex_to_rgba(color, 48) if selected else "transparent"
        return f"border:1px solid {color}; background:{bg};"

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _toggle_tag(self, tag: str):
        if tag in self._selected_tags:
            self._selected_tags.discard(tag)
            selected = False
        else:
            self._selected_tags.add(tag)
            selected = True
        btn = self._chip_btns[tag]
        btn.setProperty("selected", "true" if selected else "false")
        if tag in self._tag_colors:
            btn.setStyleSheet(self._chip_style(tag, selected))
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _on_apply(self):
        keyword = self._kw_input.text().strip()
        self.filters_applied.emit(keyword, list(self._selected_tags))
        self._last_applied_snapshot = self._snapshot_filters()
        self.accept()

    def _on_clear(self):
        self._selected_tags.clear()
        self._kw_input.clear()
        for btn in self._chip_btns.values():
            btn.setChecked(False)
            btn.setProperty("selected", "false")
            tag = btn.text()
            if tag in self._tag_colors:
                btn.setStyleSheet(self._chip_style(tag, False))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _snapshot_filters(self) -> tuple:
        keyword = self._kw_input.text().strip()
        tags = tuple(sorted(self._selected_tags))
        return (keyword, tags)

    def _confirm_close_with_unsaved_changes(self) -> bool:
        if self._snapshot_filters() == self._last_applied_snapshot:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved filter changes")
        box.setText("You changed filters without applying.")
        box.setInformativeText("Apply filters before closing?")
        apply_btn = box.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(apply_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_btn:
            return False
        if clicked == apply_btn:
            self._on_apply()
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

    def get_filters(self):
        return self._kw_input.text().strip(), list(self._selected_tags)
