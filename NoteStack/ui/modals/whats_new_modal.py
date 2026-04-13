"""
Version History modal for NoteStack.
"""
from __future__ import annotations

from pathlib import Path
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── Release history ──────────────────────────────────────────────────────────
# Newest entry first. Add a new dict at the top for each release.
_HISTORY: list[dict] = [
    {
        "version": "1.6.1",
        "date": "April 4, 2026",
        "items": [
            "Export all notes from Settings as JSON or TXT with full folder-path structure included.",
            "Folder hierarchy (nested subfolders) is preserved on export and restored on import using 'NoteStack/Folder/Subfolder' paths.",
            "Import is fully non-destructive — duplicate notes are skipped via content hashing; existing data is never overwritten.",
            "Export notes from selection as TXT, JSON, or copy straight to your clipboard — your notes, your format.",
            "Fix Windows tray icon display by loading PNG at explicit tray sizes.",
            "Note windows now use the actual note title for the taskbar/startbar hover title.",
            "Paste as new note — right-click on empty space inside a folder to instantly create a note from your clipboard.",
            "Cut, copy, and paste notes across folders and tags, just like files in a file manager.",
            "Subfolder support — drag and drop folders onto each other to build a nested hierarchy that suits your workflow.",
            "Ctrl+click to select multiple notes at once, then act on all of them together.",
            "Trash bin — deleted notes land in Trash instead of disappearing. Each note shows a countdown to permanent deletion, and Trash clears itself automatically after 48 hours.",
            "Undo delete — restore a note you just deleted with a single undo action.",
        ],
    },
]


class VersionHistoryModal(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(680)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 24, 30, 24)
        v.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()

        icon = QLabel("✦")
        icon.setObjectName("ModalIconAccent")

        title = QLabel("Version History")
        title.setObjectName("ModalTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.accept)

        header.addWidget(icon)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)
        v.addSpacing(16)

        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)
        v.addSpacing(14)

        # ── Scrollable history ───────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(400)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(0)

        current_version = self._resolve_current_version()

        for idx, entry in enumerate(_HISTORY):
            ver = entry["version"]
            date = entry.get("date", "")
            items = entry.get("items", [])
            is_current = (ver == current_version)

            # Separator between releases
            if idx > 0:
                sep = QFrame()
                sep.setObjectName("Divider")
                body_layout.addSpacing(18)
                body_layout.addWidget(sep)
                body_layout.addSpacing(18)

            # ── Version header row ───────────────────────────────────────────
            ver_row = QHBoxLayout()
            ver_row.setSpacing(8)
            ver_row.setContentsMargins(0, 0, 0, 0)

            ver_lbl = QLabel(f"v{ver}")
            ver_lbl.setObjectName("ModalSectionLabel")
            ver_row.addWidget(ver_lbl)

            if is_current:
                badge = QLabel("CURRENT")
                badge.setObjectName("VersionCurrentBadge")
                ver_row.addWidget(badge)

            ver_row.addStretch()

            if date:
                date_lbl = QLabel(date)
                date_lbl.setObjectName("ContentSubtitle")
                ver_row.addWidget(date_lbl)

            body_layout.addLayout(ver_row)
            body_layout.addSpacing(10)

            # ── Bullet items ─────────────────────────────────────────────────
            for text in items:
                lbl = QLabel(f"• {text}")
                lbl.setWordWrap(True)
                lbl.setObjectName("ContentSubtitle")
                body_layout.addWidget(lbl)
                body_layout.addSpacing(5)

        body_layout.addStretch()
        scroll.setWidget(body)
        v.addWidget(scroll)

        v.addSpacing(14)

        footer = QHBoxLayout()
        footer.addStretch()
        done_btn = QPushButton("Done")
        done_btn.setObjectName("BtnSecondary")
        done_btn.setFixedHeight(36)
        done_btn.setFixedWidth(90)
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)
        v.addLayout(footer)

        outer.addWidget(panel)

    def _resolve_current_version(self) -> str:
        default_version = _HISTORY[0]["version"] if _HISTORY else "1.0.0"
        repo_root = Path(__file__).resolve().parents[3]
        version_info_path = repo_root / "build" / "artifacts" / "version_info.txt"
        if version_info_path.exists():
            try:
                text = version_info_path.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)", text)
                if match:
                    return match.group(1).strip()
            except OSError:
                pass

        build_script_path = repo_root / "build_for_windows" / "build.ps1"
        if build_script_path.exists():
            try:
                text = build_script_path.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"\[string\]\$Version\s*=\s*\"([^\"]+)\"", text)
                if match:
                    return match.group(1).strip()
            except OSError:
                pass

        return default_version


# Backward-compatible alias kept for any lingering references
WhatsNewModal = VersionHistoryModal
