"""
Prompt Detail modal — full-screen card viewer with copy button.
"""
from __future__ import annotations
import sys
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QApplication, QSizePolicy, QWidget,
)
from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from ui.tui_editor_widget import TuiEditorWidget
from ui.snap_utils import SnapOverlay, get_snap_zone
from ui.styles import get_current_theme


def _fmt_dt(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%B %d, %Y  %H:%M")
    except Exception:
        return raw


class PromptDetailModal(QDialog):
    edit_requested = pyqtSignal(int)

    def __init__(self, data: dict, as_window: bool = False, parent=None):
        super().__init__(parent)
        self._data = data
        self._as_window = as_window
        self._settings = QSettings()
        self._settings_prefix = "ui/modals/prompt_detail"
        self._resize_margin = 6
        self._resize_edges = 0
        self._resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_geo = self.geometry()
        base_window_type = Qt.WindowType.Window if self._as_window else Qt.WindowType.Dialog
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | base_window_type)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(not self._as_window)
        self.setMinimumSize(660, 420)
        self._dragging = False
        self._drag_offset = QPoint()
        self._drag_from_maximized = False
        self._drag_from_snapped = False
        self._drag_norm_x = 0.5
        self._pre_snap_geometry: QRect | None = None
        self._snap_zone: str | None = None
        self._snap_rect: QRect | None = None
        self._snap_overlay = SnapOverlay()
        self._build()
        self._restore_ui_settings()
        self._install_resize_filter()
        if self._as_window:
            self.setWindowTitle(self._data.get("title", "") or "NoteStack")

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
        title = QLabel(self._data.get("title", ""))
        title.setObjectName("ModalTitle")
        title.setWordWrap(True)

        self._fullscreen_btn = QPushButton("⤢")
        self._fullscreen_btn.setObjectName("ModalIconBtn")
        self._fullscreen_btn.setFixedSize(28, 28)
        self._fullscreen_btn.setToolTip("Toggle fullscreen")
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)

        self._minimize_btn = QPushButton("🗕")
        self._minimize_btn.setObjectName("ModalIconBtn")
        self._minimize_btn.setFixedSize(28, 28)
        self._minimize_btn.setToolTip("Minimize")
        self._minimize_btn.clicked.connect(self.showMinimized)

        close = QPushButton("✕")
        close.setObjectName("CloseBtn")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.reject)

        hdr.addWidget(title, 1)
        hdr.addWidget(self._minimize_btn)
        hdr.addWidget(self._fullscreen_btn)
        hdr.addWidget(close)
        v.addWidget(self._drag_handle)
        v.addSpacing(10)

        # Meta row: folder • date
        meta_parts = []
        if self._data.get("folder_name"):
            meta_parts.append(f"📁 {self._data['folder_name']}")
        if self._data.get("created_at"):
            meta_parts.append(f"🗓 {_fmt_dt(self._data['created_at'])}")

        meta = QLabel("   •   ".join(meta_parts))
        meta.setObjectName("CardDate")
        meta.setStyleSheet("font-size:11px; color:#52526A; padding-bottom:4px;")
        v.addWidget(meta)
        v.addSpacing(6)

        # Tags
        if self._data.get("tags"):
            tag_row = QHBoxLayout()
            tag_row.setSpacing(6)
            for t in self._data["tags"]:
                chip = QLabel(t.upper())
                chip.setObjectName("CardTagLabel")
                chip.setFixedHeight(22)
                tag_row.addWidget(chip)
            tag_row.addStretch()
            v.addLayout(tag_row)
            v.addSpacing(12)

        div = QFrame(); div.setObjectName("Divider")
        v.addWidget(div)
        v.addSpacing(16)

        # Content — read-only TUI viewer (renders Markdown)
        self._content_box = TuiEditorWidget(
            theme=get_current_theme(),
            viewer=True,
            parent=self,
        )
        self._content_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_box.setMinimumHeight(200)
        self._content_box.set_content(self._data.get("content", ""))
        v.addWidget(self._content_box, 1)
        v.addSpacing(22)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(10)
        btns.addStretch()

        copy_btn = QPushButton("📋  Copy")
        copy_btn.setObjectName("BtnSecondary")
        copy_btn.setFixedHeight(40)
        copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_btn.clicked.connect(self._copy)

        edit_btn = QPushButton("✏️  Edit")
        edit_btn.setObjectName("BtnPrimary")
        edit_btn.setFixedHeight(40)
        edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        edit_btn.clicked.connect(self._edit)

        btns.addWidget(copy_btn)
        btns.addWidget(edit_btn)
        v.addLayout(btns)

        outer.addWidget(panel)
        self._sync_fullscreen_button()

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
            if isinstance(widget, QPushButton):
                return False
            if widget is self._drag_handle:
                return True
            if widget is self:
                return False
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
            if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
                global_pos = event.globalPosition().toPoint()
                edges = self._hit_test_edges(global_pos)
                if edges:
                    # Prefer native OS resize to avoid Aero Snap / geometry issues
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
            elif event.button() == Qt.MouseButton.LeftButton and self.isMaximized():
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
        saved_size = self._settings.value(f"{self._settings_prefix}/size", QSize(820, 640), type=QSize)
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
        self._settings.setValue(f"{self._settings_prefix}/fullscreen", self.isMaximized())
        if not self.isMaximized():
            self._settings.setValue(f"{self._settings_prefix}/size", self.size())
            self._settings.setValue(f"{self._settings_prefix}/pos", self.pos())
        self._settings.sync()

    def _sync_fullscreen_button(self):
        if not hasattr(self, "_fullscreen_btn"):
            return
        self._fullscreen_btn.setText("🗗" if self.isMaximized() else "⤢")

    def _toggle_fullscreen(self):
        if self.isMaximized():
            self.showNormal()
            saved_size = self._settings.value(f"{self._settings_prefix}/size", QSize(820, 640), type=QSize)
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
            if not self.isMaximized():
                self._settings.setValue(f"{self._settings_prefix}/size", self.size())
                self._settings.setValue(f"{self._settings_prefix}/pos", self.pos())
            self.showMaximized()
        self._save_ui_settings()
        self._sync_fullscreen_button()

    def _confirm_close_with_unsaved_changes(self) -> bool:
        return True

    def reject(self):
        if not self._confirm_close_with_unsaved_changes():
            return
        self._save_ui_settings()
        super().reject()

    def closeEvent(self, event):
        if not self._confirm_close_with_unsaved_changes():
            event.ignore()
            return
        self._save_ui_settings()
        super().closeEvent(event)

    def _copy(self):
        content = self._data.get("content", "")
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(content)

    def _edit(self):
        self.edit_requested.emit(self._data["id"])
        self.accept()
