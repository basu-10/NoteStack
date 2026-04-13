"""
Tag management dialog — create / rename / delete tags.
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
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class TagManagerModal(QDialog):
    changed = pyqtSignal()

    def __init__(self, tags: list[dict], db_ops: dict, parent=None):
        """
        db_ops = {
            'create': callable(name, color=None) -> int,
            'rename': callable(id, name),
            'delete': callable(id),
            'set_color': callable(id, color),
        }
        """
        super().__init__(parent)
        self._tags = list(tags)
        self._ops = db_ops
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 26, 30, 26)
        v.setSpacing(0)

        hdr = QHBoxLayout()
        title = QLabel("Manage Tags")
        title.setObjectName("ModalTitle")
        close = QPushButton("✕")
        close.setObjectName("CloseBtn")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.accept)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(close)
        v.addLayout(hdr)
        v.addSpacing(20)

        div = QFrame()
        div.setObjectName("Divider")
        v.addWidget(div)
        v.addSpacing(18)

        new_lbl = QLabel("NEW TAG")
        new_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(new_lbl)
        v.addSpacing(8)

        new_row = QHBoxLayout()
        new_row.setSpacing(8)
        self._new_input = QLineEdit()
        self._new_input.setObjectName("ModalInput")
        self._new_input.setPlaceholderText("Tag name…")
        self._new_input.setFixedHeight(40)
        self._new_input.returnPressed.connect(self._create_tag)

        self._new_color = ""
        self._new_color_btn = QPushButton("Color")
        self._new_color_btn.setObjectName("BtnSecondary")
        self._new_color_btn.setFixedHeight(40)
        self._new_color_btn.clicked.connect(self._pick_new_color)

        add_btn = QPushButton("+ Add")
        add_btn.setObjectName("BtnPrimary")
        add_btn.setFixedHeight(40)
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._create_tag)

        new_row.addWidget(self._new_input, 1)
        new_row.addWidget(self._new_color_btn)
        new_row.addWidget(add_btn)
        v.addLayout(new_row)
        v.addSpacing(20)

        ex_lbl = QLabel("EXISTING TAGS")
        ex_lbl.setObjectName("ModalSectionLabel")
        v.addWidget(ex_lbl)
        v.addSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(260)

        self._tag_container = QWidget()
        self._tag_layout = QVBoxLayout(self._tag_container)
        self._tag_layout.setSpacing(6)
        self._tag_layout.setContentsMargins(0, 0, 0, 0)
        self._tag_layout.addStretch()

        scroll.setWidget(self._tag_container)
        v.addWidget(scroll)

        outer.addWidget(panel)
        self._refresh_list()

    def _swatch_style(self, color: str) -> str:
        if color:
            return f"background:{color}; border:1px solid #2A2A45; border-radius:6px;"
        return "background:#1E1E30; border:1px solid #2A2A45; border-radius:6px;"

    def _pick_new_color(self):
        picked = QColorDialog.getColor(QColor(self._new_color or "#4F6EF7"), self, "Tag Color")
        if not picked.isValid():
            return
        self._new_color = picked.name()
        self._new_color_btn.setStyleSheet(self._swatch_style(self._new_color))

    def _refresh_list(self):
        while self._tag_layout.count() > 1:
            item = self._tag_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

        tags_sorted = sorted(self._tags, key=lambda x: x["name"].lower())
        for tag in tags_sorted:
            row = QHBoxLayout()
            row.setSpacing(8)

            name_inp = QLineEdit(tag["name"])
            name_inp.setObjectName("ModalInput")
            name_inp.setFixedHeight(36)

            color_btn = QPushButton(" ")
            color_btn.setFixedSize(36, 36)
            color_btn.setStyleSheet(self._swatch_style(tag.get("color") or ""))
            color_btn.clicked.connect(lambda _, tid=tag["id"]: self._pick_row_color(tid))

            save_btn = QPushButton("Save")
            save_btn.setObjectName("BtnSecondary")
            save_btn.setFixedHeight(36)
            save_btn.setFixedWidth(64)
            save_btn.clicked.connect(lambda _, tid=tag["id"], inp=name_inp: self._save(tid, inp))

            del_btn = QPushButton("🗑")
            del_btn.setObjectName("BtnDanger")
            del_btn.setFixedHeight(36)
            del_btn.setFixedWidth(36)
            del_btn.clicked.connect(lambda _, tid=tag["id"]: self._delete(tid))

            row.addWidget(name_inp, 1)
            row.addWidget(color_btn)
            row.addWidget(save_btn)
            row.addWidget(del_btn)

            container = QWidget()
            container.setLayout(row)
            self._tag_layout.insertWidget(self._tag_layout.count() - 1, container)

    def _pick_row_color(self, tag_id: int):
        tag = next((t for t in self._tags if t["id"] == tag_id), None)
        if not tag:
            return
        picked = QColorDialog.getColor(QColor(tag.get("color") or "#4F6EF7"), self, "Tag Color")
        if not picked.isValid():
            return
        tag["color"] = picked.name()
        self._ops["set_color"](tag_id, tag["color"])
        self._refresh_list()
        self.changed.emit()

    def _create_tag(self):
        name = self._new_input.text().strip()
        if not name:
            return
        color = self._new_color or None
        tag_id = self._ops["create"](name, color)
        if not tag_id:
            return
        normalized_name = name.strip().lower().lstrip("#")
        self._tags.append({"id": tag_id, "name": normalized_name, "color": color})
        self._new_input.clear()
        self._new_color = ""
        self._new_color_btn.setStyleSheet("")
        self._refresh_list()
        self.changed.emit()

    def _save(self, tag_id: int, name_inp: QLineEdit):
        name = name_inp.text().strip()
        if not name:
            return
        normalized_name = name.lower().lstrip("#")
        self._ops["rename"](tag_id, normalized_name)
        for tag in self._tags:
            if tag["id"] == tag_id:
                tag["name"] = normalized_name
                break
        self._refresh_list()
        self.changed.emit()

    def _delete(self, tag_id: int):
        self._ops["delete"](tag_id)
        self._tags = [t for t in self._tags if t["id"] != tag_id]
        self._refresh_list()
        self.changed.emit()
        self.accept()