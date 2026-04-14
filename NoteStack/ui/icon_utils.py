"""
Utility helpers for loading icons from resources/.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap


def resources_dir() -> Path:
    # icon_utils.py lives at {root}/NoteStack/ui/icon_utils.py in both dev and
    # installed layout, so parents[2] is always the root where resources/ lives.
    return Path(__file__).resolve().parents[2] / "resources"


def _icons_dir() -> Path:
    return resources_dir() / "icons"


def make_png_icon(path: Path, sizes: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 128, 256)) -> QIcon:
    src = QPixmap(str(path))
    if src.isNull():
        return QIcon()

    icon = QIcon()
    for size in sizes:
        scaled = src.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        icon.addPixmap(scaled)
    return icon


def make_icon(name: str, color: str, size: int = 16) -> QIcon:
    """Load a PNG from resources/icons/, tint it, and return as QIcon.

    The source PNG should be a dark/black icon on a transparent background.
    Opaque pixels are replaced with *color*. Returns an empty QIcon if the
    file is missing or fails to load.
    """
    path = _icons_dir() / name
    src = QPixmap(str(path))
    if src.isNull():
        return QIcon()
    src = src.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = QPixmap(src.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, src)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QColor(color))
    painter.end()
    return QIcon(result)


def make_pixmap(name: str, color: str, size: int = 14) -> QPixmap:
    """Convenience wrapper returning a tinted QPixmap instead of a QIcon."""
    icon = make_icon(name, color, size)
    return icon.pixmap(size, size)
