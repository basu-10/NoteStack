"""
New / Edit Note modal.
"""
from __future__ import annotations
import re
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QFrame, QWidget, QCompleter,
    QToolButton, QSizePolicy, QApplication, QMenu, QMessageBox,
)
from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, pyqtSignal, QStringListModel
from PyQt6.QtGui import QColor, QCursor, QKeySequence, QShortcut
from ui.tui_editor_widget import TuiEditorWidget
from ui.flow_layout import FlowLayout
from ui.icon_utils import make_icon
from ui.snap_utils import SnapOverlay, get_snap_zone
from ui.styles import get_current_theme


class TagSuggestLineEdit(QLineEdit):
    def __init__(self, known_tags: list[str], parent=None):
        super().__init__(parent)
        self._known_tags = sorted({t.strip().lower() for t in known_tags if t.strip()})
        self._model = QStringListModel(self._known_tags, self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        # Use setWidget() instead of setCompleter() so Qt does NOT auto-manage
        # the prefix (which would use the full field text, breaking # parsing).
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._refresh_suggestions)

    def _active_hash_context(self) -> tuple[int, int, str] | None:
        """
        Returns (hash_start, cursor_pos, token_after_hash) when the cursor is
        inside an active #tag token, otherwise None.
        """
        cursor = self.cursorPosition()
        text_before_cursor = self.text()[:cursor]
        hash_start = text_before_cursor.rfind("#")
        if hash_start < 0:
            return None

        # # must be at line start or preceded by whitespace / comma
        if hash_start > 0 and text_before_cursor[hash_start - 1] not in (" ", "\t", ","):
            return None

        token = text_before_cursor[hash_start + 1:]
        # If the token itself contains a space or comma the # context has ended
        if any(ch in (" ", "\t", ",") for ch in token):
            return None

        return hash_start, cursor, token.lower()

    def _refresh_suggestions(self, _text: str):
        context = self._active_hash_context()
        if context is None:
            popup = self._completer.popup()
            if popup is not None:
                popup.hide()
            return

        _, _, token = context
        self._completer.setCompletionPrefix(token)

        if self._completer.completionCount() == 0:
            popup = self._completer.popup()
            if popup is not None:
                popup.hide()
            return

        # Position popup below the field and show it
        cr = self.rect()
        self._completer.complete(cr)

    def _insert_completion(self, selected: str):
        context = self._active_hash_context()
        if context is None:
            return

        hash_start, cursor, _ = context
        current = self.text()
        # Replace the partial token after # with the chosen suggestion
        new_text = current[: hash_start + 1] + selected + current[cursor:]
        self.setText(new_text)
        self.setCursorPosition(hash_start + 1 + len(selected))
        self.setFocus()

    def update_known_tags(self, tags: list[str]):
        """Replace the tag completion list with a fresh set."""
        self._known_tags = sorted({t.strip().lower() for t in tags if t.strip()})
        self._model.setStringList(self._known_tags)


class _TagCheckMenu(QMenu):
    """QMenu whose checkable items stay open so the user can toggle multiple tags."""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action is not None and action.isCheckable():
            action.setChecked(not action.isChecked())
            action.triggered.emit(action.isChecked())
        else:
            super().mouseReleaseEvent(event)


class NewPromptModal(QDialog):
    """
    Emits saved(data_dict) on successful save.
    Pass prompt_data to pre-fill for editing.
    """
    saved = pyqtSignal(dict)

    _SAVE_DOT_RED = "#EF4444"
    _SAVE_DOT_GREEN = "#16A34A"
    _SAVE_DOT_IDLE = "#8A8FA5"

    def __init__(
        self,
        folders: list[dict],
        all_tags: list[str],
        prompt_data: dict | None = None,
        default_folder_id: int | None = None,
        default_tags: list[str] | None = None,
        default_content: str | None = None,
        as_window: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._folders = folders
        self._all_tags = all_tags
        self._editing = prompt_data
        self._default_folder_id = default_folder_id
        self._default_tags = [t.strip().lower().lstrip("#") for t in (default_tags or []) if t and t.strip()]
        self._default_content = default_content
        self._as_window = as_window
        self._settings = QSettings()
        self._settings_prefix = "ui/modals/prompt_edit"
        self._resize_margin = 8
        self._resize_edges = 0
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = self.geometry()
        base_window_type = Qt.WindowType.Window if self._as_window else Qt.WindowType.Dialog
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | base_window_type)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(not self._as_window)
        self.setMinimumSize(760, 560)
        self._dragging = False
        self._drag_offset = QPoint()
        self._drag_from_maximized = False
        self._drag_from_snapped = False
        self._drag_norm_x = 0.5
        self._pre_snap_geometry: QRect | None = None
        self._snap_zone: str | None = None
        self._snap_rect: QRect | None = None
        self._snap_overlay = SnapOverlay()
        self._last_saved_snapshot: tuple = ()
        self._has_unsaved_changes = False
        self._build()
        if prompt_data:
            self._prefill(prompt_data)
        else:
            self._apply_new_defaults()
        self._initialize_save_tracking()
        self._on_content_changed()
        self._restore_ui_settings()
        self._install_resize_filter()
        if self._as_window:
            self._update_window_title_from_input()
            self._title_input.textChanged.connect(self._update_window_title_from_input)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(0)

        # Header
        self._drag_handle = QWidget()
        hdr = QHBoxLayout(self._drag_handle)
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)
        icon_lbl = QLabel("✏️" if self._editing else "✦")
        icon_lbl.setStyleSheet("color:#4F6EF7; font-size:16px;")
        self._title_input = QLineEdit()
        self._title_input.setObjectName("ModalTitleInput")
        self._title_input.setPlaceholderText("Click here to enter title")
        self._title_input.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._save_state_dot = QLabel("●")
        self._save_state_dot.setObjectName("SaveStateDot")
        self._save_state_dot.setStyleSheet(f"color:{self._SAVE_DOT_IDLE}; font-size:12px;")
        self._save_state_dot.setToolTip("No pending changes")
        self._save_toolbar_btn = QPushButton()
        self._save_toolbar_btn.setObjectName("ModalIconBtn")
        self._save_toolbar_btn.setFixedSize(28, 28)
        self._save_toolbar_btn.setIcon(make_icon("export.png", "#52A0F0", 16))
        self._save_toolbar_btn.setIconSize(QSize(16, 16))
        self._save_toolbar_btn.setToolTip("Save (Ctrl+S)")
        self._save_toolbar_btn.clicked.connect(self._on_save_shortcut)
        self._delete_toolbar_btn = QPushButton()
        self._delete_toolbar_btn.setObjectName("ModalIconBtn")
        self._delete_toolbar_btn.setFixedSize(28, 28)
        self._delete_toolbar_btn.setIcon(make_icon("trash.png", "#EF4444", 16))
        self._delete_toolbar_btn.setIconSize(QSize(16, 16))
        self._delete_toolbar_btn.setToolTip("Delete")
        self._delete_toolbar_btn.setVisible(bool(self._editing))
        self._delete_toolbar_btn.clicked.connect(self._on_delete)
        self._minimize_btn = QPushButton("🗕")
        self._minimize_btn.setObjectName("ModalIconBtn")
        self._minimize_btn.setFixedSize(28, 28)
        self._minimize_btn.setToolTip("Minimize")
        self._minimize_btn.clicked.connect(self.showMinimized)
        self._fullscreen_btn = QPushButton("⤢")
        self._fullscreen_btn.setObjectName("ModalIconBtn")
        self._fullscreen_btn.setFixedSize(28, 28)
        self._fullscreen_btn.setToolTip("Maximize / Restore")
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        close = QPushButton("✕")
        close.setObjectName("CloseBtn")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.reject)
        hdr.addWidget(icon_lbl)
        hdr.addWidget(self._title_input)
        hdr.addStretch(1)
        hdr.addWidget(self._save_state_dot)
        hdr.addWidget(self._delete_toolbar_btn)
        hdr.addWidget(self._save_toolbar_btn)
        hdr.addWidget(self._minimize_btn)
        hdr.addWidget(self._fullscreen_btn)
        hdr.addWidget(close)
        v.addWidget(self._drag_handle)
        v.addSpacing(10)

        div = QFrame(); div.setObjectName("Divider")
        v.addWidget(div)
        v.addSpacing(8)

        # Compact metadata strip (folder + tags) — sits below the header divider
        self._meta_handle = QWidget()
        self._meta_handle.setObjectName("MetaHandle")
        self._meta_handle.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        meta_row = QHBoxLayout(self._meta_handle)
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)

        folder_icon = QLabel("📁")
        folder_icon.setStyleSheet("font-size:13px;")
        self._folder_btn = QToolButton()
        self._folder_btn.setObjectName("MetaDropBtn")
        self._folder_btn.setText("Move to…")
        self._folder_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._folder_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._folder_btn.clicked.connect(self._open_folder_picker)

        sep = QLabel("·")
        sep.setStyleSheet("color:#52526A; font-size:14px; padding:0 4px;")

        tags_icon = QLabel("🏷")
        tags_icon.setStyleSheet("font-size:13px;")
        self._tags_btn = QToolButton()
        self._tags_btn.setObjectName("MetaDropBtn")
        self._tags_btn.setText("Add tag…")
        self._tags_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._tags_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._tags_btn.clicked.connect(self._open_tags_editor)

        meta_row.addWidget(folder_icon)
        meta_row.addWidget(self._folder_btn)
        meta_row.addWidget(sep)
        meta_row.addWidget(tags_icon)
        meta_row.addWidget(self._tags_btn)
        meta_row.addStretch(1)

        v.addWidget(self._meta_handle)
        v.addSpacing(12)

        # Content field — Toast UI Editor (Markdown)
        self._content_input = TuiEditorWidget(
            theme=get_current_theme(),
            mode="markdown",
            parent=self,
        )
        self._content_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_input.setMinimumHeight(220)
        v.addWidget(self._content_input, 1)
        self._stats_lbl = QLabel("")
        self._stats_lbl.setObjectName("ContentStats")
        v.addWidget(self._stats_lbl)
        v.addSpacing(18)

        self._folder_combo = QComboBox(self)
        self._folder_combo.addItem("No folder", None)
        for folder in self._folders:
            self._folder_combo.addItem(folder["name"], folder["id"])
        self._folder_combo.hide()

        self._tags_input = TagSuggestLineEdit(self._all_tags, self)
        self._tags_input.hide()

        outer.addWidget(panel)
        self._sync_fullscreen_button()

        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._on_save_shortcut)

        self._content_input.content_changed.connect(self._on_content_changed)
        self._title_input.textChanged.connect(self._on_any_field_changed)
        # _as_window title sync is connected after _build() in __init__
        self._tags_input.textChanged.connect(self._on_any_field_changed)
        self._tags_input.textChanged.connect(self._refresh_meta_pills)
        self._folder_combo.currentIndexChanged.connect(self._on_any_field_changed)
        self._folder_combo.currentIndexChanged.connect(self._refresh_meta_pills)

        self.setTabOrder(self._title_input, self._content_input)
        self._update_title_input_width()
        self._refresh_meta_pills()
        self.windowHandle().screenChanged.connect(lambda _: self._update_title_input_width()) if self.windowHandle() else None

    def resizeEvent(self, event):
        super().resizeEvent(event)

    _EDGE_LEFT = 1
    _EDGE_TOP = 2
    _EDGE_RIGHT = 4
    _EDGE_BOTTOM = 8
    # Valid single-edge and corner combinations accepted by startSystemResize
    _VALID_SYSTEM_RESIZE_EDGES = frozenset({1, 2, 4, 8, 3, 6, 12, 9})

    def _install_resize_filter(self):
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _contains_obj(self, obj) -> bool:
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _is_drag_target(self, obj) -> bool:
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if isinstance(widget, (QPushButton, QLineEdit, QToolButton)):
                return False
            if widget is self._drag_handle or widget is self._meta_handle:
                return True
            if widget is self:
                return False
            widget = widget.parentWidget()
        return False

    def _hit_test_edges(self, global_pos: QPoint) -> int:
        if self._is_zoomed() or not self.isVisible():
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

        self.move(x, y)
        self.resize(w, h)

    def _hide_snap_overlay(self):
        self._snap_zone = None
        self._snap_rect = None
        self._snap_overlay.hide()

    def eventFilter(self, obj, event):
        if not self._contains_obj(obj):
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QEvent.Type.MouseMove:
            global_pos = event.globalPosition().toPoint()
            if self._resizing:
                self._apply_resize(global_pos)
                return True
            if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._drag_from_maximized:
                    self._drag_from_maximized = False
                    self.showNormal()
                    normal_w = self._drag_handle.width()
                    offset_x = int(self._drag_norm_x * normal_w)
                    offset_x = max(20, min(offset_x, normal_w - 20))
                    self._drag_offset = QPoint(offset_x, self._drag_handle.height() // 2)
                elif self._drag_from_snapped:
                    self._drag_from_snapped = False
                    pre_geo = self._pre_snap_geometry
                    self._pre_snap_geometry = None
                    self.setGeometry(pre_geo)
                    normal_w = pre_geo.width()
                    offset_x = int(self._drag_norm_x * normal_w)
                    offset_x = max(20, min(offset_x, normal_w - 20))
                    self._drag_offset = QPoint(offset_x, self._drag_handle.height() // 2)
                    self.move(global_pos - self._drag_offset)
                else:
                    self.move(global_pos - self._drag_offset)
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
                return True
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._update_resize_cursor(global_pos)
        elif et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and not self._is_zoomed():
                global_pos = event.globalPosition().toPoint()
                edges = self._hit_test_edges(global_pos)
                if edges:
                    # Prefer native OS resize for cross-platform reliability
                    handle = self.windowHandle()
                    if (handle is not None
                            and edges in self._VALID_SYSTEM_RESIZE_EDGES
                            and handle.startSystemResize(Qt.Edge(edges))):
                        return True
                    # Manual fallback — skip on Windows: setGeometry fights DWM
                    if sys.platform == 'win32':
                        return True
                    self._resize_edges = edges
                    self._resizing = True
                    self._resize_start_pos = global_pos
                    self._resize_start_geo = self.geometry()
                    return True
                if self._is_drag_target(obj):
                    self._dragging = True
                    self._drag_offset = global_pos - self.frameGeometry().topLeft()
                    self._drag_from_maximized = False
                    if self._pre_snap_geometry is not None:
                        self._drag_from_snapped = True
                        local_x = self._drag_handle.mapFromGlobal(global_pos).x()
                        self._drag_norm_x = local_x / max(self._drag_handle.width(), 1)
                    else:
                        self._drag_from_snapped = False
                    self._snap_zone = None
                    self._snap_rect = None
                    return True
            elif event.button() == Qt.MouseButton.LeftButton and self._is_zoomed():
                global_pos = event.globalPosition().toPoint()
                if self._is_drag_target(obj):
                    self._dragging = True
                    local_x = self._drag_handle.mapFromGlobal(global_pos).x()
                    self._drag_norm_x = local_x / max(self._drag_handle.width(), 1)
                    self._drag_from_maximized = True
                    self._drag_from_snapped = False
                    self._snap_zone = None
                    self._snap_rect = None
                    return True
        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self._resizing:
                self._resizing = False
                self._update_resize_cursor(event.globalPosition().toPoint())
                return True
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
                        self.showMaximized()
                    else:
                        if self._pre_snap_geometry is None:
                            self._pre_snap_geometry = self.geometry()
                        self.setGeometry(snap_rect)
                else:
                    self._pre_snap_geometry = None
                return True

        return super().eventFilter(obj, event)

    def _center_on_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            ag = screen.availableGeometry()
            self.move(ag.center().x() - self.width() // 2, ag.center().y() - self.height() // 2)

    def _restore_ui_settings(self):
        fullscreen = self._settings.value(f"{self._settings_prefix}/fullscreen", False, type=bool)
        saved_size = self._settings.value(f"{self._settings_prefix}/size", QSize(1100, 760), type=QSize)
        if isinstance(saved_size, QSize) and saved_size.isValid() and not fullscreen:
            width = max(self.minimumWidth(), saved_size.width())
            height = max(self.minimumHeight(), saved_size.height())
            self.resize(width, height)
        if not fullscreen:
            saved_pos = self._settings.value(f"{self._settings_prefix}/pos", None, type=QPoint)
            if isinstance(saved_pos, QPoint) and self._pos_is_on_screen(saved_pos):
                self.move(saved_pos)
            else:
                self._center_on_screen()
        if fullscreen:
            self.showMaximized()
        self._sync_fullscreen_button()

    @staticmethod
    def _pos_is_on_screen(pos: QPoint) -> bool:
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(pos):
                return True
        return False

    def _save_ui_settings(self):
        self._settings.setValue(f"{self._settings_prefix}/fullscreen", self._is_zoomed())
        if not self._is_zoomed():
            self._settings.setValue(f"{self._settings_prefix}/size", self.size())
            self._settings.setValue(f"{self._settings_prefix}/pos", self.pos())
        self._settings.sync()

    def _is_zoomed(self) -> bool:
        return self.isMaximized() or self.isFullScreen()

    def _sync_fullscreen_button(self):
        if not hasattr(self, "_fullscreen_btn"):
            return
        self._fullscreen_btn.setText("🗗" if self._is_zoomed() else "⤢")

    def _toggle_fullscreen(self):
        if self._is_zoomed():
            self.showNormal()
            saved_size = self._settings.value(f"{self._settings_prefix}/size", QSize(1100, 760), type=QSize)
            if isinstance(saved_size, QSize) and saved_size.isValid():
                width = max(self.minimumWidth(), saved_size.width())
                height = max(self.minimumHeight(), saved_size.height())
                self.resize(width, height)
            saved_pos = self._settings.value(f"{self._settings_prefix}/pos", None, type=QPoint)
            if isinstance(saved_pos, QPoint) and self._pos_is_on_screen(saved_pos):
                self.move(saved_pos)
            else:
                self._center_on_screen()
        else:
            if not self._is_zoomed():
                self._settings.setValue(f"{self._settings_prefix}/size", self.size())
                self._settings.setValue(f"{self._settings_prefix}/pos", self.pos())
            self.showMaximized()
        self._save_ui_settings()
        self._sync_fullscreen_button()
        self._trigger_autosave("maximize")

    def _on_content_changed(self, _markdown: str = "") -> None:
        self._update_content_stats()
        self._mark_dirty_if_changed()

    def _on_any_field_changed(self):
        self._mark_dirty_if_changed()

    def _update_window_title_from_input(self):
        title = self._title_input.text().strip()
        self.setWindowTitle(title if title else "NoteStack")

    def _update_title_input_width(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return

        screen_width = screen.availableGeometry().width()
        target_width = max(320, int(screen_width * 0.4))
        self._title_input.setMinimumWidth(0)
        self._title_input.setMaximumWidth(target_width)

    def _parse_tags(self, raw_tags: str) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        for tag in re.findall(r"#([^\s#,]+)", raw_tags):
            normalized = tag.strip().lower().lstrip("#").rstrip(".,;:!?")
            if normalized and normalized not in seen:
                seen.add(normalized)
                tags.append(normalized)

        if not tags:
            for tag in raw_tags.split(","):
                normalized = tag.strip().lower().lstrip("#")
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    tags.append(normalized)
        return tags

    def _refresh_meta_pills(self):
        # Update compact folder button label
        folder_id = self._folder_combo.currentData()
        folder_name = self._folder_combo.currentText().strip()
        if folder_id is None:
            self._folder_btn.setText("Move to…")
            self._folder_btn.setToolTip("Choose folder")
        else:
            display = folder_name[:32] + ("…" if len(folder_name) > 32 else "")
            self._folder_btn.setText(display)
            self._folder_btn.setToolTip(folder_name)

        # Update compact tags button label
        tags = self._parse_tags(self._tags_input.text())
        if not tags:
            self._tags_btn.setText("Add tag…")
            self._tags_btn.setToolTip("Add tags")
        else:
            summary = "  ".join(f"#{t}" for t in tags[:4])
            if len(tags) > 4:
                summary += f"  +{len(tags) - 4}"
            self._tags_btn.setText(summary)
            self._tags_btn.setToolTip("  ".join(f"#{t}" for t in tags))

    def _update_folder_section_width(self):
        pass  # replaced by compact dropdown — no fixed-width panel

    def _update_tags_section_width(self):
        pass  # replaced by compact dropdown — no fixed-width panel

    def _open_folder_picker(self):
        menu = QMenu(self)
        for index in range(self._folder_combo.count()):
            action = menu.addAction(self._folder_combo.itemText(index))
            if action is not None:
                action.setData(index)
                if index == self._folder_combo.currentIndex():
                    action.setCheckable(True)
                    action.setChecked(True)
        picked = menu.exec(self._folder_btn.mapToGlobal(QPoint(0, self._folder_btn.height())))
        if picked is not None:
            self._folder_combo.setCurrentIndex(int(picked.data()))

    def _open_tags_editor(self):
        active_tags = set(self._parse_tags(self._tags_input.text()))
        menu = _TagCheckMenu(self)

        # Populate all known tags as checkable items
        sorted_tags = sorted(self._all_tags)
        for tag in sorted_tags:
            act = menu.addAction(f"#{tag}")
            if act is not None:
                act.setCheckable(True)
                act.setChecked(tag in active_tags)
                act.setData(tag)

        def _on_action(checked, tag=None):
            if tag is None:
                return
            current = set(self._parse_tags(self._tags_input.text()))
            if checked:
                current.add(tag)
            else:
                current.discard(tag)
            self._tags_input.setText("  ".join(f"#{t}" for t in sorted(current)))

        for act in menu.actions():
            tag = act.data()
            act.triggered.connect(lambda checked, t=tag: _on_action(checked, t))

        menu.exec(self._tags_btn.mapToGlobal(QPoint(0, self._tags_btn.height())))

    def _snapshot_state(self) -> tuple:
        title = self._title_input.text().strip()
        content = self._content_input.get_content()
        folder_id = self._folder_combo.currentData()
        tags = self._parse_tags(self._tags_input.text())
        return (title, content, folder_id, tuple(tags))

    def _initialize_save_tracking(self):
        self._last_saved_snapshot = self._snapshot_state()
        self._has_unsaved_changes = False
        self._set_save_state_indicator(is_unsaved=False, saved_recently=False)

    def _mark_dirty_if_changed(self):
        changed = self._snapshot_state() != self._last_saved_snapshot
        self._has_unsaved_changes = changed
        self._set_save_state_indicator(is_unsaved=changed, saved_recently=False)

    def _set_save_state_indicator(self, *, is_unsaved: bool, saved_recently: bool):
        if is_unsaved:
            self._save_state_dot.setStyleSheet(f"color:{self._SAVE_DOT_RED}; font-size:12px;")
            self._save_state_dot.setToolTip("Unsaved changes")
            return
        if saved_recently:
            self._save_state_dot.setStyleSheet(f"color:{self._SAVE_DOT_GREEN}; font-size:12px;")
            self._save_state_dot.setToolTip("Saved recently")
            return
        self._save_state_dot.setStyleSheet(f"color:{self._SAVE_DOT_IDLE}; font-size:12px;")
        self._save_state_dot.setToolTip("No pending changes")

    def _save_payload(self) -> dict | None:
        data = self._collect()
        if data:
            self.saved.emit(data)
            saved_id = data.get("id")
            if saved_id:
                self._editing = {"id": int(saved_id)}
            self._last_saved_snapshot = self._snapshot_state()
            self._has_unsaved_changes = False
            self._set_save_state_indicator(is_unsaved=False, saved_recently=True)
            return data
        return None

    def _trigger_autosave(self, reason: str):
        if reason not in {"minimize", "maximize", "paste"}:
            return
        if not self._has_unsaved_changes:
            return
        self._save_payload()

    def _update_content_stats(self):
        text = self._content_input.get_content()
        words = len(re.findall(r"\b\w+\b", text, re.UNICODE))
        chars = len(text)
        minutes = 0 if words == 0 else max(1, (words + 199) // 200)
        read_text = "0 min read" if minutes == 0 else f"{minutes} min read"
        self._stats_lbl.setText(f"{words} words • {chars} chars • {read_text}")

    def _add_section(self, layout, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("ModalSectionLabel")
        layout.addWidget(lbl)

    def _prefill(self, data: dict):
        self._title_input.setText(data.get("title", ""))
        self._content_input.set_content(data.get("content", ""))
        self._tags_input.setText(" ".join(f"#{t}" for t in data.get("tags", [])))

        folder_id = data.get("folder_id")
        if folder_id is not None:
            for i in range(self._folder_combo.count()):
                if self._folder_combo.itemData(i) == folder_id:
                    self._folder_combo.setCurrentIndex(i)
                    break

    def _apply_new_defaults(self):
        if self._default_folder_id is not None:
            for i in range(self._folder_combo.count()):
                if self._folder_combo.itemData(i) == self._default_folder_id:
                    self._folder_combo.setCurrentIndex(i)
                    break

        if self._default_tags:
            self._tags_input.setText(" ".join(f"#{t}" for t in self._default_tags))

        if self._default_content:
            self._content_input.set_content(self._default_content)

    def _collect(self) -> dict | None:
        title = self._title_input.text().strip()
        content = self._content_input.get_content().strip()
        if not content:
            return None
        tags = self._parse_tags(self._tags_input.text())

        folder_id = self._folder_combo.currentData()
        return {
            "id": self._editing["id"] if self._editing else None,
            "title": title,
            "content": content,
            "folder_id": folder_id,
            "tags": tags,
        }

    def _on_save(self):
        if self._save_payload():
            self.accept()

    def _on_save_shortcut(self):
        self._save_payload()

    def _on_delete(self):
        if self._editing:
            self.saved.emit({"__delete__": True, "id": self._editing["id"]})
            self.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if focused is self._title_input:
                self._content_input.focus_editor()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._has_unsaved_changes:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Unsaved changes")
            box.setText("You have unsaved edits.")
            box.setInformativeText("Save now? If you close without saving, this information will be destroyed.")
            save_btn = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(save_btn)
            box.exec()

            clicked = box.clickedButton()
            if clicked == cancel_btn:
                event.ignore()
                return

            if clicked == save_btn:
                if not self._save_payload():
                    event.ignore()
                    return

        self._save_ui_settings()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._trigger_autosave("minimize")
            elif self.isMaximized():
                self._trigger_autosave("maximize")
