"""
Shared snap-to-side utilities used by the main window and floating modals.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget


_SNAP_THRESHOLD = 20  # px from screen edge that triggers a snap zone


def get_snap_zone(cursor_pos: QPoint, threshold: int = _SNAP_THRESHOLD):
    """
    Return ``(zone_name, QRect)`` for the snap target at *cursor_pos*, or
    ``(None, None)`` if the cursor is not near any screen edge.

    Zone names: ``"left"``, ``"right"``, ``"maximize"``,
                ``"top_left"``, ``"top_right"``,
                ``"bottom_left"``, ``"bottom_right"``.
    """
    screen = QApplication.screenAt(cursor_pos)
    if screen is None:
        return None, None
    avail = screen.availableGeometry()
    x, y = cursor_pos.x(), cursor_pos.y()
    t = threshold
    ax, ay, aw, ah = avail.x(), avail.y(), avail.width(), avail.height()

    # Corners take priority over straight edges
    if x <= ax + t and y <= ay + t:
        return "top_left", QRect(ax, ay, aw // 2, ah // 2)
    if x >= ax + aw - t - 1 and y <= ay + t:
        return "top_right", QRect(ax + aw // 2, ay, aw // 2, ah // 2)
    if x <= ax + t and y >= ay + ah - t - 1:
        return "bottom_left", QRect(ax, ay + ah // 2, aw // 2, ah // 2)
    if x >= ax + aw - t - 1 and y >= ay + ah - t - 1:
        return "bottom_right", QRect(ax + aw // 2, ay + ah // 2, aw // 2, ah // 2)

    # Straight edges
    if y <= ay + t:
        return "maximize", QRect(avail)
    if x <= ax + t:
        return "left", QRect(ax, ay, aw // 2, ah)
    if x >= ax + aw - t - 1:
        return "right", QRect(ax + aw // 2, ay, aw // 2, ah)

    return None, None


class SnapOverlay(QWidget):
    """Semi-transparent overlay that previews the snap target rectangle."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(0, 120, 215, 70))
        painter.setPen(QColor(0, 100, 200, 180))
        painter.drawRoundedRect(self.rect().adjusted(4, 4, -4, -4), 8, 8)
