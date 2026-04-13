"""
About modal for NoteStack.
"""
from __future__ import annotations

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


_ABOUT_HEADER = [
    "The Ephemeral Tech Manifesto",
    "Toward Stable, Predictable, and Self‑Contained Software",
    "Author: Asesh Basu (asesh.basu.dev@gmail.com)",
    "On: April, 2026.",
]

_ABOUT_SECTIONS: list[tuple[str, list[str]]] = [
    (
        "Preamble",
        [
            "Frequent software changes can disrupt workflows, impose user effort, and create uncertainty about long‑term data access.",
            "This document proposes an alternative: design for stability, backward compatibility, and user autonomy.",
        ],
    ),
    (
        "Design Principles",
        [
            "Core workflows never break – Learn once, use for years.",
            "Old files always open – No migrations. No data loss. Ever.",
            "Offline operation – Full functionality without internet. No account, remote server, or network authentication required.",
            "No telemetry, No spying – No telemetry. No hidden network calls.",
            "UI stays consistent – No redesigns that ruin muscle memory.",
            "Performance stability – No degradation across versions. If it slows down, it's a bug.",
            "No bloat – Features earn their place.",
            "Feature‑complete base product – All functionality included. Only paid service is online storage (vendor or third‑party, e.g., Google Drive, Dropbox). No paywalls for features.",
        ],
    ),
    (
        "Implications for Users",
        [
            "A tool that works the same in v1.0 and v1.7.",
            "Files that never become obsolete.",
            "Software you don't have to think about.",
        ],
    ),
    (
        "Why this exists",
        [
            "Modern software companies must balance shareholder expectations with the delivery of software features.",
            "This balance often incentivizes continuous change: new features, redesigned interfaces, and altered workflows, regardless of user need.",
            "Such change is not accidental but a structural response to market pressures.",
            "This manifesto codifies an alternative model—one that prioritizes long‑term usability and user control over feature velocity and vendor‑driven evolution.",
        ],
    ),
]


class AboutModal(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(760, 540)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")

        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 24, 30, 24)
        v.setSpacing(0)

        header = QHBoxLayout()

        title = QLabel("About")
        title.setObjectName("ModalTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.accept)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)
        v.addSpacing(16)

        divider = QFrame()
        divider.setObjectName("Divider")
        v.addWidget(divider)
        v.addSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(400)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(0)

        manifesto_title = QLabel(_ABOUT_HEADER[0])
        manifesto_title.setObjectName("ModalSectionLabel")
        body_layout.addWidget(manifesto_title)
        body_layout.addSpacing(10)

        for line in _ABOUT_HEADER[1:]:
            meta_lbl = QLabel(line)
            meta_lbl.setWordWrap(True)
            meta_lbl.setObjectName("ContentSubtitle")
            body_layout.addWidget(meta_lbl)
            body_layout.addSpacing(4)

        for idx, (section_title, paragraphs) in enumerate(_ABOUT_SECTIONS):
            body_layout.addSpacing(18)

            if idx > 0:
                sep = QFrame()
                sep.setObjectName("Divider")
                body_layout.addWidget(sep)
                body_layout.addSpacing(18)

            if section_title:
                section_lbl = QLabel(section_title)
                section_lbl.setObjectName("ModalSectionLabel")
                body_layout.addWidget(section_lbl)
                body_layout.addSpacing(10)

            for paragraph in paragraphs:
                paragraph_lbl = QLabel(paragraph)
                paragraph_lbl.setWordWrap(True)
                paragraph_lbl.setObjectName("ContentSubtitle")
                body_layout.addWidget(paragraph_lbl)
                body_layout.addSpacing(8)

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