"""
Main window for NoteStack.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, QTimer, pyqtSignal, QMimeData
from PyQt6.QtGui import QAction, QActionGroup, QColor, QCursor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import database.db as db
import database.seed as db_seed
from ui.flow_layout import FlowLayout
from ui.icon_utils import make_icon, make_pixmap, make_png_icon, resources_dir
from ui.modals.bulk_export_modal import BulkExportModal
from ui.modals.create_folder_modal import CreateFolderModal
from ui.modals.advanced_search_modal import AdvancedSearchModal
from ui.modals.item_edit_modal import ItemEditModal
from ui.modals.new_prompt_modal import NewPromptModal, TagSuggestLineEdit
from ui.modals.prompt_detail_modal import PromptDetailModal
from ui.modals.settings_modal import SettingsModal
from ui.modals.tag_modal import TagManagerModal
from ui.prompt_card import PromptCard, PromptListRow
from ui.snap_utils import SnapOverlay, get_snap_zone
from ui.styles import C, SIDEBAR_MIN_W, SIDEBAR_W, make_stylesheet, normalize_theme, set_theme


def _hex_to_rgba(color: str | None, alpha: int) -> str:
    value = str(color or "").strip().lstrip("#")
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


class FilterState:
    def __init__(self):
        self.section: str | int = "all"
        self.keyword: str = ""
        self.tags: list[str] = []
        self.sort: str = "newest"
        self.view: str = "grid"

    def to_query(self) -> dict:
        q: dict = {"sort": self.sort}
        q["favorites_only"] = self.section == "favorites"
        q["folder_id"] = self.section if isinstance(self.section, int) else None
        if self.keyword:
            q["keyword"] = self.keyword
        if self.tags:
            q["tag_names"] = self.tags
        return q

    @property
    def has_filters(self) -> bool:
        return bool(self.keyword or self.tags)


class FolderTreeWidget(QTreeWidget):
    move_req = pyqtSignal(int, object)
    invalid_move = pyqtSignal(str)
    folder_context_req = pyqtSignal(int, object)
    root_context_req = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FolderTree")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _folder_id(self, item: QTreeWidgetItem | None) -> int | None:
        if item is None:
            return None
        folder_id = item.data(0, Qt.ItemDataRole.UserRole)
        return folder_id if isinstance(folder_id, int) else None

    def _is_descendant(self, ancestor: QTreeWidgetItem, candidate: QTreeWidgetItem | None) -> bool:
        node = candidate
        while node is not None:
            if node is ancestor:
                return True
            node = node.parent()
        return False

    def _subtree_height(self, item: QTreeWidgetItem) -> int:
        if item.childCount() == 0:
            return 1
        max_child = 1
        for i in range(item.childCount()):
            child = item.child(i)
            if child:
                max_child = max(max_child, 1 + self._subtree_height(child))
        return max_child

    def _depth_from_root(self, item: QTreeWidgetItem | None) -> int:
        depth = 0
        node = item
        while node is not None:
            depth += 1
            node = node.parent()
        return depth

    def _resolve_drop_parent(self, event, target: QTreeWidgetItem | None):
        pos = self.dropIndicatorPosition()
        if pos == QAbstractItemView.DropIndicatorPosition.OnViewport:
            return None
        if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            return target
        if target is None:
            return None
        return target.parent()

    def dropEvent(self, event):
        moving_item = self.currentItem()
        moving_id = self._folder_id(moving_item)
        if moving_item is None or moving_id is None:
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        new_parent_item = self._resolve_drop_parent(event, target_item)
        new_parent_id = self._folder_id(new_parent_item)

        if new_parent_item is not None and self._is_descendant(moving_item, new_parent_item):
            self.invalid_move.emit("Cannot move a folder into itself or its own child.")
            event.ignore()
            return

        subtree_height = self._subtree_height(moving_item)
        final_depth = self._depth_from_root(new_parent_item) + subtree_height
        if final_depth > 20:
            self.invalid_move.emit("Folder nesting is limited to 20 levels.")
            event.ignore()
            return

        old_parent_item = moving_item.parent()
        old_parent_id = self._folder_id(old_parent_item)
        if old_parent_id == new_parent_id:
            event.ignore()
            return

        super().dropEvent(event)
        self.move_req.emit(moving_id, new_parent_id)

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        global_pos = self.viewport().mapToGlobal(pos)
        folder_id = self._folder_id(item)
        if folder_id is not None:
            self.folder_context_req.emit(folder_id, global_pos)
        else:
            self.root_context_req.emit(global_pos)


class Sidebar(QWidget):
    nav_changed = pyqtSignal(object)
    create_folder_req = pyqtSignal(object)
    create_note_in_folder_req = pyqtSignal(int)
    edit_folder_req = pyqtSignal(int)
    delete_folder_req = pyqtSignal(int)
    folder_parent_change_req = pyqtSignal(int, object)
    folder_move_invalid = pyqtSignal(str)
    manage_tags_req = pyqtSignal()
    tag_filter_changed = pyqtSignal(object)
    recent_prompt_req = pyqtSignal(int)
    settings_req = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._nav_btns: dict = {}
        self._nav_icon_files: dict[str, str] = {}
        self._tag_btns: dict = {}
        self._tag_meta: dict[str, dict] = {}
        self._folder_items: dict[int, QTreeWidgetItem] = {}
        self._recent_btns: list[tuple[QPushButton, str]] = []
        self._active_tag: str | None = None
        self._tag_colors: dict[str, str] = {}
        self._settings_btn: QPushButton | None = None
        self._manage_tags_btn: QPushButton | None = None
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Logo (pinned at top, outside scroll area) ─────────────
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(20, -10, 16, 4)
        logo_row.setSpacing(10)
        logo_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._logo_icon = QLabel()
        self._logo_icon.setFixedSize(28, 28)
        _logo_path = resources_dir() / "project_logo.png"
        _logo_pix = QPixmap(str(_logo_path))
        if not _logo_pix.isNull():
            self._logo_icon.setPixmap(
                _logo_pix.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        self._logo_title = QLabel()
        self._logo_title.setObjectName("LogoLabel")
        self._logo_title.setTextFormat(Qt.TextFormat.RichText)
        self._logo_title.setText(
            "<span style='color:#14B8A6;font-size:22px;font-weight:700;'>Note</span>"
            "<span style='color:#9CA3AF;font-size:22px;font-weight:700;'>Stack</span>"
        )
        logo_row.addWidget(self._logo_icon)
        logo_row.addWidget(self._logo_title)
        logo_row.addStretch()
        v.addLayout(logo_row)
        v.addSpacing(8)

        # ── Scrollable body (RECENT → FOLDERS → TAGS) ─────────────
        # Wrapping in a QScrollArea prevents folders/tags from
        # overlapping on shorter windows across all platforms.
        self._sidebar_scroll = QScrollArea()
        self._sidebar_scroll.setObjectName("SidebarScroll")
        self._sidebar_scroll.setWidgetResizable(True)
        self._sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)

        _scroll_body = QWidget()
        _scroll_body.setObjectName("SidebarScrollContent")
        sv = QVBoxLayout(_scroll_body)
        sv.setContentsMargins(0, 0, 0, 8)
        sv.setSpacing(0)

        _recent_hdr = QHBoxLayout()
        _recent_hdr.setContentsMargins(20, 6, 20, 4)
        _recent_hdr.setSpacing(6)
        self._recent_icon_lbl = QLabel()
        self._recent_icon_lbl.setFixedSize(12, 12)
        self._recent_label = QLabel("RECENT")
        self._recent_label.setObjectName("SidebarSectionLabel")
        _recent_hdr.addWidget(self._recent_icon_lbl)
        _recent_hdr.addWidget(self._recent_label)
        _recent_hdr.addStretch()
        sv.addLayout(_recent_hdr)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setObjectName("SidebarRecentScroll")
        self._recent_scroll.setWidgetResizable(True)
        self._recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._recent_scroll.setMinimumHeight(64)
        self._recent_scroll.setMaximumHeight(224)

        self._recent_wrap = QWidget()
        self._recent_container = QVBoxLayout(self._recent_wrap)
        self._recent_container.setContentsMargins(0, 0, 0, 0)
        self._recent_container.setSpacing(0)
        self._recent_container.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._recent_scroll.setWidget(self._recent_wrap)
        sv.addWidget(self._recent_scroll)
        sv.addSpacing(8)

        self._nav_btns["all"] = self._make_nav("All Notes", "all", "all files.png")
        self._nav_btns["favorites"] = self._make_nav("Favorites", "favorites", "favourites.png")
        self._nav_btns["trash"] = self._make_nav("Trash", "trash", "trash.png")
        sv.addWidget(self._nav_btns["all"])
        sv.addWidget(self._nav_btns["favorites"])
        sv.addWidget(self._nav_btns["trash"])
        sv.addSpacing(8)

        folders_hdr = QHBoxLayout()
        folders_hdr.setContentsMargins(20, 8, 10, 4)
        self._folders_label = QLabel("FOLDERS")
        self._folders_label.setObjectName("SidebarSectionLabel")

        new_folder_btn = QPushButton("＋")
        new_folder_btn.setObjectName("SidebarIconBtn")
        new_folder_btn.setFixedSize(22, 22)
        new_folder_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        new_folder_btn.setToolTip("Create Folder in Root")
        new_folder_btn.clicked.connect(lambda: self.create_folder_req.emit(None))
        folders_hdr.addWidget(self._folders_label)
        folders_hdr.addStretch()
        folders_hdr.addWidget(new_folder_btn)
        sv.addLayout(folders_hdr)

        self._folder_tree = FolderTreeWidget()
        self._folder_tree.setMinimumHeight(60)
        self._folder_tree.itemClicked.connect(self._on_folder_clicked)
        self._folder_tree.move_req.connect(self.folder_parent_change_req.emit)
        self._folder_tree.invalid_move.connect(self.folder_move_invalid.emit)
        self._folder_tree.folder_context_req.connect(self._show_folder_context_menu)
        self._folder_tree.root_context_req.connect(self._show_root_context_menu)
        sv.addWidget(self._folder_tree)

        sv.addSpacing(8)

        tags_hdr = QHBoxLayout()
        tags_hdr.setContentsMargins(20, 8, 10, 4)

        self._tags_label = QLabel("TAGS")
        self._tags_label.setObjectName("SidebarSectionLabel")

        manage_tags_btn = QPushButton()
        manage_tags_btn.setObjectName("SidebarIconBtn")
        manage_tags_btn.setFixedSize(22, 22)
        manage_tags_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        manage_tags_btn.setToolTip("Manage Tags")
        manage_tags_btn.clicked.connect(self.manage_tags_req.emit)
        self._manage_tags_btn = manage_tags_btn

        tags_hdr.addWidget(self._tags_label)
        tags_hdr.addStretch()
        tags_hdr.addWidget(manage_tags_btn)
        sv.addLayout(tags_hdr)

        self._tags_wrap = QWidget()
        self._tags_wrap.setStyleSheet("background:transparent;")
        self._tags_container = FlowLayout(self._tags_wrap, h_spacing=6, v_spacing=6)
        self._tags_container.setContentsMargins(20, 4, 12, 8)
        sv.addWidget(self._tags_wrap)

        self._sidebar_scroll.setWidget(_scroll_body)
        v.addWidget(self._sidebar_scroll, 1)

        # ── Settings (pinned at bottom, outside scroll area) ───────
        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("NavBtn")
        settings_btn.setFixedHeight(38)
        settings_btn.setIconSize(QSize(16, 16))
        settings_btn.clicked.connect(self.settings_req.emit)
        v.addWidget(settings_btn)
        self._settings_btn = settings_btn

        self._apply_theme_tokens()

    def _apply_theme_tokens(self):
        self._logo_title.setStyleSheet(
            f"color:{C['text_primary']}; font-size:16px; font-weight:700; padding:0;"
        )
        self._recent_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:9px; font-weight:700; letter-spacing:1.5px; padding:0;"
        )
        self._recent_icon_lbl.setPixmap(make_pixmap("recents.png", C["text_muted"], 12))
        self._folders_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:9px; font-weight:700; letter-spacing:1.5px; padding:0;"
        )
        self._tags_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:9px; font-weight:700; letter-spacing:1.5px; padding:0;"
        )
        # Refresh nav button icons with current theme colour
        icon_color = C["text_secondary"]
        for key, btn in self._nav_btns.items():
            icon_file = self._nav_icon_files.get(key, "")
            if icon_file:
                btn.setIcon(make_icon(icon_file, icon_color, 16))
        if self._settings_btn is not None:
            self._settings_btn.setIcon(make_icon("settings.png", icon_color, 16))
        if self._manage_tags_btn is not None:
            self._manage_tags_btn.setIcon(make_icon("tags.png", icon_color, 14))
            self._manage_tags_btn.setIconSize(QSize(14, 14))

    def apply_theme_tokens(self):
        self._apply_theme_tokens()

    def _make_nav(self, label: str, key, icon_file: str = "") -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("NavBtn")
        btn.setFixedHeight(34)
        btn.setProperty("active", "false")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.setIconSize(QSize(16, 16))
        if icon_file:
            self._nav_icon_files[key] = icon_file
            btn.setIcon(make_icon(icon_file, C["text_secondary"], 16))
        btn.clicked.connect(lambda: self._activate(key))
        return btn

    def _color_icon(self, color: str | None) -> QIcon:
        pix = QPixmap(12, 12)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color or "#6B7280"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        return QIcon(pix)

    def _on_folder_clicked(self, item: QTreeWidgetItem, _col: int):
        folder_id = item.data(0, Qt.ItemDataRole.UserRole)
        self._activate(folder_id)

    def _show_folder_context_menu(self, folder_id: int, global_pos):
        _ic = C["text_secondary"]
        menu = QMenu(self)
        create_folder_action = menu.addAction(make_icon("folder.png", _ic, 16), "Create Folder Here")
        create_note_action = menu.addAction("Create Note Here")
        menu.addSeparator()
        edit_action = menu.addAction("Edit Folder")
        delete_action = menu.addAction(make_icon("trash.png", C["danger"], 16), "Delete Folder")

        chosen = menu.exec(global_pos)
        if chosen is create_folder_action:
            self.create_folder_req.emit(folder_id)
        elif chosen is create_note_action:
            self.create_note_in_folder_req.emit(folder_id)
        elif chosen is edit_action:
            self.edit_folder_req.emit(folder_id)
        elif chosen is delete_action:
            self.delete_folder_req.emit(folder_id)

    def _show_root_context_menu(self, global_pos):
        menu = QMenu(self)
        create_folder_action = menu.addAction(make_icon("folder.png", C["text_secondary"], 16), "Create Folder in Root")
        chosen = menu.exec(global_pos)
        if chosen is create_folder_action:
            self.create_folder_req.emit(None)

    def _activate(self, key):
        self._update_active_display(key)
        self.nav_changed.emit(key)

    def _update_active_display(self, key):
        for k, btn in self._nav_btns.items():
            active = k == key
            btn.setProperty("active", "true" if active else "false")
            style = btn.style()
            if style:
                style.unpolish(btn)
                style.polish(btn)

        if isinstance(key, int):
            self._folder_tree.blockSignals(True)
            self._folder_tree.clearSelection()
            item = self._folder_items.get(key)
            if item:
                item.setSelected(True)
                self._folder_tree.setCurrentItem(item)
            self._folder_tree.blockSignals(False)
        else:
            self._folder_tree.blockSignals(True)
            self._folder_tree.clearSelection()
            self._folder_tree.blockSignals(False)

    def set_active(self, key):
        self._update_active_display(key)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_layout_dependent_ui()

    def refresh_recent(self, prompts: list[dict]):
        while self._recent_container.count():
            item = self._recent_container.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        self._recent_btns.clear()

        for prompt in prompts[:8]:
            title = prompt["title"]
            full_label = f"  • {title}"
            btn = QPushButton(full_label)
            btn.setObjectName("NavBtn")
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _, pid=prompt["id"]: self.recent_prompt_req.emit(pid))
            self._recent_container.addWidget(btn)
            self._recent_btns.append((btn, full_label))

        self._update_recent_elide()

    def refresh_folders(self, folders: list[dict]):
        self._folder_tree.clear()
        self._folder_items.clear()

        sorted_folders = sorted(folders, key=lambda f: f["name"].lower())
        by_id: dict[int, QTreeWidgetItem] = {}
        for folder in sorted_folders:
            item = QTreeWidgetItem([folder["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, folder["id"])
            item.setIcon(0, self._color_icon(folder.get("color")))
            by_id[folder["id"]] = item

        for folder in sorted_folders:
            item = by_id[folder["id"]]
            parent_id = folder.get("parent_id")
            if parent_id and parent_id in by_id:
                by_id[parent_id].addChild(item)
            else:
                self._folder_tree.addTopLevelItem(item)

        for i in range(self._folder_tree.topLevelItemCount()):
            top = self._folder_tree.topLevelItem(i)
            if top:
                top.setExpanded(True)

        self._folder_items = by_id
        self._update_folder_column_width()

    def refresh_tags(self, tags: list[dict]):
        while self._tags_container.count():
            item = self._tags_container.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        self._tag_btns.clear()
        self._tag_meta.clear()
        self._tag_colors.clear()

        for tag in tags:
            btn = QPushButton(f"#{tag['name']}")
            btn.setObjectName("FilterTagChip")
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked, name=tag["name"]: self._on_tag_clicked(name))
            tag_color = tag.get("color") or C["border_tag"]
            self._tag_colors[tag["name"]] = tag_color
            self._tags_container.addWidget(btn)
            self._tag_btns[tag["name"]] = btn
            self._tag_meta[tag["name"]] = {"id": tag.get("id"), "color": tag.get("color")}

        self.set_active_tag(self._active_tag)

    def refresh_trash_count(self, count: int):
        btn = self._nav_btns.get("trash")
        if btn is None:
            return
        if count > 0:
            btn.setText(f"Trash  ({count})")
        else:
            btn.setText("Trash")

    def _on_tag_clicked(self, tag_name: str):
        new_tag = None if self._active_tag == tag_name else tag_name
        self.set_active_tag(new_tag)
        self.tag_filter_changed.emit(new_tag)

    def set_active_tag(self, tag_name: str | None):
        self._active_tag = tag_name
        for name, btn in self._tag_btns.items():
            is_selected = name == tag_name
            btn.setChecked(is_selected)
            btn.setProperty("selected", "true" if is_selected else "false")
            color = self._tag_colors.get(name, C["border_tag"])
            bg = _hex_to_rgba(color, 48) if is_selected else "transparent"
            btn.setStyleSheet(f"border:1px solid {color}; background:{bg}; border-radius:10px;")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def contains_widget(self, widget: QWidget | None) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _focus_folder_tree(self):
        self._folder_tree.setFocus()
        if self._folder_tree.currentItem() is None and self._folder_tree.topLevelItemCount() > 0:
            item = self._folder_tree.topLevelItem(0)
            if item:
                self._folder_tree.setCurrentItem(item)

    def _focus_first_tag(self) -> bool:
        if not self._tag_btns:
            return False
        first_btn = next(iter(self._tag_btns.values()))
        first_btn.setFocus()
        return True

    def focus_cycle(self):
        fw = QApplication.focusWidget()
        if fw is self._folder_tree or (fw is not None and self._folder_tree.isAncestorOf(fw)):
            if self._focus_first_tag():
                return
        self._focus_folder_tree()

    def _update_layout_dependent_ui(self):
        self._update_folder_column_width()
        self._update_recent_elide()

    def _update_folder_column_width(self):
        viewport = self._folder_tree.viewport()
        available_width = max(0, viewport.width() if viewport else 0)
        self._folder_tree.setColumnWidth(0, available_width)

    def _update_recent_elide(self):
        for btn, full_label in self._recent_btns:
            available = max(20, btn.width() - 36)
            text = btn.fontMetrics().elidedText(full_label, Qt.TextElideMode.ElideRight, available)
            btn.setText(text)


class TopBar(QWidget):
    new_prompt_req = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self._build()

    def _build(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(24, 0, 24, 0)
        h.setSpacing(14)
        h.addStretch()

        self._new_btn = QPushButton("✦  New Note")
        self._new_btn.setObjectName("BtnPrimary")
        self._new_btn.setFixedHeight(38)
        self._new_btn.clicked.connect(self.new_prompt_req.emit)
        h.addWidget(self._new_btn)

    def set_new_prompt_tooltip(self, text: str):
        self._new_btn.setToolTip(text)


class WindowChromeBar(QWidget):
    def __init__(self, window: QMainWindow, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = QPoint()
        self._dragging = False
        self._drag_from_maximized = False
        self._drag_from_snapped = False
        self._drag_norm_x = 0.5
        self._pre_snap_geometry: QRect | None = None
        self._snap_zone: str | None = None
        self._snap_rect: QRect | None = None
        self._snap_overlay = SnapOverlay()
        self.setObjectName("WindowChromeBar")
        self.setFixedHeight(36)
        self._build()
        self.update_window_title(window.windowTitle())
        self.sync_maximize_state()

    def _build(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 0, 0)
        h.setSpacing(8)

        self._title_lbl = QLabel("NoteStack")
        self._title_lbl.setObjectName("WindowChromeTitle")
        h.addWidget(self._title_lbl)
        h.addStretch()

        self._min_btn = QPushButton("−")
        self._min_btn.setObjectName("WindowChromeBtn")
        self._min_btn.setFixedSize(42, 28)
        self._min_btn.clicked.connect(self._window.showMinimized)

        self._max_btn = QPushButton("□")
        self._max_btn.setObjectName("WindowChromeBtn")
        self._max_btn.setFixedSize(42, 28)
        self._max_btn.clicked.connect(self._toggle_maximize)

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("WindowChromeCloseBtn")
        self._close_btn.setFixedSize(42, 28)
        self._close_btn.clicked.connect(self._window.close)

        h.addWidget(self._min_btn)
        h.addWidget(self._max_btn)
        h.addWidget(self._close_btn)

        self.update_window_icon()

    def update_window_icon(self):
        pass

    def update_window_title(self, title: str):
        display = title if title and title != "NoteStack" else ""
        self._title_lbl.setText(display)
        self._title_lbl.setVisible(bool(display))

    def sync_maximize_state(self):
        self._max_btn.setText("❐" if self._window.isMaximized() else "□")

    def _toggle_maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_maximize_state()

    # ---- snap-to-side -------------------------------------------------------

    def _hide_snap_overlay(self):
        self._snap_zone = None
        self._snap_rect = None
        self._snap_overlay.hide()

    # ---- mouse events -------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            global_pos = event.globalPosition().toPoint()
            if self._window.isMaximized():
                # Track normalized cursor position so we can restore sensibly
                self._drag_from_maximized = True
                self._drag_from_snapped = False
                self._drag_norm_x = event.position().x() / max(self.width(), 1)
            elif self._pre_snap_geometry is not None:
                # Window is in a custom left/right snap state — restore on first move
                self._drag_from_snapped = True
                self._drag_from_maximized = False
                self._drag_norm_x = event.position().x() / max(self.width(), 1)
            else:
                self._drag_from_maximized = False
                self._drag_from_snapped = False
                self._drag_offset = global_pos - self._window.frameGeometry().topLeft()
            self._snap_zone = None
            self._snap_rect = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            global_pos = event.globalPosition().toPoint()

            if self._drag_from_maximized:
                # Restore the window and recompute drag offset on first move
                self._drag_from_maximized = False
                self._window.showNormal()
                normal_w = self._window.width()
                offset_x = int(self._drag_norm_x * normal_w)
                offset_x = max(20, min(offset_x, normal_w - 20))
                self._drag_offset = QPoint(offset_x, self.height() // 2)
                self._window.move(global_pos - self._drag_offset)
                event.accept()
                return

            if self._drag_from_snapped:
                # Restore pre-snap geometry and recompute drag offset on first move
                self._drag_from_snapped = False
                pre_geo = self._pre_snap_geometry
                self._pre_snap_geometry = None
                self._window.setGeometry(pre_geo)
                normal_w = pre_geo.width()
                offset_x = int(self._drag_norm_x * normal_w)
                offset_x = max(20, min(offset_x, normal_w - 20))
                self._drag_offset = QPoint(offset_x, self.height() // 2)
                self._window.move(global_pos - self._drag_offset)
                event.accept()
                return

            self._window.move(global_pos - self._drag_offset)

            zone, rect = get_snap_zone(global_pos)
            if rect is not None:
                self._snap_zone = zone
                self._snap_rect = rect
                self._snap_overlay.setGeometry(rect)
                if not self._snap_overlay.isVisible():
                    self._snap_overlay.show()
                self._snap_overlay.update()
            else:
                self._hide_snap_overlay()

            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_from_maximized = False
            self._drag_from_snapped = False
            snap_zone = self._snap_zone
            snap_rect = self._snap_rect
            self._hide_snap_overlay()

            if snap_rect is not None:
                if snap_zone == "maximize":
                    self._pre_snap_geometry = None
                    self._window.showMaximized()
                else:
                    if self._pre_snap_geometry is None:
                        self._pre_snap_geometry = self._window.geometry()
                    self._window.setGeometry(snap_rect)
                event.accept()
                return
            else:
                self._pre_snap_geometry = None
        self._dragging = False
        self._drag_from_maximized = False
        self._drag_from_snapped = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CardGrid(QWidget):
    card_clicked = pyqtSignal(int)
    card_starred = pyqtSignal(int, bool)
    card_edit = pyqtSignal(int)
    card_delete = pyqtSignal(int)
    card_copy = pyqtSignal(int)
    ctrl_clicked = pyqtSignal(int)
    selection_toggled = pyqtSignal(int, bool)
    card_context_menu = pyqtSignal(int, object)
    area_context_menu = pyqtSignal(object)
    tag_clicked = pyqtSignal(str)
    folder_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = FlowLayout(self, h_spacing=14, v_spacing=14)
        self._cards: list[PromptCard] = []
        self._selection_mode = False

    def load(self, prompts: list[dict]):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        self._cards.clear()

        for prompt in prompts:
            card = PromptCard(prompt)
            card.set_selection_mode(self._selection_mode)
            card.clicked.connect(self.card_clicked)
            card.starred.connect(self.card_starred)
            card.edit_req.connect(self.card_edit)
            card.delete_req.connect(self.card_delete)
            card.copy_req.connect(self.card_copy)
            card.context_menu_req.connect(self.card_context_menu)
            card.ctrl_clicked.connect(self.ctrl_clicked)
            card.selection_toggled.connect(self.selection_toggled)
            card.tag_clicked.connect(self.tag_clicked)
            card.folder_clicked.connect(self.folder_clicked)
            self._layout.addWidget(card)
            self._cards.append(card)

        self.updateGeometry()

    def set_selection_mode(self, enabled: bool):
        self._selection_mode = enabled
        for card in self._cards:
            card.set_selection_mode(enabled)

    def set_card_selected(self, prompt_id: int, selected: bool):
        for card in self._cards:
            if card.prompt_id() == prompt_id:
                card.set_selected(selected)
                break

    def get_cards(self) -> list[PromptCard]:
        return list(self._cards)

    def contextMenuEvent(self, event):
        self.area_context_menu.emit(event.globalPos())
        event.accept()


class ListContainer(QWidget):
    row_clicked = pyqtSignal(int)
    row_starred = pyqtSignal(int, bool)
    row_edit = pyqtSignal(int)
    row_delete = pyqtSignal(int)
    row_copy = pyqtSignal(int)
    ctrl_clicked = pyqtSignal(int)
    selection_toggled = pyqtSignal(int, bool)
    row_context_menu = pyqtSignal(int, object)
    area_context_menu = pyqtSignal(object)
    tag_clicked = pyqtSignal(str)
    folder_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addStretch()
        self._selection_mode = False

    def load(self, prompts: list[dict]):
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

        for prompt in prompts:
            row = PromptListRow(prompt)
            row.set_selection_mode(self._selection_mode)
            row.clicked.connect(self.row_clicked)
            row.starred.connect(self.row_starred)
            row.edit_req.connect(self.row_edit)
            row.delete_req.connect(self.row_delete)
            row.copy_req.connect(self.row_copy)
            row.context_menu_req.connect(self.row_context_menu)
            row.ctrl_clicked.connect(self.ctrl_clicked)
            row.selection_toggled.connect(self.selection_toggled)
            row.tag_clicked.connect(self.tag_clicked)
            row.folder_clicked.connect(self.folder_clicked)
            self._layout.insertWidget(self._layout.count() - 1, row)

        self.updateGeometry()

    def set_selection_mode(self, enabled: bool):
        self._selection_mode = enabled
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, PromptListRow):
                widget.set_selection_mode(enabled)

    def set_card_selected(self, prompt_id: int, selected: bool):
        for row in self.get_rows():
            if row.prompt_id() == prompt_id:
                row.set_selected(selected)
                break

    def get_rows(self) -> list[PromptListRow]:
        rows: list[PromptListRow] = []
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, PromptListRow):
                rows.append(widget)
        return rows

    def contextMenuEvent(self, event):
        self.area_context_menu.emit(event.globalPos())
        event.accept()


class EmptyStateWidget(QWidget):
    create_req = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 40, 0, 40)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._illustration = QLabel("✦")
        self._illustration.setStyleSheet(f"font-size:72px; color:{C['accent']};")
        self._title = QLabel("Your vault is empty")
        self._title.setObjectName("ContentHeader")
        self._subtitle = QLabel("Create your first prompt to start building your library.")
        self._subtitle.setObjectName("ContentSubtitle")

        self._cta_btn = QPushButton("＋ Create your first prompt")
        self._cta_btn.setObjectName("BtnPrimary")
        self._cta_btn.setFixedHeight(40)
        self._cta_btn.clicked.connect(self.create_req.emit)

        layout.addWidget(self._illustration, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._subtitle, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._cta_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_folder_mode(self, is_folder: bool):
        if is_folder:
            self._title.setText("This folder is empty")
            self._subtitle.setText("Notes added to this folder will appear here. You can also create subfolders to organize your notes hierarchically. Create either a subfolder or a note by clicking the + button.")
            self._cta_btn.hide()
        else:
            self._title.setText("Your vault is empty")
            self._subtitle.setText("Create your first prompt to start building your library.")
            self._cta_btn.show()

    def set_create_tooltip(self, text: str):
        self._cta_btn.setToolTip(text)

    def apply_theme_tokens(self):
        self._illustration.setStyleSheet(f"font-size:72px; color:{C['accent']};")


class ContentArea(QWidget):
    new_prompt_req = pyqtSignal()
    search_changed = pyqtSignal(str)
    filter_req = pyqtSignal()
    card_clicked = pyqtSignal(int)
    card_starred = pyqtSignal(int, bool)
    card_edit = pyqtSignal(int)
    card_delete = pyqtSignal(int)
    card_copy = pyqtSignal(int)
    sort_changed = pyqtSignal(str)
    view_changed = pyqtSignal(str)
    header_edit_req = pyqtSignal()
    selection_mode_changed = pyqtSignal(bool)
    ctrl_clicked = pyqtSignal(int)
    bulk_move_req = pyqtSignal(object)
    bulk_copy_req = pyqtSignal(object)
    bulk_tag_req = pyqtSignal(str)
    bulk_delete_req = pyqtSignal()
    bulk_export_req = pyqtSignal()
    card_context_menu = pyqtSignal(int, object)
    area_context_menu = pyqtSignal(object)
    subfolder_clicked = pyqtSignal(int)
    back_clicked = pyqtSignal(object)
    tag_clicked = pyqtSignal(str)
    folder_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sort_cycle: list[tuple[str, str, str]] = [
            ("newest", "↓", "Newest First"),
            ("oldest", "↑", "Oldest First"),
            ("alpha", "A", "A-Z"),
            ("alpha_desc", "Z", "Z-A"),
        ]
        self._view_cycle: list[tuple[str, str, str]] = [
            ("grid", "▦", "Card View"),
            ("list", "≡", "List View"),
        ]
        self._current_sort = "newest"
        self._current_view = "grid"
        self._overflow_hidden: list[QWidget] = []
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._emit_search)
        self._undo_callback = None
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(0)

        self._hdr_col_widget = QWidget()
        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._section_title = QLabel("All Notes")
        self._section_title.setObjectName("ContentHeader")
        self._section_edit_btn = QPushButton("✎")
        self._section_edit_btn.setObjectName("ModalIconBtn")
        self._section_edit_btn.setFixedSize(18, 18)
        self._section_edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._section_edit_btn.clicked.connect(self.header_edit_req.emit)
        self._section_edit_btn.hide()

        title_row.addWidget(self._section_title)
        title_row.addWidget(self._section_edit_btn)
        title_row.addStretch()

        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("ContentSubtitle")
        hdr_col.addLayout(title_row)
        hdr_col.addWidget(self._count_lbl)
        self._hdr_col_widget.setLayout(hdr_col)

        self._hdr_row = QHBoxLayout()
        hdr_row = self._hdr_row
        hdr_row.setSpacing(10)
        hdr_row.addWidget(self._hdr_col_widget)
        hdr_row.addStretch()

        self._select_btn = QPushButton("Select")
        self._select_btn.setObjectName("BtnSecondary")
        self._select_btn.setFixedHeight(36)
        self._select_btn.setCheckable(True)
        self._select_btn.setIcon(make_icon("select.png", C["text_secondary"], 16))
        self._select_btn.setIconSize(QSize(16, 16))
        self._select_btn.toggled.connect(self.selection_mode_changed)

        self._filter_btn = QPushButton("Filters")
        self._filter_btn.setObjectName("FilterBtn")
        self._filter_btn.setFixedHeight(36)
        self._filter_btn.setIcon(make_icon("filter (1).png", C["text_secondary"], 16))
        self._filter_btn.setIconSize(QSize(16, 16))
        self._filter_btn.clicked.connect(self.filter_req.emit)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("SearchBox")
        self._search_input.setPlaceholderText("Search Notes, tags, or content…")
        self._search_input.setFixedHeight(36)
        self._search_input.setMinimumWidth(170)
        self._search_input.setMaximumWidth(260)
        self._search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())

        self._sort_btn = QPushButton()
        self._sort_btn.setObjectName("ModeToggleBtn")
        self._sort_btn.setFixedSize(36, 36)
        self._sort_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._sort_btn.setIconSize(QSize(16, 16))
        self._sort_btn.clicked.connect(self._cycle_sort)
        self._update_sort_button()

        self._view_btn = QPushButton()
        self._view_btn.setObjectName("ModeToggleBtn")
        self._view_btn.setFixedSize(36, 36)
        self._view_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._view_btn.setIconSize(QSize(16, 16))
        self._view_btn.clicked.connect(self._toggle_view)
        self._update_view_button()

        self._overflow_btn = QToolButton()
        self._overflow_btn.setObjectName("HeaderOverflowBtn")
        self._overflow_btn.setText("⋯")
        self._overflow_btn.setFixedSize(36, 36)
        self._overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow_menu = QMenu(self)
        self._overflow_btn.setMenu(self._overflow_menu)
        self._overflow_btn.hide()

        self._select_action = QAction("Select", self)
        self._select_action.setCheckable(True)
        self._select_action.toggled.connect(self._select_btn.setChecked)
        self._select_btn.toggled.connect(self._select_action.setChecked)

        self._filter_action = QAction("Filters", self)
        self._filter_action.triggered.connect(self.filter_req.emit)

        self._sort_menu = QMenu("Sort", self)
        self._sort_group = QActionGroup(self)
        self._sort_group.setExclusive(True)
        self._sort_actions: dict[str, QAction] = {}
        for mode, icon, label in self._sort_cycle:
            act = QAction(f"{icon}  {label}", self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked, m=mode: self._set_sort(m) if checked else None)
            self._sort_group.addAction(act)
            self._sort_menu.addAction(act)
            self._sort_actions[mode] = act

        self._view_menu = QMenu("View", self)
        self._grid_action = QAction("Grid", self)
        self._grid_action.triggered.connect(lambda: self._set_view("grid"))
        self._list_action = QAction("List", self)
        self._list_action.triggered.connect(lambda: self._set_view("list"))
        self._view_menu.addAction(self._grid_action)
        self._view_menu.addAction(self._list_action)

        hdr_row.addWidget(self._select_btn)
        hdr_row.addWidget(self._filter_btn)
        hdr_row.addWidget(self._search_input)
        hdr_row.addWidget(self._sort_btn)
        hdr_row.addWidget(self._view_btn)
        hdr_row.addWidget(self._overflow_btn)
        v.addLayout(hdr_row)
        v.addSpacing(12)

        self._filter_pills_row = QHBoxLayout()
        self._filter_pills_container = QWidget()
        self._filter_pills_container.setLayout(self._filter_pills_row)
        self._filter_pills_container.hide()
        v.addWidget(self._filter_pills_container)
        v.addSpacing(6)

        self._subfolder_strip_wrap = QWidget()
        self._subfolder_strip_wrap.setObjectName("SubfolderStrip")
        subfolder_row = QHBoxLayout(self._subfolder_strip_wrap)
        subfolder_row.setContentsMargins(0, 0, 0, 0)
        subfolder_row.setSpacing(6)

        self._back_btn = QPushButton()
        self._back_btn.setObjectName("BackBtn")
        self._back_btn.setFixedHeight(28)
        self._back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._back_btn.hide()
        subfolder_row.addWidget(self._back_btn)

        self._strip_sep = QFrame()
        self._strip_sep.setFrameShape(QFrame.Shape.VLine)
        self._strip_sep.setObjectName("BulkBarSep")
        self._strip_sep.hide()
        subfolder_row.addWidget(self._strip_sep)

        self._subfolder_label = QLabel("FOLDERS")
        self._subfolder_label.setObjectName("SubfolderLabel")
        subfolder_row.addWidget(self._subfolder_label)
        self._subfolder_chips_wrap = QWidget()
        self._subfolder_chips_layout = FlowLayout(self._subfolder_chips_wrap, h_spacing=6, v_spacing=4)
        self._subfolder_chips_layout.setContentsMargins(0, 0, 0, 0)
        subfolder_row.addWidget(self._subfolder_chips_wrap, 1)
        self._subfolder_strip_wrap.hide()
        v.addWidget(self._subfolder_strip_wrap)
        v.addSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._grid = CardGrid()
        self._grid.card_clicked.connect(self.card_clicked)
        self._grid.card_starred.connect(self.card_starred)
        self._grid.card_edit.connect(self.card_edit)
        self._grid.card_delete.connect(self.card_delete)
        self._grid.card_copy.connect(self.card_copy)
        self._grid.card_context_menu.connect(self.card_context_menu)
        self._grid.area_context_menu.connect(self.area_context_menu)
        self._grid.ctrl_clicked.connect(self.ctrl_clicked)
        self._grid.selection_toggled.connect(self._on_item_selection)
        self._grid.tag_clicked.connect(self.tag_clicked)
        self._grid.folder_clicked.connect(self.folder_clicked)

        self._list_view = ListContainer()
        self._list_view.row_clicked.connect(self.card_clicked)
        self._list_view.row_starred.connect(self.card_starred)
        self._list_view.row_edit.connect(self.card_edit)
        self._list_view.row_delete.connect(self.card_delete)
        self._list_view.row_copy.connect(self.card_copy)
        self._list_view.row_context_menu.connect(self.card_context_menu)
        self._list_view.area_context_menu.connect(self.area_context_menu)
        self._list_view.ctrl_clicked.connect(self.ctrl_clicked)
        self._list_view.selection_toggled.connect(self._on_item_selection)
        self._list_view.tag_clicked.connect(self.tag_clicked)
        self._list_view.folder_clicked.connect(self.folder_clicked)
        self._list_view.hide()

        self._empty = EmptyStateWidget()
        self._empty.hide()
        self._empty.create_req.connect(self.new_prompt_req)

        self._body = QWidget()
        body_v = QVBoxLayout(self._body)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(0)
        body_v.addWidget(self._grid)
        body_v.addWidget(self._list_view)
        body_v.addWidget(self._empty)
        body_v.addStretch()

        self._body.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._body.customContextMenuRequested.connect(
            lambda pos: self.area_context_menu.emit(self._body.mapToGlobal(pos))
        )

        self._scroll.setWidget(self._body)
        v.addWidget(self._scroll, 1)

        self._bulk_bar = QFrame()
        self._bulk_bar.setObjectName("BulkBar")
        self._bulk_bar.hide()
        bulk = QHBoxLayout(self._bulk_bar)
        bulk.setContentsMargins(12, 8, 12, 8)
        bulk.setSpacing(8)

        self._selected_lbl = QLabel("0 selected")

        # ── Folder destination (shared by Move and Copy) ───────────────────
        self._folder_combo = QComboBox()
        self._folder_combo.setObjectName("ModalCombo")
        self._folder_combo.setFixedHeight(34)

        move_btn = QPushButton("Move to")
        move_btn.setObjectName("BtnSecondary")
        move_btn.setToolTip("Move selected notes to the chosen folder")
        move_btn.clicked.connect(lambda: self.bulk_move_req.emit(self._folder_combo.currentData()))

        copy_btn = QPushButton("Copy to")
        copy_btn.setObjectName("BtnSecondary")
        copy_btn.setToolTip("Duplicate selected notes into the chosen folder")
        copy_btn.clicked.connect(lambda: self.bulk_copy_req.emit(self._folder_combo.currentData()))

        # ── Separator ──────────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setObjectName("BulkBarSep")

        # ── Tag input with # suggestions ───────────────────────────────────
        self._tag_input = TagSuggestLineEdit([])
        self._tag_input.setObjectName("ModalInput")
        self._tag_input.setFixedHeight(34)
        self._tag_input.setPlaceholderText("#tag")
        self._tag_input.setMinimumWidth(110)
        self._tag_input.setMaximumWidth(160)

        tag_btn = QPushButton("Add Tag")
        tag_btn.setObjectName("BtnSecondary")
        tag_btn.setToolTip("Add the typed tag to selected notes")
        tag_btn.clicked.connect(self._emit_bulk_tag)

        # ── Separator ──────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setObjectName("BulkBarSep")

        # ── Export button ──────────────────────────────────────────────────
        export_btn = QPushButton("Export…")
        export_btn.setObjectName("BtnSecondary")
        export_btn.setToolTip("Export selected notes to a file or clipboard")
        export_btn.clicked.connect(self.bulk_export_req.emit)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("BtnDanger")
        delete_btn.clicked.connect(self.bulk_delete_req.emit)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("BtnSecondary")
        clear_btn.clicked.connect(self._clear_selection)

        bulk.addWidget(self._selected_lbl)
        bulk.addWidget(self._folder_combo)
        bulk.addWidget(move_btn)
        bulk.addWidget(copy_btn)
        bulk.addWidget(sep1)
        bulk.addWidget(self._tag_input)
        bulk.addWidget(tag_btn)
        bulk.addWidget(sep2)
        bulk.addWidget(export_btn)
        bulk.addWidget(delete_btn)
        bulk.addWidget(clear_btn)
        bulk.addStretch()

        v.addWidget(self._bulk_bar)

        self._toast = QFrame()
        self._toast.setObjectName("ToastBar")
        self._toast.hide()
        toast_row = QHBoxLayout(self._toast)
        toast_row.setContentsMargins(12, 8, 12, 8)
        toast_row.setSpacing(8)
        self._toast_lbl = QLabel("")
        self._toast_undo = QPushButton("Undo")
        self._toast_undo.setObjectName("BtnSecondary")
        self._toast_undo.setFixedHeight(30)
        self._toast_undo.clicked.connect(self._on_undo_clicked)
        toast_row.addWidget(self._toast_lbl)
        toast_row.addStretch()
        toast_row.addWidget(self._toast_undo)

        v.addWidget(self._toast)

        self._fab = QPushButton("＋", self)
        self._fab.setObjectName("FloatingAddBtn")
        self._fab.setFixedSize(56, 56)
        self._fab.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._fab.clicked.connect(self.new_prompt_req.emit)
        self._fab.raise_()
        self._position_fab()
        QTimer.singleShot(0, self._update_header_overflow)

    def set_new_prompt_tooltip(self, text: str):
        self._fab.setToolTip(text)
        self._empty.set_create_tooltip(text)

    def _sync_overflow_menu(self):
        self._overflow_menu.clear()
        hidden_set = set(self._overflow_hidden)

        if self._select_btn in hidden_set:
            self._select_action.setChecked(self._select_btn.isChecked())
            self._overflow_menu.addAction(self._select_action)
        if self._filter_btn in hidden_set:
            self._overflow_menu.addAction(self._filter_action)
        if self._sort_btn in hidden_set:
            if self._current_sort in self._sort_actions:
                self._sort_actions[self._current_sort].setChecked(True)
            self._overflow_menu.addMenu(self._sort_menu)
        if self._view_btn in hidden_set:
            self._overflow_menu.addMenu(self._view_menu)

    def _is_header_overflowing(self) -> bool:
        first_control_x = None
        rightmost_x = 0
        controls = [
            self._select_btn,
            self._filter_btn,
            self._search_input,
            self._sort_btn,
            self._view_btn,
        ]
        if self._overflow_btn.isVisible():
            controls.append(self._overflow_btn)

        for widget in controls:
            if not widget.isVisible():
                continue
            geom = widget.geometry()
            if first_control_x is None or geom.x() < first_control_x:
                first_control_x = geom.x()
            rightmost_x = max(rightmost_x, geom.right())

        if first_control_x is None:
            return False

        info_right = self._hdr_col_widget.geometry().right()
        if first_control_x < info_right + 16:
            return True

        return rightmost_x > self.width() - 12

    def _update_header_overflow(self):
        overflow_order = [
            self._view_btn,
            self._sort_btn,
            self._filter_btn,
            self._select_btn,
        ]

        self._overflow_hidden = []
        for widget in overflow_order:
            widget.show()
        self._overflow_btn.hide()
        self._hdr_row.activate()

        for widget in overflow_order:
            if not self._is_header_overflowing():
                break
            widget.hide()
            self._overflow_hidden.append(widget)
            self._overflow_btn.show()
            self._hdr_row.activate()

        if not self._overflow_hidden:
            self._overflow_btn.hide()
        self._sync_overflow_menu()

    def _emit_search(self):
        self.search_changed.emit(self._search_input.text())

    def _position_fab(self):
        bottom_offset = 20
        if self._bulk_bar.isVisible():
            bottom_offset += self._bulk_bar.height() + 8
        if self._toast.isVisible():
            bottom_offset += self._toast.height() + 8
        x = self.width() - self._fab.width() - 22
        y = self.height() - self._fab.height() - bottom_offset
        self._fab.move(max(0, x), max(0, y))
        self._fab.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_fab"):
            self._position_fab()
        self._update_header_overflow()

    def _on_item_selection(self, _pid: int, _selected: bool):
        pass

    def _clear_selection(self):
        self._select_btn.setChecked(False)

    def _emit_bulk_tag(self):
        raw = self._tag_input.text().strip().lstrip("#")
        if raw:
            self.bulk_tag_req.emit(raw)

    def set_folder_options(self, folders: list[dict]):
        self._folder_combo.clear()
        self._folder_combo.addItem("No folder", None)
        for folder in folders:
            self._folder_combo.addItem(folder["name"], folder["id"])

    def update_tag_completions(self, tags: list[str]):
        """Refresh the tag suggestion list in the bulk bar tag input."""
        self._tag_input.update_known_tags(tags)

    def set_selection_count(self, count: int):
        self._selected_lbl.setText(f"{count} selected")
        self._bulk_bar.setVisible(count > 0)
        self._position_fab()

    def set_selection_mode(self, enabled: bool):
        self._grid.set_selection_mode(enabled)
        self._list_view.set_selection_mode(enabled)
        if not enabled:
            self.set_selection_count(0)

    def set_card_selected(self, prompt_id: int, selected: bool):
        self._grid.set_card_selected(prompt_id, selected)
        self._list_view.set_card_selected(prompt_id, selected)

    def clear_bulk_inputs(self):
        self._tag_input.clear()

    def show_toast(self, message: str, undo_callback=None, timeout_ms: int = 5000):
        self._toast_lbl.setText(message)
        self._undo_callback = undo_callback
        self._toast_undo.setVisible(undo_callback is not None)
        self._toast.show()
        self._toast_timer.start(timeout_ms)
        self._position_fab()

    def _hide_toast(self):
        self._toast.hide()
        self._undo_callback = None
        self._position_fab()

    def _on_undo_clicked(self):
        if self._undo_callback:
            self._undo_callback()
        self._hide_toast()

    def load_folder_nav(self, *, back_key=None, back_label: str = "", subfolders: list[dict]):
        # Back button
        if back_key is not None:
            self._back_btn.setText(f"←  {back_label}")
            self._back_btn.show()
            try:
                self._back_btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._back_btn.clicked.connect(lambda: self.back_clicked.emit(back_key))
        else:
            self._back_btn.hide()

        # Subfolder chips
        while self._subfolder_chips_layout.count():
            item = self._subfolder_chips_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()
        has_chips = bool(subfolders)
        self._subfolder_label.setVisible(has_chips)
        self._subfolder_chips_wrap.setVisible(has_chips)
        if has_chips:
            for folder in subfolders:
                color = folder.get("color") or "#6B7280"
                name = folder["name"]
                fid = folder["id"]
                chip = QPushButton(f"● {name}")
                chip.setObjectName("SubfolderChip")
                chip.setFixedHeight(28)
                chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                chip.setStyleSheet(f"color: {color};")
                chip.clicked.connect(lambda _checked, _id=fid: self.subfolder_clicked.emit(_id))
                self._subfolder_chips_layout.addWidget(chip)

        # Separator between back btn and folder chips
        self._strip_sep.setVisible(back_key is not None and has_chips)

        self._subfolder_strip_wrap.setVisible(back_key is not None or has_chips)

    def load_prompts(
        self,
        prompts: list[dict],
        title: str,
        filter_state: FilterState,
        heading_color: str | None = None,
        tag_colors: dict[str, str | None] | None = None,
        heading_edit_visible: bool = False,
        is_folder_context: bool = False,
    ):
        self._section_title.setText(title)
        self._section_title.setStyleSheet(f"color:{heading_color};" if heading_color else "")
        self._section_edit_btn.setVisible(heading_edit_visible)
        n = len(prompts)
        self._count_lbl.setText(f"{n} prompt{'s' if n != 1 else ''} found")

        self._grid.load(prompts)
        self._list_view.load(prompts)
        self._refresh_pills(filter_state, tag_colors=tag_colors)
        self._update_filter_btn(filter_state.has_filters)

        empty = n == 0
        self._grid.setVisible(not empty and self._current_view == "grid")
        self._list_view.setVisible(not empty and self._current_view == "list")
        self._empty.set_folder_mode(is_folder_context)
        self._empty.setVisible(empty)
        self._update_header_overflow()

    def _refresh_pills(self, fs: FilterState, tag_colors: dict[str, str | None] | None = None):
        while self._filter_pills_row.count():
            item = self._filter_pills_row.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

        if not fs.has_filters:
            self._filter_pills_container.hide()
            return

        if fs.keyword:
            pill = QLabel(f'🔍 "{fs.keyword}"')
            pill.setObjectName("ActiveFilterPill")
            self._filter_pills_row.addWidget(pill)

        for tag in fs.tags:
            pill = QLabel(f"# {tag}")
            pill.setObjectName("ActiveFilterPill")
            tag_color = (tag_colors or {}).get(tag)
            if tag_color:
                pill.setStyleSheet(f"border:1px solid {tag_color}; background:{_hex_to_rgba(tag_color, 48)};")
            self._filter_pills_row.addWidget(pill)

        self._filter_pills_row.addStretch()
        self._filter_pills_container.show()

    def _update_filter_btn(self, active: bool):
        self._filter_btn.setProperty("active", "true" if active else "false")
        style = self._filter_btn.style()
        if style:
            style.unpolish(self._filter_btn)
            style.polish(self._filter_btn)

    def _sort_index(self, mode: str) -> int:
        for idx, (key, _icon, _label) in enumerate(self._sort_cycle):
            if key == mode:
                return idx
        return 0

    def _update_sort_button(self):
        mode = self._current_sort
        idx = self._sort_index(mode)
        _key, sort_char, label = self._sort_cycle[idx]
        self._sort_btn.setIcon(make_icon("sort.png", C["text_secondary"], 16))
        self._sort_btn.setText("")
        self._sort_btn.setToolTip(f"Sort: {label} ({sort_char})")

    def _update_view_button(self):
        is_grid = self._current_view == "grid"
        label = "Card View" if is_grid else "List View"
        self._view_btn.setIcon(make_icon("view.png", C["text_secondary"], 16))
        self._view_btn.setText("")
        self._view_btn.setToolTip(f"View: {label}")

    def _set_sort(self, mode: str):
        if mode not in {m for m, _icon, _label in self._sort_cycle}:
            mode = "newest"
        self._current_sort = mode
        self._update_sort_button()
        if mode in self._sort_actions:
            self._sort_actions[mode].setChecked(True)
        self.sort_changed.emit(mode)

    def _cycle_sort(self):
        idx = self._sort_index(self._current_sort)
        next_idx = (idx + 1) % len(self._sort_cycle)
        next_mode = self._sort_cycle[next_idx][0]
        self._set_sort(next_mode)

    def _toggle_view(self):
        self._set_view("list" if self._current_view == "grid" else "grid")

    def _set_view(self, view: str):
        self._current_view = view
        is_grid = view == "grid"
        self._grid.setVisible(is_grid and not self._empty.isVisible())
        self._list_view.setVisible((not is_grid) and not self._empty.isVisible())
        self._update_view_button()
        self._grid_action.setEnabled(not is_grid)
        self._list_action.setEnabled(is_grid)
        self.view_changed.emit(view)

    def get_visible_prompt_widgets(self) -> list[tuple[int, QWidget]]:
        if self._empty.isVisible():
            return []
        if self._current_view == "grid":
            return [(card.prompt_id(), card) for card in self._grid.get_cards()]
        return [(row.prompt_id(), row) for row in self._list_view.get_rows()]

    def ensure_prompt_visible(self, widget: QWidget | None):
        if widget is None:
            return
        self._scroll.ensureWidgetVisible(widget, 8, 8)

    def apply_theme_tokens(self):
        self._empty.apply_theme_tokens()
        # Refresh toolbar icons for new theme colours
        _ic = C["text_secondary"]
        self._select_btn.setIcon(make_icon("select.png", _ic, 16))
        self._filter_btn.setIcon(make_icon("filter (1).png", _ic, 16))
        self._update_sort_button()
        self._update_view_button()


# ─── Trash UI ─────────────────────────────────────────────────────────────────

class TrashItemRow(QFrame):
    restore_req = pyqtSignal(int)  # trash_id

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._trash_id = int(item["id"])
        self._deleted_at_str = item.get("deleted_at", "")
        self.setObjectName("TrashItemRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(12)

        info_col = QVBoxLayout()
        info_col.setSpacing(3)

        title_lbl = QLabel(item.get("title", "(untitled)"))
        title_lbl.setObjectName("PromptTitle")

        meta_parts: list[str] = []
        folder_name = item.get("folder_name")
        if folder_name:
            meta_parts.append(f"\U0001f4c2 {folder_name}")
        tags = item.get("tags") or []
        if tags:
            meta_parts.append("  ".join(f"#{t}" for t in tags[:5]))
        if meta_parts:
            meta_lbl = QLabel("  ·  ".join(meta_parts))
            meta_lbl.setObjectName("ContentSubtitle")
            info_col.addWidget(meta_lbl)

        self._expires_lbl = QLabel()
        self._expires_lbl.setObjectName("TrashExpiry")
        self._refresh_expiry()

        info_col.addWidget(title_lbl)
        info_col.addWidget(self._expires_lbl)

        restore_btn = QPushButton("Restore")
        restore_btn.setObjectName("BtnSecondary")
        restore_btn.setFixedHeight(32)
        restore_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        restore_btn.clicked.connect(lambda: self.restore_req.emit(self._trash_id))

        h.addLayout(info_col, 1)
        h.addWidget(restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _refresh_expiry(self):
        try:
            deleted_at = datetime.strptime(self._deleted_at_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            self._expires_lbl.setText("Will be deleted soon")
            return
        expiry = deleted_at + timedelta(hours=48)
        # deleted_at is stored in UTC; compare against UTC now so the countdown
        # is correct regardless of the user's local timezone.
        remaining = expiry - datetime.now(timezone.utc).replace(tzinfo=None)
        total_secs = remaining.total_seconds()
        if total_secs <= 0:
            self._expires_lbl.setText("Expired — will be removed shortly")
            self._expires_lbl.setStyleSheet(f"color:{C['danger']};")
        else:
            hours = int(total_secs // 3600)
            mins = int((total_secs % 3600) // 60)
            if hours > 0:
                text = f"Expires in {hours}h {mins}m"
            elif mins > 0:
                text = f"Expires in {mins}m"
            else:
                text = "Expires in less than a minute"
            self._expires_lbl.setText(text)
            self._expires_lbl.setStyleSheet(
                f"color:{C['danger']};" if hours < 2 else f"color:{C['text_muted']};"
            )

    def refresh_expiry(self):
        self._refresh_expiry()


class TrashView(QWidget):
    restore_req = pyqtSignal(int)    # trash_id
    restore_all_req = pyqtSignal()
    clear_trash_req = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[TrashItemRow] = []
        self._build()

        self._expiry_tick = QTimer(self)
        self._expiry_tick.setInterval(60_000)
        self._expiry_tick.timeout.connect(self._refresh_expiry_labels)
        self._expiry_tick.start()

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)

        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(2)
        self._title_lbl = QLabel("Trash")
        self._title_lbl.setObjectName("ContentHeader")
        self._subtitle_lbl = QLabel("Items are permanently deleted after 48 hours")
        self._subtitle_lbl.setObjectName("ContentSubtitle")
        hdr_col.addWidget(self._title_lbl)
        hdr_col.addWidget(self._subtitle_lbl)

        self._restore_all_btn = QPushButton("Restore All")
        self._restore_all_btn.setObjectName("BtnSecondary")
        self._restore_all_btn.setFixedHeight(36)
        self._restore_all_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._restore_all_btn.clicked.connect(self.restore_all_req.emit)

        self._clear_btn = QPushButton("Clear Trash")
        self._clear_btn.setObjectName("BtnDanger")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._clear_btn.clicked.connect(self.clear_trash_req.emit)

        hdr_row.addLayout(hdr_col, 1)
        hdr_row.addWidget(self._restore_all_btn)
        hdr_row.addWidget(self._clear_btn)
        v.addLayout(hdr_row)
        v.addSpacing(16)

        # ── Item list ─────────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._items_widget = QWidget()
        self._items_layout = QVBoxLayout(self._items_widget)
        self._items_layout.setSpacing(8)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.addStretch()

        self._scroll.setWidget(self._items_widget)
        v.addWidget(self._scroll, 1)

        # ── Empty state ───────────────────────────────────────────────────────
        self._empty_lbl = QLabel("Trash is empty")
        self._empty_lbl.setObjectName("ContentSubtitle")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.hide()
        v.addWidget(self._empty_lbl)

        # ── Toast ─────────────────────────────────────────────────────────────
        self._toast = QFrame()
        self._toast.setObjectName("ToastBar")
        self._toast.hide()
        toast_row = QHBoxLayout(self._toast)
        toast_row.setContentsMargins(12, 8, 12, 8)
        self._toast_lbl = QLabel("")
        toast_row.addWidget(self._toast_lbl)
        toast_row.addStretch()
        v.addWidget(self._toast)

    def load(self, items: list[dict]):
        while self._items_layout.count() > 1:
            it = self._items_layout.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.deleteLater()
        self._rows.clear()

        has_items = bool(items)
        self._scroll.setVisible(has_items)
        self._empty_lbl.setVisible(not has_items)
        self._restore_all_btn.setEnabled(has_items)
        self._clear_btn.setEnabled(has_items)

        if not has_items:
            self._subtitle_lbl.setText("Trash is empty")
            return

        n = len(items)
        self._subtitle_lbl.setText(
            f"{n} item{'s' if n != 1 else ''} · Permanently deleted after 48 hours"
        )

        for item in items:
            row = TrashItemRow(item)
            row.restore_req.connect(self.restore_req.emit)
            self._items_layout.insertWidget(self._items_layout.count() - 1, row)
            self._rows.append(row)

    def _refresh_expiry_labels(self):
        for row in self._rows:
            row.refresh_expiry()

    def show_toast(self, msg: str):
        self._toast_lbl.setText(msg)
        self._toast.show()
        self._toast_timer.start(4000)

    def _hide_toast(self):
        self._toast.hide()

    def apply_theme_tokens(self):
        pass


class _TagInputDialog(QDialog):
    """Small popup dialog to enter a single tag with auto-complete."""

    def __init__(self, all_tags: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Tag")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setMinimumWidth(280)
        self._tag: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        lbl = QLabel("Add tag to note:")
        lbl.setObjectName("ContentSubtitle")
        layout.addWidget(lbl)

        self._input = TagSuggestLineEdit(all_tags, self)
        self._input.setObjectName("ModalInput")
        self._input.setPlaceholderText("Type #tagname…")
        self._input.setFixedHeight(34)
        self._input.returnPressed.connect(self._on_accept)
        layout.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("BtnSecondary")
        cancel_btn.setFixedHeight(32)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Add Tag")
        ok_btn.setObjectName("BtnPrimary")
        ok_btn.setFixedHeight(32)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_accept(self):
        text = self._input.text().strip().lstrip("#").lower()
        if text:
            self._tag = text
            self.accept()

    def get_tag(self) -> str | None:
        return self._tag


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._settings = QSettings()
        self._theme = normalize_theme(self._settings.value("ui/theme", "dark", type=str))
        set_theme(self._theme)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(make_stylesheet(self._theme))
        self.setWindowTitle("NoteStack")
        self.resize(1280, 820)
        self.setMinimumSize(900, 600)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self._fs = FilterState()
        self._selected_ids: set[int] = set()
        self._kbd_selected_id: int | None = None
        self._current_prompts: list[dict] = []
        self._header_edit_target: tuple[str, int] | None = None
        self._undo_folder_payload: dict | None = None
        self._undo_tag_payload: dict | None = None
        self._note_clipboard: dict | None = None
        self._last_used_prompt_id: int | None = None
        self._sidebar_selected_tag: str | None = None
        self._tray: QSystemTrayIcon | None = None
        self._open_edit_windows: dict[int, NewPromptModal] = {}
        self._open_new_windows: list[NewPromptModal] = []
        self._open_detail_windows: list[PromptDetailModal] = []
        self._resize_margin = 6
        self._resize_edges = 0
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = self.geometry()

        self._build()
        self._restore_ui_settings()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self._save_ui_settings)
        self._setup_tray()

        # Purge expired trash items on startup, then every 5 minutes while running
        db.purge_expired_trash()
        self._trash_purge_timer = QTimer(self)
        self._trash_purge_timer.setInterval(5 * 60 * 1000)
        self._trash_purge_timer.timeout.connect(self._on_trash_purge_tick)
        self._trash_purge_timer.start()

        self._refresh_sidebar()
        self._reload_prompts()
        self._sidebar.set_active("all")

    _EDGE_LEFT = 1
    _EDGE_TOP = 2
    _EDGE_RIGHT = 4
    _EDGE_BOTTOM = 8

    def _contains_obj(self, obj) -> bool:
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _hit_test_edges(self, global_pos: QPoint) -> int:
        if self.isMaximized() or not self.isVisible():
            return 0

        top_left = self.mapToGlobal(QPoint(0, 0))
        x = global_pos.x() - top_left.x()
        y = global_pos.y() - top_left.y()
        w = self.width()
        h = self.height()

        if x < 0 or y < 0 or x > w or y > h:
            return 0

        m = self._resize_margin
        edges = 0
        if x <= m:
            edges |= self._EDGE_LEFT
        elif x >= w - m:
            edges |= self._EDGE_RIGHT

        if y <= m:
            edges |= self._EDGE_TOP
        elif y >= h - m:
            edges |= self._EDGE_BOTTOM
        return edges

    def _cursor_for_edges(self, edges: int):
        if edges in (self._EDGE_TOP | self._EDGE_LEFT, self._EDGE_BOTTOM | self._EDGE_RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (self._EDGE_TOP | self._EDGE_RIGHT, self._EDGE_BOTTOM | self._EDGE_LEFT):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (self._EDGE_LEFT, self._EDGE_RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edges in (self._EDGE_TOP, self._EDGE_BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return None

    def _update_resize_cursor(self, global_pos: QPoint):
        if self._resizing:
            return
        edges = self._hit_test_edges(global_pos)
        cursor = self._cursor_for_edges(edges)
        if cursor is None:
            self.unsetCursor()
            self._resize_edges = 0
            return
        self.setCursor(cursor)
        self._resize_edges = edges

    def _apply_resize(self, global_pos: QPoint):
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()

        start = self._resize_start_geo
        x = start.x()
        y = start.y()
        w = start.width()
        h = start.height()

        min_w = self.minimumWidth()
        min_h = self.minimumHeight()

        if self._resize_edges & self._EDGE_LEFT:
            new_x = x + dx
            new_w = w - dx
            if new_w < min_w:
                new_x = x + (w - min_w)
                new_w = min_w
            x, w = new_x, new_w
        if self._resize_edges & self._EDGE_RIGHT:
            w = max(min_w, w + dx)

        if self._resize_edges & self._EDGE_TOP:
            new_y = y + dy
            new_h = h - dy
            if new_h < min_h:
                new_y = y + (h - min_h)
                new_h = min_h
            y, h = new_y, new_h
        if self._resize_edges & self._EDGE_BOTTOM:
            h = max(min_h, h + dy)

        self.setGeometry(x, y, w, h)

    def eventFilter(self, obj, event):
        if not self._contains_obj(obj):
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QEvent.Type.MouseMove:
            global_pos = event.globalPosition().toPoint()
            if self._resizing:
                self._apply_resize(global_pos)
                return True
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._update_resize_cursor(global_pos)
        elif et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
                global_pos = event.globalPosition().toPoint()
                edges = self._hit_test_edges(global_pos)
                if edges:
                    self._resize_edges = edges
                    self._resizing = True
                    self._resize_start_pos = global_pos
                    self._resize_start_geo = self.geometry()
                    return True
        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self._resizing:
                self._resizing = False
                self._update_resize_cursor(event.globalPosition().toPoint())
                return True

        return super().eventFilter(obj, event)

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_v = QVBoxLayout(root)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        self._window_chrome = WindowChromeBar(self)
        main_v.addWidget(self._window_chrome)

        self._body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._body_splitter.setChildrenCollapsible(False)
        self._body_splitter.setHandleWidth(8)
        self._body_splitter.setOpaqueResize(True)

        self._sidebar = Sidebar()
        self._sidebar.setMinimumWidth(SIDEBAR_MIN_W)
        self._sidebar.nav_changed.connect(self._on_nav)
        self._sidebar.create_folder_req.connect(self._open_create_folder_modal)
        self._sidebar.create_note_in_folder_req.connect(self._open_new_prompt_in_folder)
        self._sidebar.edit_folder_req.connect(self._open_folder_quick_edit)
        self._sidebar.delete_folder_req.connect(self._delete_folder_with_undo)
        self._sidebar.folder_parent_change_req.connect(self._on_folder_parent_change)
        self._sidebar.folder_move_invalid.connect(self._on_folder_move_invalid)
        self._sidebar.manage_tags_req.connect(self._open_tag_mgr)
        self._sidebar.tag_filter_changed.connect(self._on_sidebar_tag_filter)
        self._sidebar.recent_prompt_req.connect(self._open_recent)
        self._sidebar.settings_req.connect(self._open_settings)
        self._body_splitter.addWidget(self._sidebar)

        self._content = ContentArea()
        self._content.search_changed.connect(self._on_search)
        self._content.filter_req.connect(self._open_advanced_search)
        self._content.new_prompt_req.connect(self._open_new_prompt)
        self._content.card_clicked.connect(self._open_detail)
        self._content.card_starred.connect(self._on_star)
        self._content.card_edit.connect(self._open_edit)
        self._content.card_delete.connect(self._delete_single)
        self._content.card_copy.connect(self._copy_content)
        self._content.card_context_menu.connect(self._on_card_context_menu)
        self._content.area_context_menu.connect(self._on_area_context_menu)
        self._content.sort_changed.connect(self._on_sort)
        self._content.view_changed.connect(self._on_view_changed)
        self._content.header_edit_req.connect(self._open_active_scope_editor)
        self._content.selection_mode_changed.connect(self._on_selection_mode_changed)
        self._content.bulk_move_req.connect(self._bulk_move)
        self._content.bulk_copy_req.connect(self._bulk_copy)
        self._content.bulk_tag_req.connect(self._bulk_add_tag)
        self._content.bulk_delete_req.connect(self._bulk_delete)
        self._content.bulk_export_req.connect(self._bulk_export)
        self._content.subfolder_clicked.connect(self._on_nav)
        self._content.back_clicked.connect(self._on_nav)
        self._content.tag_clicked.connect(self._on_card_tag_clicked)
        self._content.folder_clicked.connect(self._on_card_folder_clicked)
        self._content._grid.selection_toggled.connect(self._on_selection_toggled)
        self._content._list_view.selection_toggled.connect(self._on_selection_toggled)
        self._content.ctrl_clicked.connect(self._on_ctrl_click)

        self._trash_view = TrashView()
        self._trash_view.restore_req.connect(self._restore_from_trash)
        self._trash_view.restore_all_req.connect(self._restore_all_from_trash)
        self._trash_view.clear_trash_req.connect(self._clear_trash)

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._content)    # index 0: normal content
        self._content_stack.addWidget(self._trash_view) # index 1: trash view

        self._body_splitter.addWidget(self._content_stack)
        self._body_splitter.setStretchFactor(0, 0)
        self._body_splitter.setStretchFactor(1, 1)
        self._body_splitter.setSizes([SIDEBAR_W, max(1, self.width() - SIDEBAR_W)])
        main_v.addWidget(self._body_splitter, 1)

        self._new_prompt_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self._new_prompt_shortcut.activated.connect(self._open_new_prompt)
        new_prompt_key = self._new_prompt_shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
        self._content.set_new_prompt_tooltip(f"New Note ({new_prompt_key})")

        self._paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        self._paste_shortcut.activated.connect(self._handle_paste_shortcut)

    def _restore_ui_settings(self):
        saved_window_size = self._settings.value("ui/window_size", QSize(1280, 820), type=QSize)
        if isinstance(saved_window_size, QSize) and saved_window_size.isValid():
            width = max(self.minimumWidth(), saved_window_size.width())
            height = max(self.minimumHeight(), saved_window_size.height())
            self.resize(width, height)

        saved_sidebar_width = self._settings.value("ui/sidebar_width", SIDEBAR_W, type=int)
        if isinstance(saved_sidebar_width, int):
            sidebar_width = max(SIDEBAR_MIN_W, saved_sidebar_width)
            content_width = max(1, self.width() - sidebar_width)
            self._body_splitter.setSizes([sidebar_width, content_width])

    def _save_ui_settings(self):
        if not self.isMaximized():
            self._settings.setValue("ui/window_size", self.size())

        self._settings.setValue("ui/theme", self._theme)

        if hasattr(self, "_body_splitter"):
            sizes = self._body_splitter.sizes()
            if sizes:
                self._settings.setValue("ui/sidebar_width", max(SIDEBAR_MIN_W, int(sizes[0])))

        self._settings.sync()

    def _apply_theme(self, theme: str, persist: bool = True):
        self._theme = set_theme(theme)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(make_stylesheet(self._theme))

        if hasattr(self, "_sidebar"):
            self._sidebar.apply_theme_tokens()
        if hasattr(self, "_content"):
            self._content.apply_theme_tokens()
        if hasattr(self, "_trash_view"):
            self._trash_view.apply_theme_tokens()

        if persist:
            self._settings.setValue("ui/theme", self._theme)
            self._settings.sync()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return

        icon = self.windowIcon()

        _res_root = resources_dir()
        logo_path = _res_root / "project_logo.png"
        tray_icon_path = _res_root / "tray_icon.png"

        if icon.isNull() and logo_path.exists():
            icon = make_png_icon(logo_path)

        if icon.isNull():
            pix = QPixmap(32, 32)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C["accent"]))
            painter.drawRoundedRect(0, 0, 32, 32, 7, 7)
            painter.setPen(QColor("white"))
            painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "✦")
            painter.end()
            icon = QIcon(pix)

        self.setWindowIcon(icon)
        if hasattr(self, "_window_chrome"):
            self._window_chrome.update_window_icon()
            self._window_chrome.update_window_title(self.windowTitle())

        tray_icon = QIcon()
        if tray_icon_path.exists():
            tray_icon = make_png_icon(tray_icon_path, sizes=(16, 20, 24, 32, 40, 48))
        elif logo_path.exists():
            tray_icon = make_png_icon(logo_path, sizes=(16, 20, 24, 32, 40, 48))

        if tray_icon.isNull():
            tray_icon = icon

        self._tray = QSystemTrayIcon(tray_icon, self)
        menu = QMenu(self)

        open_action = QAction("Open NoteStack", self)
        open_action.triggered.connect(self._restore_from_tray)

        copy_action = QAction("Quick Copy Last Used", self)
        copy_action.triggered.connect(self._quick_copy_last_used)

        # ── Troubleshoot submenu ──────────────────────────────────────────────
        troubleshoot_menu = QMenu("Troubleshoot", self)

        reset_windows_action = QAction("Reset Window Positions", self)
        reset_windows_action.triggered.connect(self._troubleshoot_reset_window_positions)

        reset_settings_action = QAction("Reset All UI Settings", self)
        reset_settings_action.triggered.connect(self._troubleshoot_reset_all_settings)

        add_sample_action = QAction("Add Sample Data", self)
        add_sample_action.triggered.connect(self._troubleshoot_add_sample_data)

        remove_sample_action = QAction("Remove Sample Data", self)
        remove_sample_action.triggered.connect(self._troubleshoot_remove_sample_data)

        open_data_folder_action = QAction("Open App Data Folder", self)
        open_data_folder_action.triggered.connect(self._troubleshoot_open_data_folder)

        troubleshoot_menu.addAction(reset_windows_action)
        troubleshoot_menu.addAction(reset_settings_action)
        troubleshoot_menu.addSeparator()
        troubleshoot_menu.addAction(add_sample_action)
        troubleshoot_menu.addAction(remove_sample_action)
        troubleshoot_menu.addSeparator()
        troubleshoot_menu.addAction(open_data_folder_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(lambda: QApplication.quit())

        menu.addAction(open_action)
        menu.addAction(copy_action)
        menu.addSeparator()
        menu.addMenu(troubleshoot_menu)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _can_minimize_to_tray(self) -> bool:
        return self._tray is not None and self._tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _restore_from_tray(self):
        self.bring_to_front()

    def _quick_copy_last_used(self):
        target = self._last_used_prompt_id
        if target is None:
            recents = db.get_recent_prompts(1)
            target = recents[0]["id"] if recents else None
        if target is None:
            return
        prompt = db.get_prompt(target)
        if not prompt:
            return
        self._set_clipboard_content(prompt["content"])
        db.touch_prompt(target)
        self._refresh_sidebar()
        self._content.show_toast("Copied last-used prompt.")

    def _set_clipboard_content(self, content: str):
        clipboard = QApplication.clipboard()
        if not clipboard:
            return
        if Qt.mightBeRichText(content):
            doc = QTextDocument()
            doc.setHtml(content)
            mime = QMimeData()
            mime.setHtml(content)
            mime.setText(doc.toPlainText())
            clipboard.setMimeData(mime)
        else:
            clipboard.setText(content)

    # ── Troubleshoot actions ──────────────────────────────────────────────────

    def _troubleshoot_reset_window_positions(self):
        reply = QMessageBox.question(
            self,
            "Reset Window Positions",
            "This will reset all saved window positions and sizes to defaults.\n\n"
            "Your notes and data are not affected. Reopen any windows to see the effect.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        for key in [
            "ui/window_size",
            "ui/modals/prompt_edit/fullscreen",
            "ui/modals/prompt_edit/size",
            "ui/modals/prompt_edit/pos",
            "ui/modals/prompt_detail/fullscreen",
            "ui/modals/prompt_detail/size",
            "ui/modals/prompt_detail/pos",
        ]:
            self._settings.remove(key)
        self._settings.sync()
        if self._tray:
            self._tray.showMessage(
                "NoteStack",
                "Window positions reset. Reopen windows to apply.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _troubleshoot_reset_all_settings(self):
        reply = QMessageBox.question(
            self,
            "Reset All UI Settings",
            "This will reset all UI settings (theme, window sizes, positions) to defaults.\n\n"
            "Your notes and data are not affected.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        self._settings.clear()
        self._settings.sync()
        if self._tray:
            self._tray.showMessage(
                "NoteStack",
                "All settings reset to defaults.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _troubleshoot_add_sample_data(self):
        count = len(db_seed.SAMPLE_DATA)
        reply = QMessageBox.question(
            self,
            "Add Sample Data",
            f"This will add {count} sample notes to your library.\n\n"
            "Your existing notes will not be affected.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        db_seed.seed_force()
        self._refresh_sidebar()
        self._reload_prompts()
        if self._tray:
            self._tray.showMessage(
                "NoteStack", f"Added {count} sample notes.", QSystemTrayIcon.MessageIcon.Information, 2500
            )

    def _troubleshoot_remove_sample_data(self):
        count = len(db_seed.SAMPLE_DATA)
        reply = QMessageBox.question(
            self,
            "Remove Sample Data",
            f"This will delete notes matching the {count} sample note titles.\n\n"
            "Only sample notes will be removed; your own notes are safe.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return
        removed = db_seed.remove_sample_data()
        self._refresh_sidebar()
        self._reload_prompts()
        if self._tray:
            self._tray.showMessage(
                "NoteStack",
                f"Removed {removed} sample note(s).",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def _troubleshoot_open_data_folder(self):
        import os
        import subprocess
        folder = db.USER_DATA_DIR
        if os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])

    def closeEvent(self, event):
        if self._can_minimize_to_tray():
            event.ignore()
            self.hide()
            self._tray.showMessage("NoteStack", "Still running in the system tray.", QSystemTrayIcon.MessageIcon.Information, 2500)
            return
        self._save_ui_settings()
        super().closeEvent(event)

    def changeEvent(self, event):
        if hasattr(self, "_window_chrome"):
            if event.type() == QEvent.Type.WindowStateChange:
                self._window_chrome.sync_maximize_state()
                if self.isMaximized():
                    self.unsetCursor()
            elif event.type() == QEvent.Type.WindowTitleChange:
                self._window_chrome.update_window_title(self.windowTitle())
            elif event.type() == QEvent.Type.WindowIconChange:
                self._window_chrome.update_window_icon()
        super().changeEvent(event)

    def _reload_prompts(self):
        if self._fs.section == "trash":
            return
        prompts = db.get_prompts(**self._fs.to_query())
        self._current_prompts = prompts

        all_tags = db.get_all_tags()
        tag_color_by_name = {t["name"]: t.get("color") for t in all_tags}
        tag_id_by_name = {t["name"]: t["id"] for t in all_tags}
        active_tag = self._fs.tags[0] if len(self._fs.tags) == 1 else None
        heading_color = tag_color_by_name.get(active_tag) if active_tag else None

        section = self._fs.section
        self._header_edit_target = None
        back_key = None
        back_label = ""
        child_subfolders: list[dict] = []
        if active_tag:
            title = f"#{active_tag}"
            tag_id = tag_id_by_name.get(active_tag)
            if isinstance(tag_id, int):
                self._header_edit_target = ("tag", tag_id)
        elif section == "all":
            title = "All Notes"
        elif section == "favorites":
            title = "Favorites"
        else:
            folders = db.get_all_folders()
            folder_by_id = {f["id"]: f for f in folders}
            current_folder = folder_by_id.get(section)
            name = current_folder["name"] if current_folder else "Folder"
            title = name
            if isinstance(section, int):
                self._header_edit_target = ("folder", section)
                child_subfolders = sorted(
                    [f for f in folders if f.get("parent_id") == section],
                    key=lambda f: f["name"].lower(),
                )
                parent_id = current_folder.get("parent_id") if current_folder else None
                if parent_id is not None:
                    parent_folder = folder_by_id.get(parent_id)
                    back_key = parent_id
                    back_label = parent_folder["name"] if parent_folder else "Back"
                else:
                    back_key = "all"
                    back_label = "All Notes"

                # Cards in subfolder context: use the browsed folder's color so
                # all cards in this view share a consistent color identity.
                ctx_color = current_folder.get("color") if current_folder else None
                if ctx_color:
                    for p in prompts:
                        p["folder_color"] = ctx_color

        self._content.load_folder_nav(back_key=back_key, back_label=back_label, subfolders=child_subfolders)
        self._content.load_prompts(
            prompts,
            title,
            self._fs,
            heading_color=heading_color,
            tag_colors=tag_color_by_name,
            heading_edit_visible=self._header_edit_target is not None,
            is_folder_context=isinstance(self._fs.section, int),
        )
        self._content.set_folder_options(db.get_all_folders())
        self._content.update_tag_completions([t["name"] for t in all_tags])

        visible_ids = {p["id"] for p in prompts}
        self._selected_ids = {pid for pid in self._selected_ids if pid in visible_ids}
        self._content.set_selection_count(len(self._selected_ids))

        if self._kbd_selected_id not in visible_ids:
            self._kbd_selected_id = None
        self._sync_kbd_selection()

        self._sidebar.set_active_tag(active_tag)

    def _open_active_scope_editor(self):
        if not self._header_edit_target:
            return
        kind, item_id = self._header_edit_target
        if kind == "folder":
            self._open_folder_quick_edit(item_id)
        elif kind == "tag":
            self._open_tag_quick_edit(item_id)

    def _refresh_sidebar(self):
        self._sidebar.refresh_recent(db.get_recent_prompts(8))
        self._sidebar.refresh_folders(db.get_all_folders_tree())
        self._sidebar.refresh_tags(db.get_all_tags())
        self._sidebar.refresh_trash_count(db.get_trash_count())

    def _on_nav(self, key):
        if key == "trash":
            self._fs.section = "trash"
            self._fs.keyword = ""
            self._fs.tags = []
            self._sidebar_selected_tag = None
            self._content_stack.setCurrentIndex(1)
            self._load_trash_view()
            return

        self._fs.section = key
        self._fs.keyword = ""
        self._fs.tags = []
        self._sidebar_selected_tag = None
        self._content_stack.setCurrentIndex(0)
        self._sidebar.set_active(key)
        self._reload_prompts()

    def _load_trash_view(self, *, purge: bool = True):
        if purge:
            db.purge_expired_trash()
        items = db.get_trash_items()
        self._trash_view.load(items)
        self._sidebar.refresh_trash_count(len(items))

    def _restore_from_trash(self, trash_id: int):
        db.restore_from_trash(trash_id)
        self._load_trash_view()
        self._trash_view.show_toast("Note restored.")

    def _restore_all_from_trash(self):
        count = db.restore_all_from_trash()
        self._load_trash_view()
        self._refresh_sidebar()
        self._trash_view.show_toast(
            f"Restored {count} note{'s' if count != 1 else ''}."
        )

    def _clear_trash(self):
        from PyQt6.QtWidgets import QMessageBox
        items = db.get_trash_items()
        if not items:
            return
        n = len(items)
        reply = QMessageBox.question(
            self,
            "Clear Trash",
            f"Permanently delete {n} item{'s' if n != 1 else ''} from trash? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.clear_trash()
            self._load_trash_view()
            self._refresh_sidebar()

    def _on_trash_purge_tick(self):
        db.purge_expired_trash()
        if self._fs.section == "trash":
            # purge already done above — skip the second one inside _load_trash_view
            self._load_trash_view(purge=False)
        else:
            self._sidebar.refresh_trash_count(db.get_trash_count())

    def _on_search(self, text: str):
        self._fs.keyword = text
        self._reload_prompts()

    def _on_sidebar_tag_filter(self, tag_name: str | None):
        self._sidebar_selected_tag = tag_name
        self._fs.tags = [tag_name] if tag_name else []
        if self._fs.section == "trash":
            self._fs.section = "all"
            self._content_stack.setCurrentIndex(0)
        self._reload_prompts()

    def _on_card_tag_clicked(self, tag_name: str):
        """Navigate to all notes filtered by the clicked tag."""
        self._fs.section = "all"
        self._fs.keyword = ""
        self._fs.tags = [tag_name]
        self._sidebar_selected_tag = tag_name
        self._content_stack.setCurrentIndex(0)
        self._sidebar.set_active("all")
        self._sidebar.set_active_tag(tag_name)
        self._reload_prompts()

    def _on_card_folder_clicked(self, folder_id: int):
        """Navigate to the folder shown on the clicked card."""
        self._on_nav(folder_id)
        self._sidebar.set_active_tag(None)

    def _on_sort(self, sort: str):
        self._fs.sort = sort
        self._reload_prompts()

    def _on_view_changed(self, _view: str):
        self._sync_kbd_selection()

    def _open_recent(self, prompt_id: int):
        self._open_detail(prompt_id)

    def _open_advanced_search(self):
        all_tags = db.get_all_tags()
        dlg = AdvancedSearchModal(
            all_tags=all_tags,
            current_keyword=self._fs.keyword,
            current_tags=self._fs.tags,
            parent=self,
        )
        dlg.filters_applied.connect(self._apply_filters)
        dlg.exec()

    def _apply_filters(self, keyword: str, tags: list[str]):
        self._sidebar_selected_tag = None
        self._fs.keyword = keyword
        self._fs.tags = tags
        self._reload_prompts()

    def _get_new_prompt_defaults(self) -> tuple[int | None, list[str] | None]:
        if self._fs.keyword:
            return None, None

        if self._sidebar_selected_tag:
            return None, [self._sidebar_selected_tag]

        if self._fs.tags:
            return None, None

        if isinstance(self._fs.section, int):
            return self._fs.section, None

        return None, None

    def _open_new_prompt(self):
        folders = db.get_all_folders()
        all_tags = [tag["name"] for tag in db.get_all_tags()]
        default_folder_id, default_tags = self._get_new_prompt_defaults()
        dlg = NewPromptModal(
            folders=folders,
            all_tags=all_tags,
            default_folder_id=default_folder_id,
            default_tags=default_tags,
            as_window=True,
            parent=None,
        )
        dlg.saved.connect(self._on_prompt_saved)
        dlg.finished.connect(lambda _result, window=dlg: self._on_new_prompt_window_closed(window))
        self._open_new_windows.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _open_new_prompt_in_folder(self, folder_id: int):
        folders = db.get_all_folders()
        all_tags = [tag["name"] for tag in db.get_all_tags()]
        dlg = NewPromptModal(
            folders=folders,
            all_tags=all_tags,
            default_folder_id=folder_id,
            default_tags=None,
            as_window=True,
            parent=None,
        )
        dlg.saved.connect(self._on_prompt_saved)
        dlg.finished.connect(lambda _result, window=dlg: self._on_new_prompt_window_closed(window))
        self._open_new_windows.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _folder_depth_map(self, folders: list[dict]) -> dict[int, int]:
        parent_by_id = {f["id"]: f.get("parent_id") for f in folders}
        cache: dict[int, int] = {}

        def _depth(folder_id: int, path: set[int]) -> int:
            if folder_id in cache:
                return cache[folder_id]
            if folder_id in path:
                return 1
            path.add(folder_id)
            parent_id = parent_by_id.get(folder_id)
            if parent_id is None or parent_id not in parent_by_id:
                value = 1
            else:
                value = 1 + _depth(parent_id, path)
            path.remove(folder_id)
            cache[folder_id] = value
            return value

        for folder in folders:
            _depth(folder["id"], set())
        return cache

    def _is_descendant_folder(self, folder_id: int, ancestor_id: int, parent_by_id: dict[int, int | None]) -> bool:
        node = parent_by_id.get(folder_id)
        while node is not None:
            if node == ancestor_id:
                return True
            node = parent_by_id.get(node)
        return False

    def _max_subtree_height(self, root_id: int, children_by_parent: dict[int | None, list[int]]) -> int:
        children = children_by_parent.get(root_id, [])
        if not children:
            return 1
        return 1 + max(self._max_subtree_height(child_id, children_by_parent) for child_id in children)

    def _validate_reparent(
        self,
        folder_id: int,
        new_parent_id: int | None,
        folders: list[dict],
        max_depth: int = 20,
    ) -> tuple[bool, str]:
        parent_by_id = {f["id"]: f.get("parent_id") for f in folders}
        if folder_id not in parent_by_id:
            return False, "Folder no longer exists."

        if new_parent_id == folder_id:
            return False, "Cannot move a folder into itself."

        if new_parent_id is not None and self._is_descendant_folder(new_parent_id, folder_id, parent_by_id):
            return False, "Cannot move a folder into its own descendant."

        depth_map = self._folder_depth_map(folders)
        parent_depth = depth_map.get(new_parent_id, 0) if new_parent_id is not None else 0

        children_by_parent: dict[int | None, list[int]] = {}
        for f in folders:
            children_by_parent.setdefault(f.get("parent_id"), []).append(f["id"])
        subtree_height = self._max_subtree_height(folder_id, children_by_parent)
        if parent_depth + subtree_height > max_depth:
            return False, f"Folder nesting is limited to {max_depth} levels."
        return True, ""

    def _open_create_folder_modal(self, parent_id: int | None):
        folders = db.get_all_folders_tree()
        folder_by_id = {f["id"]: f for f in folders}
        if parent_id is not None and parent_id not in folder_by_id:
            self._content.show_toast("Selected parent folder was not found.")
            self._refresh_sidebar()
            return

        if parent_id is None:
            parent_label = "Root"
        else:
            parent_label = folder_by_id[parent_id]["name"]

        dlg = CreateFolderModal(parent_label=parent_label, parent=self)

        def _create(name: str, color: str | None):
            if any(f["name"].strip().lower() == name.strip().lower() for f in folders):
                self._content.show_toast("A folder with this name already exists.")
                return

            if parent_id is not None:
                depth_map = self._folder_depth_map(folders)
                if depth_map.get(parent_id, 1) >= 20:
                    self._content.show_toast("Folder nesting is limited to 20 levels.")
                    return

            db.create_folder(name, parent_id, color)
            self._refresh_sidebar()
            self._reload_prompts()
            self._content.show_toast(f"Created folder \"{name}\".")

        dlg.created.connect(_create)
        dlg.exec()

    def _on_folder_parent_change(self, folder_id: int, new_parent_id: int | None):
        folders = db.get_all_folders_tree()
        is_valid, message = self._validate_reparent(folder_id, new_parent_id, folders)
        if not is_valid:
            self._refresh_sidebar()
            self._content.show_toast(message)
            return

        db.set_folder_parent(folder_id, new_parent_id)
        self._refresh_sidebar()
        self._reload_prompts()

    def _on_folder_move_invalid(self, message: str):
        self._content.show_toast(message)

    def _on_new_prompt_window_closed(self, window: NewPromptModal):
        if window in self._open_new_windows:
            self._open_new_windows.remove(window)

    def _open_edit(self, prompt_id: int):
        existing = self._open_edit_windows.get(prompt_id)
        if existing and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        if existing is not None:
            self._open_edit_windows.pop(prompt_id, None)

        data = db.get_prompt(prompt_id)
        if not data:
            return
        folders = db.get_all_folders()
        all_tags = [tag["name"] for tag in db.get_all_tags()]
        dlg = NewPromptModal(folders=folders, all_tags=all_tags, prompt_data=data, as_window=True, parent=None)
        dlg.saved.connect(self._on_prompt_saved)
        dlg.finished.connect(lambda _result, pid=prompt_id: self._on_editor_window_closed(pid))
        self._open_edit_windows[prompt_id] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_editor_window_closed(self, prompt_id: int):
        self._open_edit_windows.pop(prompt_id, None)

    def _close_editor_window(self, prompt_id: int):
        editor = self._open_edit_windows.pop(prompt_id, None)
        if editor is not None:
            editor.close()

    def _on_prompt_saved(self, data: dict):
        if data.get("__delete__"):
            self._delete_prompt_ids([data["id"]])
            return

        if data.get("id"):
            db.update_prompt(
                data["id"],
                title=data["title"],
                content=data["content"],
                folder_id=data.get("folder_id"),
                tag_names=data.get("tags", []),
            )
        else:
            created_id = db.create_prompt(
                title=data["title"],
                content=data["content"],
                folder_id=data.get("folder_id"),
                tag_names=data.get("tags", []),
            )
            data["id"] = created_id

        self._refresh_sidebar()
        self._reload_prompts()

    def _open_detail(self, prompt_id: int):
        data = db.get_prompt(prompt_id)
        if not data:
            return
        db.touch_prompt(prompt_id)
        self._last_used_prompt_id = prompt_id
        self._refresh_sidebar()

        dlg = PromptDetailModal(data=data, as_window=True, parent=None)
        dlg.edit_requested.connect(self._open_edit)
        dlg.finished.connect(lambda _result, window=dlg: self._on_detail_window_closed(window))
        self._open_detail_windows.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_detail_window_closed(self, window: PromptDetailModal):
        if window in self._open_detail_windows:
            self._open_detail_windows.remove(window)

    def _close_detail_windows_for_prompt(self, prompt_id: int):
        for window in list(self._open_detail_windows):
            data = getattr(window, "_data", None)
            if isinstance(data, dict) and data.get("id") == prompt_id:
                window.close()

    def _sync_kbd_selection(self):
        widgets = self._content.get_visible_prompt_widgets()
        for pid, widget in widgets:
            widget.set_kbd_selected(pid == self._kbd_selected_id)

    def _set_kbd_selected_id(self, prompt_id: int | None):
        self._kbd_selected_id = prompt_id
        selected_widget = None
        for pid, widget in self._content.get_visible_prompt_widgets():
            is_selected = pid == prompt_id
            widget.set_kbd_selected(is_selected)
            if is_selected:
                selected_widget = widget
        self._content.ensure_prompt_visible(selected_widget)

    def _focus_cycle_sidebar(self):
        self._sidebar.focus_cycle()

    def _focus_in_text_input(self) -> bool:
        widget = QApplication.focusWidget()
        return isinstance(widget, (QLineEdit, QTextEdit, QComboBox))

    def _focus_in_sidebar(self) -> bool:
        widget = QApplication.focusWidget()
        return self._sidebar.contains_widget(widget)

    def _handle_arrow_navigation(self, direction: int) -> bool:
        widgets = self._content.get_visible_prompt_widgets()
        if not widgets:
            return False

        ids = [pid for pid, _ in widgets]
        if self._kbd_selected_id in ids:
            current_index = ids.index(self._kbd_selected_id)
        else:
            current_index = 0 if direction > 0 else len(ids) - 1

        next_index = max(0, min(len(ids) - 1, current_index + direction))
        self._set_kbd_selected_id(ids[next_index])
        return True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not self._focus_in_text_input():
            self._focus_cycle_sidebar()
            event.accept()
            return

        if not self._focus_in_text_input() and not self._focus_in_sidebar() and not event.modifiers():
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
                if self._handle_arrow_navigation(-1):
                    event.accept()
                    return
            if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                if self._handle_arrow_navigation(1):
                    event.accept()
                    return

            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._kbd_selected_id is not None:
                    self._open_detail(self._kbd_selected_id)
                    event.accept()
                    return

        super().keyPressEvent(event)

    def _on_star(self, prompt_id: int, _new_val: bool):
        db.toggle_favorite(prompt_id)
        if self._fs.section == "favorites":
            self._reload_prompts()

    def _delete_single(self, prompt_id: int):
        self._delete_prompt_ids([prompt_id])

    def _delete_prompt_ids(self, prompt_ids: list[int]):
        # Close any open windows first (even if the prompt is gone).
        for prompt_id in prompt_ids:
            self._close_editor_window(prompt_id)
            self._close_detail_windows_for_prompt(prompt_id)

        if not prompt_ids:
            return

        # Invalidate any note-clipboard entries that are being deleted so
        # a subsequent paste cannot operate on ghost IDs.
        if self._note_clipboard:
            dying = set(prompt_ids)
            surviving = [i for i in self._note_clipboard["ids"] if i not in dying]
            if surviving:
                self._note_clipboard["ids"] = surviving
            else:
                self._note_clipboard = None

        # Move to trash — move_to_trash handles missing IDs gracefully (returns 0).
        if len(prompt_ids) == 1:
            trash_ids = [db.move_to_trash(prompt_ids[0])]
        else:
            trash_ids = db.bulk_move_to_trash(prompt_ids)

        # Only count IDs that were actually moved (non-zero return).
        valid_trash_ids = [tid for tid in trash_ids if tid > 0]
        if not valid_trash_ids:
            return

        n = len(valid_trash_ids)
        self._refresh_sidebar()
        self._reload_prompts()

        # Capture the exact set of trash IDs in the closure so that a
        # subsequent delete cannot overwrite the undo target for this toast.
        def _undo(captured_ids: list[int] = valid_trash_ids):
            for tid in captured_ids:
                db.restore_from_trash(tid)
            self._refresh_sidebar()
            self._reload_prompts()
            self._content.show_toast("Restored from Trash.")

        self._content.show_toast(
            f"Moved {n} note{'s' if n != 1 else ''} to Trash.",
            undo_callback=_undo,
        )

    def _copy_content(self, prompt_id: int):
        prompt = db.get_prompt(prompt_id)
        if prompt:
            self._set_clipboard_content(prompt["content"])
            db.touch_prompt(prompt_id)
            self._last_used_prompt_id = prompt_id
            self._refresh_sidebar()
            self._content.show_toast("Prompt copied.")

    # ── Note clipboard (cut / copy / paste) ───────────────────────────────────

    def _cut_note(self, prompt_id: int):
        self._note_clipboard = {"mode": "cut", "ids": [prompt_id]}
        self._content.show_toast("Note cut — navigate to a folder or tag and paste.")

    def _copy_note(self, prompt_id: int):
        self._note_clipboard = {"mode": "copy", "ids": [prompt_id]}
        self._content.show_toast("Note copied — navigate to a folder or tag and paste.")

    def _handle_paste_shortcut(self):
        """Ctrl+V handler — only triggers paste when focused outside a text field."""
        if self._focus_in_text_input():
            return
        self._paste_notes()

    def _paste_notes(self):
        cb = self._note_clipboard
        if not cb:
            self._content.show_toast("Clipboard is empty.")
            return

        ids: list[int] = list(cb["ids"])  # defensive copy
        mode: str = cb["mode"]  # "cut" or "copy"
        section = self._fs.section

        if section == "trash":
            self._content.show_toast("Cannot paste into Trash.")
            return

        if not ids:
            self._note_clipboard = None
            self._content.show_toast("Clipboard is empty.")
            return

        n = len(ids)

        # ── Determine destination ──────────────────────────────────────────
        # A folder section always takes priority over a tag filter so that
        # "folder + tag" filtered views paste into the folder, not just tag.
        dest_is_folder = isinstance(section, int)

        if dest_is_folder:
            folder_id: int | None = section
            folders = db.get_all_folders()
            dest_name = next((f["name"] for f in folders if f["id"] == folder_id), "folder")

            if mode == "cut":
                db.bulk_move_prompts(ids, folder_id)
                self._note_clipboard = None
                self._content.show_toast(
                    f"Moved {n} note{'s' if n != 1 else ''} to {dest_name}."
                )
            else:
                new_ids = db.bulk_copy_prompts(ids, folder_id)
                self._content.show_toast(
                    f"Copied {len(new_ids)} note{'s' if len(new_ids) != 1 else ''} to {dest_name}."
                )

        elif len(self._fs.tags) == 1:
            # Tag-only destination: folder takes the back seat
            tag_name = self._fs.tags[0]

            if mode == "copy":
                # Duplicate notes preserving each note's original folder, then tag.
                new_ids = db.bulk_copy_prompts(ids, None, preserve_folder=True)
                db.bulk_add_tag(new_ids, tag_name)
                self._content.show_toast(
                    f"Copied {len(new_ids)} note{'s' if len(new_ids) != 1 else ''} with tag #{tag_name}."
                )
            else:
                # Cut → tag the originals in-place; folder membership unchanged.
                db.bulk_add_tag(ids, tag_name)
                self._note_clipboard = None
                self._content.show_toast(
                    f"Added tag #{tag_name} to {n} note{'s' if n != 1 else ''}."
                )

        else:
            # "All Notes" / "Favorites" / multi-tag view → remove folder membership
            folder_id = None
            dest_name = "All Notes"

            if mode == "cut":
                db.bulk_move_prompts(ids, folder_id)
                self._note_clipboard = None
                self._content.show_toast(
                    f"Moved {n} note{'s' if n != 1 else ''} to {dest_name}."
                )
            else:
                new_ids = db.bulk_copy_prompts(ids, folder_id)
                self._content.show_toast(
                    f"Copied {len(new_ids)} note{'s' if len(new_ids) != 1 else ''} to {dest_name}."
                )

        self._refresh_sidebar()
        self._reload_prompts()

    def _single_export(self, prompt_id: int):
        dlg = BulkExportModal(count=1, parent=self)
        dlg.export_json.connect(lambda: self._export_prompt_ids([prompt_id], fmt="json"))
        dlg.export_txt.connect(lambda: self._export_prompt_ids([prompt_id], fmt="txt"))
        dlg.export_clipboard.connect(lambda: self._export_to_clipboard([prompt_id]))
        dlg.exec()

    def _add_tag_to_note(self, prompt_id: int):
        all_tags = [t["name"] for t in db.get_all_tags()]
        dlg = _TagInputDialog(all_tags, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            tag = dlg.get_tag()
            if tag:
                db.bulk_add_tag([prompt_id], tag)
                self._refresh_sidebar()
                self._reload_prompts()
                self._content.show_toast(f"Added tag #{tag}.")

    def _on_card_context_menu(self, prompt_id: int, global_pos):
        # When multiple cards are selected and the right-clicked card is among them,
        # show the batch context menu instead of the single-item menu.
        if len(self._selected_ids) >= 2 and prompt_id in self._selected_ids:
            self._show_batch_context_menu(global_pos)
            return

        _ic = C["text_secondary"]
        menu = QMenu(self)

        edit_action = menu.addAction("✏️  Edit")
        edit_action.triggered.connect(lambda: self._open_edit(prompt_id))

        menu.addSeparator()

        cut_action = menu.addAction(make_icon("cut.png", _ic, 16), "Cut")
        cut_action.triggered.connect(lambda: self._cut_note(prompt_id))

        copy_note_action = menu.addAction(make_icon("copy.png", _ic, 16), "Copy note")
        copy_note_action.triggered.connect(lambda: self._copy_note(prompt_id))

        cb = self._note_clipboard
        if cb:
            n_cb = len(cb["ids"])
            mode_label = "Move" if cb["mode"] == "cut" else "Copy"
            paste_label = (
                f"Paste ({n_cb} note{'s' if n_cb != 1 else ''} — {mode_label})"
            )
            paste_action = menu.addAction(make_icon("paste.png", _ic, 16), paste_label)
            paste_action.setEnabled(self._fs.section != "trash")
            paste_action.triggered.connect(self._paste_notes)

        menu.addSeparator()

        export_action = menu.addAction(make_icon("export.png", _ic, 16), "Export")
        export_action.triggered.connect(lambda: self._single_export(prompt_id))

        add_tag_action = menu.addAction(make_icon("tags.png", _ic, 16), "Add Tag…")
        add_tag_action.triggered.connect(lambda: self._add_tag_to_note(prompt_id))

        menu.addSeparator()

        copy_content_action = menu.addAction(make_icon("copy.png", _ic, 16), "Copy content")
        copy_content_action.triggered.connect(lambda: self._copy_content(prompt_id))

        delete_action = menu.addAction(make_icon("trash.png", C["danger"], 16), "Delete")
        delete_action.triggered.connect(lambda: self._delete_single(prompt_id))

        pos = global_pos if isinstance(global_pos, QPoint) else QCursor.pos()
        menu.exec(pos)

    def _show_batch_context_menu(self, global_pos):
        ids = sorted(self._selected_ids)
        n = len(ids)
        _ic = C["text_secondary"]
        menu = QMenu(self)

        cut_action = menu.addAction(make_icon("cut.png", _ic, 16), f"Cut {n} notes")
        cut_action.triggered.connect(lambda: self._batch_cut_notes(ids))

        copy_action = menu.addAction(make_icon("copy.png", _ic, 16), f"Copy {n} notes")
        copy_action.triggered.connect(lambda: self._batch_copy_notes(ids))

        menu.addSeparator()

        delete_action = menu.addAction(make_icon("trash.png", C["danger"], 16), f"Delete {n} notes")
        delete_action.triggered.connect(self._bulk_delete)

        pos = global_pos if isinstance(global_pos, QPoint) else QCursor.pos()
        menu.exec(pos)

    def _batch_cut_notes(self, ids: list[int]):
        self._note_clipboard = {"mode": "cut", "ids": list(ids)}
        n = len(ids)
        self._content.show_toast(
            f"{n} note{'s' if n != 1 else ''} cut — navigate to a folder or tag and paste."
        )

    def _batch_copy_notes(self, ids: list[int]):
        self._note_clipboard = {"mode": "copy", "ids": list(ids)}
        n = len(ids)
        self._content.show_toast(
            f"{n} note{'s' if n != 1 else ''} copied — navigate to a folder or tag and paste."
        )

    def _on_area_context_menu(self, global_pos):
        menu = QMenu(self)
        in_trash = self._fs.section == "trash"
        _ic = C["text_secondary"]

        cb = self._note_clipboard
        if cb:
            n_cb = len(cb["ids"])
            mode_label = "Move" if cb["mode"] == "cut" else "Copy"
            paste_note_label = (
                f"Paste: Note ({n_cb} note{'s' if n_cb != 1 else ''} — {mode_label})"
            )
        else:
            paste_note_label = "Paste: Note"
        paste_note_action = menu.addAction(make_icon("paste.png", _ic, 16), paste_note_label)
        paste_note_action.setEnabled(bool(cb) and not in_trash)
        paste_note_action.triggered.connect(self._paste_notes)

        os_clipboard_text = QApplication.clipboard().text().strip()
        paste_clip_action = menu.addAction(make_icon("paste.png", _ic, 16), "Paste: Clipboard to Note")
        paste_clip_action.setEnabled(bool(os_clipboard_text) and not in_trash)
        paste_clip_action.triggered.connect(self._paste_clipboard_to_note)

        menu.addSeparator()

        folder_id = self._fs.section if isinstance(self._fs.section, int) else None
        create_folder_label = "Create New Folder (here)" if folder_id is not None else "Create New Folder"
        create_folder_action = menu.addAction(make_icon("folder.png", _ic, 16), create_folder_label)
        create_folder_action.setEnabled(not in_trash)
        create_folder_action.triggered.connect(lambda: self._open_create_folder_modal(folder_id))

        pos = global_pos if isinstance(global_pos, QPoint) else QCursor.pos()
        menu.exec(pos)

    def _paste_clipboard_to_note(self):
        os_clipboard_text = QApplication.clipboard().text().strip()
        if not os_clipboard_text:
            self._content.show_toast("System clipboard is empty.")
            return
        folders = db.get_all_folders()
        all_tags = [tag["name"] for tag in db.get_all_tags()]
        default_folder_id, default_tags = self._get_new_prompt_defaults()
        dlg = NewPromptModal(
            folders=folders,
            all_tags=all_tags,
            default_folder_id=default_folder_id,
            default_tags=default_tags,
            default_content=os_clipboard_text,
            as_window=True,
            parent=None,
        )
        dlg.saved.connect(self._on_prompt_saved)
        dlg.finished.connect(lambda _result, window=dlg: self._on_new_prompt_window_closed(window))
        self._open_new_windows.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _open_tag_mgr(self):
        tags = db.get_all_tags()

        def _create(name: str, color: str | None = None) -> int:
            return db.create_tag(name.strip().lower().lstrip("#"), color)

        def _rename(tag_id: int, name: str):
            existing_tags = db.get_all_tags()
            old_tag = next((t for t in existing_tags if t["id"] == tag_id), None)
            old_name = old_tag["name"] if old_tag else ""
            normalized_name = name.strip().lower().lstrip("#")
            db.rename_tag(tag_id, normalized_name)
            if old_name and self._fs.tags == [old_name]:
                self._fs.tags = [normalized_name]

        dlg = TagManagerModal(
            tags=tags,
            db_ops={
                "create": _create,
                "rename": _rename,
                "delete": self._delete_tag_with_undo,
                "set_color": db.set_tag_color,
            },
            parent=self,
        )
        dlg.changed.connect(lambda: (self._refresh_sidebar(), self._reload_prompts()))
        dlg.exec()

    def _delete_folder_with_undo(self, folder_id: int):
        folders = db.get_all_folders_tree()
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            return

        child_folders = [
            {"id": f["id"], "name": f["name"]}
            for f in folders
            if f.get("parent_id") == folder_id
        ]
        prompt_ids = [p["id"] for p in db.get_prompts(folder_id=folder_id)]

        self._undo_folder_payload = {
            "folder": {
                "name": folder["name"],
                "parent_id": folder.get("parent_id"),
                "color": folder.get("color"),
            },
            "child_folders": child_folders,
            "prompt_ids": prompt_ids,
        }

        db.delete_folder(folder_id)
        if self._fs.section == folder_id:
            self._fs.section = "all"
            self._sidebar.set_active("all")

        self._refresh_sidebar()
        self._reload_prompts()
        self._content.show_toast(f"Deleted folder \"{folder['name']}\".", undo_callback=self._undo_folder_delete)

    def _undo_folder_delete(self):
        payload = self._undo_folder_payload
        if not payload:
            return

        folder = payload.get("folder", {})
        restored_folder_id = db.create_folder(
            folder.get("name", ""),
            folder.get("parent_id"),
            folder.get("color"),
        )

        prompt_ids = payload.get("prompt_ids", [])
        if prompt_ids:
            db.bulk_move_prompts(prompt_ids, restored_folder_id)

        child_folders = payload.get("child_folders", [])
        all_folders = db.get_all_folders_tree()
        folder_names_by_id = {f["id"]: f["name"] for f in all_folders}
        for child in child_folders:
            child_id = child.get("id")
            child_name = folder_names_by_id.get(child_id)
            if child_id and child_name:
                db.rename_folder(child_id, child_name, restored_folder_id)

        self._undo_folder_payload = None
        self._refresh_sidebar()
        self._reload_prompts()
        self._content.show_toast(f"Restored folder \"{folder.get('name', '')}\".")

    def _open_folder_quick_edit(self, folder_id: int):
        folders = db.get_all_folders_tree()
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if not folder:
            return

        dlg = ItemEditModal(
            title="Edit Folder",
            item_name=folder["name"],
            item_color=folder.get("color"),
            delete_label="Delete Folder",
            parent=self,
        )

        def _save(name: str, color: str | None):
            db.rename_folder(folder_id, name, folder.get("parent_id"))
            db.set_folder_color(folder_id, color)
            self._refresh_sidebar()
            self._reload_prompts()

        def _delete():
            self._delete_folder_with_undo(folder_id)

        dlg.saved.connect(_save)
        dlg.deleted.connect(_delete)
        dlg.exec()

    def _open_tag_quick_edit(self, tag_id: int):
        tags = db.get_all_tags()
        tag = next((t for t in tags if t["id"] == tag_id), None)
        if not tag:
            return

        dlg = ItemEditModal(
            title="Edit Tag",
            item_name=tag["name"],
            item_color=tag.get("color"),
            delete_label="Delete Tag",
            parent=self,
        )

        def _save(name: str, color: str | None):
            old_name = tag["name"]
            normalized = name.strip().lower().lstrip("#")
            db.rename_tag(tag_id, normalized)
            db.set_tag_color(tag_id, color)
            if self._fs.tags == [old_name]:
                self._fs.tags = [normalized]
            self._refresh_sidebar()
            self._reload_prompts()

        def _delete():
            self._delete_tag_with_undo(tag_id)

        dlg.saved.connect(_save)
        dlg.deleted.connect(_delete)
        dlg.exec()

    def _delete_tag_with_undo(self, tag_id: int):
        tags = db.get_all_tags()
        tag = next((t for t in tags if t["id"] == tag_id), None)
        if not tag:
            return

        tagged_prompt_ids = [p["id"] for p in db.get_prompts(tag_names=[tag["name"]])]
        self._undo_tag_payload = {
            "name": tag["name"],
            "color": tag.get("color"),
            "prompt_ids": tagged_prompt_ids,
        }

        db.delete_tag(tag_id)
        if self._fs.tags == [tag["name"]]:
            self._fs.tags = []

        self._refresh_sidebar()
        self._reload_prompts()
        self._content.show_toast(f"Deleted tag #{tag['name']}.", undo_callback=self._undo_tag_delete)

    def _undo_tag_delete(self):
        payload = self._undo_tag_payload
        if not payload:
            return

        tag_name = payload.get("name", "")
        if not tag_name:
            return

        restored_tag_id = db.create_tag(tag_name, payload.get("color"))
        prompt_ids = payload.get("prompt_ids", [])
        if prompt_ids:
            db.bulk_add_tag(prompt_ids, tag_name)
        if restored_tag_id and payload.get("color"):
            db.set_tag_color(restored_tag_id, payload.get("color"))

        self._undo_tag_payload = None
        self._refresh_sidebar()
        self._reload_prompts()
        self._content.show_toast(f"Restored tag #{tag_name}.")

    def _open_settings(self):
        dlg = SettingsModal(current_theme=self._theme, parent=self)
        dlg.theme_changed.connect(self._apply_theme)
        dlg.import_req.connect(self._import_prompts)
        dlg.export_req.connect(self._export_current_or_selected)
        dlg.exec()

    def _on_selection_mode_changed(self, enabled: bool):
        self._content.set_selection_mode(enabled)
        if not enabled:
            self._selected_ids.clear()
            self._content.set_selection_count(0)

    def _on_selection_toggled(self, prompt_id: int, selected: bool):
        if selected:
            self._selected_ids.add(prompt_id)
        else:
            self._selected_ids.discard(prompt_id)
        self._content.set_selection_count(len(self._selected_ids))

    def _on_ctrl_click(self, prompt_id: int):
        """Ctrl+Click toggles selection on a card, entering selection mode if needed."""
        if not self._content._select_btn.isChecked():
            self._content._select_btn.setChecked(True)
        currently_selected = prompt_id in self._selected_ids
        self._content.set_card_selected(prompt_id, not currently_selected)

    def _bulk_move(self, folder_id):
        ids = sorted(self._selected_ids)
        if not ids:
            return
        db.bulk_move_prompts(ids, folder_id)
        self._selected_ids.clear()
        self._content.clear_bulk_inputs()
        self._content.show_toast("Moved selected prompts.")
        self._refresh_sidebar()
        self._reload_prompts()

    def _bulk_copy(self, folder_id):
        ids = sorted(self._selected_ids)
        if not ids:
            return
        new_ids = db.bulk_copy_prompts(ids, folder_id)
        self._content.show_toast(f"Copied {len(new_ids)} prompt{'s' if len(new_ids) != 1 else ''}.")
        self._refresh_sidebar()
        self._reload_prompts()

    def _bulk_add_tag(self, tag_name: str):
        ids = sorted(self._selected_ids)
        if not ids or not tag_name:
            return
        normalized = tag_name.strip().lower().lstrip("#")
        if not normalized:
            return
        db.bulk_add_tag(ids, normalized)
        self._content.clear_bulk_inputs()
        self._content.show_toast(f"Added tag #{normalized}.")
        self._refresh_sidebar()
        self._reload_prompts()

    def _bulk_delete(self):
        ids = sorted(self._selected_ids)
        if not ids:
            return
        self._selected_ids.clear()
        self._delete_prompt_ids(ids)

    def _bulk_export(self):
        ids = sorted(self._selected_ids)
        if not ids:
            self._content.show_toast("Nothing to export.")
            return
        dlg = BulkExportModal(count=len(ids), parent=self)
        dlg.export_json.connect(lambda: self._export_prompt_ids(ids, fmt="json"))
        dlg.export_txt.connect(lambda: self._export_prompt_ids(ids, fmt="txt"))
        dlg.export_clipboard.connect(lambda: self._export_to_clipboard(ids))
        dlg.exec()

    def _export_current_or_selected(self):
        all_rows = db.export_prompts()
        ids = [r["id"] for r in all_rows]
        if not ids:
            self._content.show_toast("Nothing to export.")
            return
        dlg = BulkExportModal(
            count=len(ids),
            parent=self,
            show_clipboard=False,
            subtitle_override=f"All {len(ids)} note{'s' if len(ids) != 1 else ''}",
        )
        dlg.export_json.connect(lambda: self._export_prompt_ids(ids, fmt="json"))
        dlg.export_txt.connect(lambda: self._export_prompt_ids(ids, fmt="txt"))
        dlg.exec()

    def _export_to_clipboard(self, prompt_ids: list[int]):
        if not prompt_ids:
            return
        rows = db.export_prompts(prompt_ids)
        parts: list[str] = []
        for row in rows:
            title = row.get("title", "").strip()
            content = row.get("content", "").strip()
            tags = row.get("tags", [])
            block = f"# {title}" if title else "# (untitled)"
            if tags:
                block += f"\nTags: {', '.join('#' + t for t in tags)}"
            block += f"\n\n{content}"
            parts.append(block)
        text = "\n\n---\n\n".join(parts)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        self._content.show_toast(f"Copied {len(rows)} prompt{'s' if len(rows) != 1 else ''} to clipboard.")

    def _export_prompt_ids(self, prompt_ids: list[int], fmt: str = ""):
        if not prompt_ids:
            self._content.show_toast("Nothing to export.")
            return

        if fmt == "txt":
            default_name = "prompts.txt"
            file_filter = "Text Files (*.txt)"
        else:
            default_name = "prompts.json"
            file_filter = "JSON Files (*.json);;CSV Files (*.csv)"

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Prompts",
            default_name,
            file_filter,
        )
        if not path:
            return

        rows = db.export_prompts(prompt_ids)
        target = Path(path)

        if fmt == "txt" or target.suffix.lower() == ".txt":
            parts: list[str] = []
            for row in rows:
                title = row.get("title", "").strip()
                content = row.get("content", "").strip()
                tags = row.get("tags", [])
                folder_path = row.get("folder_path", "NoteStack")
                block = f"# {title}" if title else "# (untitled)"
                block += f"\nFolder: {folder_path}"
                if tags:
                    block += f"\nTags: {', '.join('#' + t for t in tags)}"
                block += f"\n\n{content}"
                parts.append(block)
            with target.open("w", encoding="utf-8") as f:
                f.write("\n\n---\n\n".join(parts))
        elif "CSV" in selected_filter or target.suffix.lower() == ".csv":
            with target.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "title",
                        "content",
                        "folder_path",
                        "tags",
                        "created_at",
                        "updated_at",
                        "is_favorite",
                        "last_accessed_at",
                    ],
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "title": row.get("title", ""),
                            "content": row.get("content", ""),
                            "folder_path": row.get("folder_path", "NoteStack"),
                            "tags": "|".join(row.get("tags", [])),
                            "created_at": row.get("created_at", ""),
                            "updated_at": row.get("updated_at", ""),
                            "is_favorite": 1 if row.get("is_favorite") else 0,
                            "last_accessed_at": row.get("last_accessed_at", ""),
                        }
                    )
        else:
            payload = []
            for row in rows:
                payload.append(
                    {
                        "title": row.get("title", ""),
                        "content": row.get("content", ""),
                        "folder_path": row.get("folder_path", "NoteStack"),
                        "tags": row.get("tags", []),
                        "created_at": row.get("created_at", ""),
                        "updated_at": row.get("updated_at", ""),
                        "is_favorite": 1 if row.get("is_favorite") else 0,
                        "last_accessed_at": row.get("last_accessed_at", ""),
                    }
                )
            with target.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        self._content.show_toast(f"Exported {len(rows)} prompts.")

    def _import_prompts(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Prompts",
            "",
            "JSON Files (*.json);;CSV Files (*.csv)",
        )
        if not path:
            return

        source = Path(path)
        rows: list[dict] = []

        if source.suffix.lower() == ".csv":
            with source.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(
                        {
                            "title": row.get("title", ""),
                            "content": row.get("content", ""),
                            "folder_path": row.get("folder_path", ""),
                            "folder": row.get("folder", ""),
                            "tags": row.get("tags", ""),
                            "created_at": row.get("created_at", ""),
                            "updated_at": row.get("updated_at", ""),
                            "is_favorite": row.get("is_favorite", "0") in {"1", "true", "True"},
                            "last_accessed_at": row.get("last_accessed_at", ""),
                        }
                    )
        else:
            with source.open("r", encoding="utf-8") as f:
                parsed = json.load(f)
                if isinstance(parsed, list):
                    rows = parsed

        imported = db.import_prompts(rows)
        self._refresh_sidebar()
        self._reload_prompts()
        self._content.show_toast(f"Imported {imported} prompts.")
