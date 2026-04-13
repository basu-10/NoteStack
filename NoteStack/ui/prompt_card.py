"""
Prompt card and list row widgets.
"""
from __future__ import annotations

from datetime import datetime
from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QTextDocument
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ui.styles import C
from ui.icon_utils import make_icon, make_pixmap


def _fmt_date(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%b %d")
    except Exception:
        return raw[:10] if raw else ""


def _elide_text(label: QLabel, text: str, max_width: int) -> str:
    return label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max_width)


def _plain_text(raw: str) -> str:
    if not raw:
        return ""
    if Qt.mightBeRichText(raw):
        doc = QTextDocument()
        doc.setHtml(raw)
        return doc.toPlainText()
    return raw


class TagChip(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text: str, color: str | None = None, parent=None):
        super().__init__(text.upper(), parent)
        self._tag_name = text
        self.setObjectName("CardTagLabel")
        self.setFixedHeight(20)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if color:
            self.setStyleSheet(f"border:1px solid {color};")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._tag_name)
            event.accept()
        else:
            super().mousePressEvent(event)


class _ClickableWidget(QWidget):
    """Thin wrapper that emits clicked() on left mouse press without bubbling."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._hover_label: QLabel | None = None

    def set_hover_label(self, label: QLabel) -> None:
        self._hover_label = label

    def _set_hover(self, state: bool) -> None:
        if self._hover_label:
            self._hover_label.setProperty("hovered", "true" if state else "false")
            self._hover_label.style().unpolish(self._hover_label)
            self._hover_label.style().polish(self._hover_label)

    def enterEvent(self, event):
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hover(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class PromptCard(QFrame):
    clicked = pyqtSignal(int)
    ctrl_clicked = pyqtSignal(int)
    starred = pyqtSignal(int, bool)
    edit_req = pyqtSignal(int)
    delete_req = pyqtSignal(int)
    copy_req = pyqtSignal(int)
    selection_toggled = pyqtSignal(int, bool)
    context_menu_req = pyqtSignal(int, object)
    tag_clicked = pyqtSignal(str)
    folder_clicked = pyqtSignal(int)

    CARD_W = 290
    CARD_H = 195

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self._data = data
        self._selection_mode = False
        self.setObjectName("PromptCard")
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build()
        self._apply_folder_color()

    def prompt_id(self) -> int:
        return self._data["id"]

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self._select = QCheckBox()
        self._select.setVisible(False)
        self._select.toggled.connect(lambda v: self.selection_toggled.emit(self._data["id"], v))
        row1.addWidget(self._select)

        self._title = QLabel(self._data["title"])
        self._title.setObjectName("CardTitle")
        self._title.setWordWrap(False)
        self._title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title.setFixedWidth(self.CARD_W - 95)
        full_title = self._data.get("title", "")
        self._title.setText(_elide_text(self._title, full_title, self._title.width()))
        self._title.setToolTip(full_title)

        self._star = QPushButton()
        self._star.setObjectName("StarBtn")
        self._star.setFixedSize(28, 28)
        self._star.setIconSize(QSize(16, 16))
        self._star.setProperty("favorited", "true" if self._data.get("is_favorite") else "false")
        self._star.clicked.connect(self._on_star)
        self._refresh_star_icon()

        self._menu_btn = QPushButton("⋯")
        self._menu_btn.setObjectName("CardMenuBtn")
        self._menu_btn.setFixedSize(28, 28)
        self._menu_btn.clicked.connect(self._show_menu)

        row1.addWidget(self._title)
        row1.addStretch()
        row1.addWidget(self._star)
        row1.addWidget(self._menu_btn)
        outer.addLayout(row1)

        body_frame = QFrame()
        body_frame.setObjectName("CardBodyFrame")
        b_layout = QVBoxLayout(body_frame)
        b_layout.setContentsMargins(10, 8, 10, 8)

        content_plain = _plain_text(self._data.get("content", ""))
        body_text = content_plain[:180]
        if len(content_plain) > 180:
            body_text += "…"

        self._body = QLabel(body_text)
        self._body.setObjectName("CardBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        b_layout.addWidget(self._body)
        outer.addWidget(body_frame, 1)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(5)
        tags = self._data.get("tags", [])[:4]
        tag_colors = self._data.get("tag_colors", {})
        for tag in tags:
            chip = TagChip(tag, tag_colors.get(tag))
            chip.clicked.connect(self.tag_clicked)
            tag_row.addWidget(chip)
        tag_row.addStretch()
        outer.addLayout(tag_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(4)
        folder_name = (self._data.get("folder_name") or "").strip()
        if folder_name:
            folder_id = self._data.get("folder_id")
            folder_chip = _ClickableWidget()
            fc_layout = QHBoxLayout(folder_chip)
            fc_layout.setContentsMargins(0, 0, 0, 0)
            fc_layout.setSpacing(4)
            folder_icon = QLabel()
            folder_icon.setObjectName("CardDate")
            folder_icon.setFixedSize(14, 14)
            folder_icon.setPixmap(make_pixmap("folder.png", C["text_muted"], 14))
            folder_lbl = QLabel()
            folder_lbl.setObjectName("CardFolderLabel")
            folder_lbl.setFixedWidth(self.CARD_W - 130)
            folder_lbl.setText(_elide_text(folder_lbl, folder_name, folder_lbl.width()))
            folder_lbl.setToolTip(folder_name)
            folder_chip.set_hover_label(folder_lbl)
            fc_layout.addWidget(folder_icon)
            fc_layout.addWidget(folder_lbl)
            if folder_id is not None:
                folder_chip.clicked.connect(lambda fid=folder_id: self.folder_clicked.emit(fid))
            date_row.addWidget(folder_chip)
            sep = QLabel("•")
            sep.setObjectName("CardDate")
            date_row.addWidget(sep)

        cal_icon = QLabel()
        cal_icon.setObjectName("CardDate")
        cal_icon.setFixedSize(14, 14)
        cal_icon.setPixmap(make_pixmap("calendar.png", C["text_muted"], 14))
        date_lbl = QLabel(_fmt_date(self._data.get("created_at", "")))
        date_lbl.setObjectName("CardDate")
        date_row.addWidget(cal_icon)
        date_row.addWidget(date_lbl)
        date_row.addStretch()
        outer.addLayout(date_row)

    def _apply_folder_color(self):
        color = (self._data.get("folder_color") or "").strip()
        if color:
            self.setStyleSheet(f"QFrame#PromptCard {{ border-left: 3px solid {color}; }}")
        else:
            self.setStyleSheet("")

    def set_selection_mode(self, enabled: bool):
        self._selection_mode = enabled
        self._select.setVisible(enabled)
        if not enabled:
            self._select.setChecked(False)

    def set_selected(self, selected: bool):
        self._select.setChecked(selected)

    def set_kbd_selected(self, selected: bool):
        self.setProperty("kbd_selected", "true" if selected else "false")
        style = self.style()
        if style:
            style.unpolish(self)
            style.polish(self)

    def is_selected(self) -> bool:
        return self._select.isChecked()

    def _refresh_star_icon(self):
        color = C["star_on"] if self._data.get("is_favorite") else C["star_off"]
        self._star.setIcon(make_icon("favourites.png", color, 15))

    def _on_star(self):
        new_val = not self._data.get("is_favorite", False)
        self._data["is_favorite"] = new_val
        self._star.setProperty("favorited", "true" if new_val else "false")
        style = self._star.style()
        if style:
            style.unpolish(self._star)
            style.polish(self._star)
        self._refresh_star_icon()
        self.starred.emit(self._data["id"], new_val)

    def _show_menu(self):
        self.context_menu_req.emit(self._data["id"], QCursor.pos())

    def contextMenuEvent(self, event):
        self.context_menu_req.emit(self._data["id"], event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.ctrl_clicked.emit(self._data["id"])
                event.accept()
                return
            if not self._selection_mode:
                self.clicked.emit(self._data["id"])
        super().mousePressEvent(event)


class PromptListRow(QFrame):
    clicked = pyqtSignal(int)
    ctrl_clicked = pyqtSignal(int)
    starred = pyqtSignal(int, bool)
    edit_req = pyqtSignal(int)
    delete_req = pyqtSignal(int)
    copy_req = pyqtSignal(int)
    selection_toggled = pyqtSignal(int, bool)
    context_menu_req = pyqtSignal(int, object)
    tag_clicked = pyqtSignal(str)
    folder_clicked = pyqtSignal(int)

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self._data = data
        self._selection_mode = False
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(200)
        self._hover_timer.timeout.connect(self._show_preview)

        self.setObjectName("ListRow")
        self.setFixedHeight(58)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build()
        self._apply_folder_color()

    def prompt_id(self) -> int:
        return self._data["id"]

    def _build(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 12, 0)
        row.setSpacing(10)

        self._select = QCheckBox()
        self._select.setVisible(False)
        self._select.toggled.connect(lambda v: self.selection_toggled.emit(self._data["id"], v))
        row.addWidget(self._select)

        self._star = QPushButton()
        self._star.setObjectName("StarBtn")
        self._star.setFixedSize(26, 26)
        self._star.setIconSize(QSize(15, 15))
        self._star.setProperty("favorited", "true" if self._data.get("is_favorite") else "false")
        self._star.clicked.connect(self._on_star)
        self._refresh_star_icon()
        row.addWidget(self._star)

        title_text = self._data.get("title", "")
        title = QLabel(title_text)
        title.setObjectName("CardTitle")
        title.setFixedWidth(220)
        title.setText(_elide_text(title, title_text, title.width()))
        title.setToolTip(title_text)
        row.addWidget(title)

        content_plain = _plain_text(self._data.get("content", ""))
        preview = content_plain[:80].replace("\n", " ")
        body = QLabel(preview + "…" if len(content_plain) > 80 else preview)
        body.setObjectName("CardBody")
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(body, 1)

        tag_colors = self._data.get("tag_colors", {})
        for tag in self._data.get("tags", [])[:2]:
            chip = TagChip(tag, tag_colors.get(tag))
            chip.clicked.connect(self.tag_clicked)
            row.addWidget(chip)

        folder_name = (self._data.get("folder_name") or "").strip()
        if folder_name:
            folder_id = self._data.get("folder_id")
            folder_chip = _ClickableWidget()
            fc_layout = QHBoxLayout(folder_chip)
            fc_layout.setContentsMargins(0, 0, 0, 0)
            fc_layout.setSpacing(4)
            folder_icon_lbl = QLabel()
            folder_icon_lbl.setObjectName("CardDate")
            folder_icon_lbl.setFixedSize(13, 13)
            folder_icon_lbl.setPixmap(make_pixmap("folder.png", C["text_muted"], 13))
            folder_lbl = QLabel()
            folder_lbl.setObjectName("CardDate")
            folder_lbl.setFixedWidth(112)
            folder_lbl.setText(_elide_text(folder_lbl, folder_name, folder_lbl.width()))
            folder_lbl.setToolTip(folder_name)
            fc_layout.addWidget(folder_icon_lbl)
            fc_layout.addWidget(folder_lbl)
            if folder_id is not None:
                folder_chip.clicked.connect(lambda fid=folder_id: self.folder_clicked.emit(fid))
            row.addWidget(folder_chip)

        date = QLabel(_fmt_date(self._data.get("created_at", "")))
        date.setObjectName("CardDate")
        date.setFixedWidth(50)
        date.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(date)

        m_btn = QPushButton("⋯")
        m_btn.setObjectName("CardMenuBtn")
        m_btn.setFixedSize(28, 28)
        m_btn.clicked.connect(self._show_menu)
        row.addWidget(m_btn)

    def _apply_folder_color(self):
        color = (self._data.get("folder_color") or "").strip()
        if color:
            self.setStyleSheet(f"QFrame#ListRow {{ border-left: 3px solid {color}; }}")
        else:
            self.setStyleSheet("")

    def set_selection_mode(self, enabled: bool):
        self._selection_mode = enabled
        self._select.setVisible(enabled)
        if not enabled:
            self._select.setChecked(False)

    def set_selected(self, selected: bool):
        self._select.setChecked(selected)

    def set_kbd_selected(self, selected: bool):
        self.setProperty("kbd_selected", "true" if selected else "false")
        style = self.style()
        if style:
            style.unpolish(self)
            style.polish(self)

    def is_selected(self) -> bool:
        return self._select.isChecked()

    def _refresh_star_icon(self):
        color = C["star_on"] if self._data.get("is_favorite") else C["star_off"]
        self._star.setIcon(make_icon("favourites.png", color, 15))

    def _on_star(self):
        new_val = not self._data.get("is_favorite", False)
        self._data["is_favorite"] = new_val
        self._star.setProperty("favorited", "true" if new_val else "false")
        style = self._star.style()
        if style:
            style.unpolish(self._star)
            style.polish(self._star)
        self._refresh_star_icon()
        self.starred.emit(self._data["id"], new_val)

    def _show_menu(self):
        self.context_menu_req.emit(self._data["id"], QCursor.pos())

    def contextMenuEvent(self, event):
        self.context_menu_req.emit(self._data["id"], event.globalPos())
        event.accept()

    def _show_preview(self):
        if self._selection_mode:
            return
        text = _plain_text(self._data.get("content", ""))
        if not text:
            return
        pos = self.mapToGlobal(self.rect().bottomLeft())
        QToolTip.showText(pos, text, self)

    def enterEvent(self, event):
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_timer.stop()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.ctrl_clicked.emit(self._data["id"])
                event.accept()
                return
            if not self._selection_mode:
                self.clicked.emit(self._data["id"])
        super().mousePressEvent(event)
