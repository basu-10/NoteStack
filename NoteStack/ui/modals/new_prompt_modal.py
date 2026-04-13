"""
New / Edit Note modal.
"""
from __future__ import annotations
import re
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QComboBox, QFrame, QWidget, QCompleter,
    QCheckBox,
    QToolButton, QInputDialog, QSizePolicy, QApplication, QFileDialog, QColorDialog, QMenu, QMessageBox,
    QScrollArea,
    QSpinBox,
)
from PyQt6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, pyqtSignal, QStringListModel, QBuffer, QIODevice, QMimeData, QByteArray
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QImage, QKeySequence, QPixmap, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextListFormat, QTextTableFormat, QBrush
from ui.modals.outline_modal import OutlineModal, extract_headings_from_document
from ui.flow_layout import FlowLayout
from ui.icon_utils import make_icon
from ui.snap_utils import SnapOverlay, get_snap_zone


def _looks_like_rich_text(content: str) -> bool:
    lowered = content.lower()
    return any(tag in lowered for tag in ("<html", "<body", "<p", "<div", "<span", "<img", "<table", "<ul", "<ol"))


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


class RichTextEdit(QTextEdit):
    pasted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self._image_width = 480
        self._image_wrap = "inline"
        self._image_border_width = 0
        self._image_border_color = "#EAF0FF"
        self._image_crop = (0, 0, 0, 0)

    def set_image_insert_options(
        self,
        width: int,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] | None = None,
    ):
        self._image_width = max(40, int(width))
        self._image_wrap = wrap_mode if wrap_mode in ("inline", "left", "right") else "inline"
        self._image_border_width = max(0, int(border_width))
        color = QColor(border_color)
        self._image_border_color = color.name() if color.isValid() else "#EAF0FF"
        if crop_factors is None:
            self._image_crop = (0, 0, 0, 0)
        else:
            top, right, bottom, left = crop_factors
            self._image_crop = (
                max(0, int(top)),
                max(0, int(right)),
                max(0, int(bottom)),
                max(0, int(left)),
            )

    def image_insert_defaults(self) -> tuple[int, str, int, str, tuple[int, int, int, int]]:
        return (
            self._image_width,
            self._image_wrap,
            self._image_border_width,
            self._image_border_color,
            self._image_crop,
        )

    def _crop_image(self, image: QImage, crop_factors: tuple[int, int, int, int]) -> QImage:
        top, right, bottom, left = crop_factors
        if top <= 0 and right <= 0 and bottom <= 0 and left <= 0:
            return image

        width = image.width()
        height = image.height()
        if width <= 1 or height <= 1:
            return image

        left_px = int(round(width * max(0, left) / 100.0))
        right_px = int(round(width * max(0, right) / 100.0))
        top_px = int(round(height * max(0, top) / 100.0))
        bottom_px = int(round(height * max(0, bottom) / 100.0))

        crop_width = max(1, width - left_px - right_px)
        crop_height = max(1, height - top_px - bottom_px)
        origin_x = min(max(0, left_px), max(0, width - 1))
        origin_y = min(max(0, top_px), max(0, height - 1))

        return image.copy(origin_x, origin_y, crop_width, crop_height)

    def crop_qimage(self, image: QImage, crop_factors: tuple[int, int, int, int]) -> QImage:
        return self._crop_image(image, crop_factors)

    def _insert_qimage(
        self,
        image: QImage,
        width: int,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] | None = None,
    ):
        if image.isNull():
            return
        width = max(40, int(width))
        wrap_mode = wrap_mode if wrap_mode in ("inline", "left", "right") else "inline"
        border_width = max(0, int(border_width))
        color = QColor(border_color)
        border_color = color.name() if color.isValid() else "#EAF0FF"
        crop = crop_factors or (0, 0, 0, 0)

        prepared = self._crop_image(image, crop)

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        prepared.save(buffer, "PNG")
        encoded = buffer.data().toBase64().data().decode("ascii")
        style = self._image_style(wrap_mode, border_width, border_color, crop)
        html = f'<img src="data:image/png;base64,{encoded}" width="{width}"{style} />'
        self.textCursor().insertHtml(html)

    def _image_style(
        self,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> str:
        declarations: list[str] = []
        if wrap_mode == "left":
            declarations.extend(["float:left", "margin:0 12px 8px 0"])
        elif wrap_mode == "right":
            declarations.extend(["float:right", "margin:0 0 8px 12px"])

        if border_width > 0:
            declarations.append(f"border:{max(0, int(border_width))}px solid {border_color}")

        top, right, bottom, left = crop_factors
        declarations.append(f"--ns-crop-top:{max(0, int(top))}%")
        declarations.append(f"--ns-crop-right:{max(0, int(right))}%")
        declarations.append(f"--ns-crop-bottom:{max(0, int(bottom))}%")
        declarations.append(f"--ns-crop-left:{max(0, int(left))}%")

        if not declarations:
            return ""
        return f' style="{"; ".join(declarations)};"'

    def _image_html_from_src(
        self,
        src: str,
        width: int,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> str:
        return (
            f'<img src="{src}" width="{max(40, int(width))}"'
            f'{self._image_style(wrap_mode, border_width, border_color, crop_factors)} />'
        )

    def insert_qimage(
        self,
        image: QImage,
        width: int | None = None,
        wrap_mode: str | None = None,
        border_width: int | None = None,
        border_color: str | None = None,
        crop_factors: tuple[int, int, int, int] | None = None,
    ):
        self._insert_qimage(
            image,
            width or self._image_width,
            wrap_mode or self._image_wrap,
            self._image_border_width if border_width is None else border_width,
            self._image_border_color if border_color is None else border_color,
            self._image_crop if crop_factors is None else crop_factors,
        )

    def image_cursor_for_position(self, pos: QPoint) -> QTextCursor | None:
        cursor = self.cursorForPosition(pos)
        if cursor.charFormat().isImageFormat():
            return cursor

        probe = QTextCursor(cursor)
        if probe.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 1):
            if probe.charFormat().isImageFormat():
                probe.clearSelection()
                probe.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 1)
                return probe
        return None

    def image_data_at_cursor(self, cursor: QTextCursor) -> tuple[str, int] | None:
        image_cursor = self._image_object_cursor(cursor)
        if image_cursor is None:
            return None

        probe = QTextCursor(image_cursor)
        if not probe.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            1,
        ):
            return None

        img_fmt = probe.charFormat().toImageFormat()
        src = img_fmt.name() or ""
        if not src:
            return None
        width = int(img_fmt.width()) if img_fmt.width() > 0 else self._image_width
        return src, max(40, width)

    def _image_object_cursor(self, cursor: QTextCursor) -> QTextCursor | None:
        doc = self.document()
        if doc is None:
            return None

        pos = cursor.position()

        if pos < doc.characterCount():
            probe = QTextCursor(doc)
            probe.setPosition(pos)
            if probe.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.KeepAnchor,
                1,
            ) and probe.charFormat().isImageFormat():
                out = QTextCursor(doc)
                out.setPosition(pos)
                return out

        if pos > 0:
            probe = QTextCursor(doc)
            probe.setPosition(pos - 1)
            if probe.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.KeepAnchor,
                1,
            ) and probe.charFormat().isImageFormat():
                out = QTextCursor(doc)
                out.setPosition(pos - 1)
                return out

        return None

    def _image_tag_at_cursor(self, cursor: QTextCursor) -> str | None:
        image_cursor = self._image_object_cursor(cursor)
        if image_cursor is None:
            return None

        probe = QTextCursor(image_cursor)
        if not probe.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            1,
        ):
            return None

        fragment = probe.selection().toHtml()
        match = re.search(r"<img\\b[^>]*>", fragment, flags=re.IGNORECASE)
        return match.group(0) if match else None

    def image_options_at_cursor(self, cursor: QTextCursor) -> dict | None:
        info = self.image_data_at_cursor(cursor)
        if info is None:
            return None

        src, width = info
        wrap_mode = "inline"
        border_width = 0
        border_color = "#EAF0FF"
        crop = (0, 0, 0, 0)

        tag = self._image_tag_at_cursor(cursor)
        if tag:
            style_match = re.search(
                r"\\sstyle\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)')",
                tag,
                flags=re.IGNORECASE,
            )
            style = ""
            if style_match:
                style = style_match.group(1) or style_match.group(2) or ""
                lowered = style.lower()
                if "float:left" in lowered:
                    wrap_mode = "left"
                elif "float:right" in lowered:
                    wrap_mode = "right"

                border_match = re.search(
                    r"border\\s*:\\s*(\\d+)px\\s+solid\\s+([^;]+)",
                    style,
                    flags=re.IGNORECASE,
                )
                if border_match:
                    border_width = max(0, int(border_match.group(1)))
                    color = QColor(border_match.group(2).strip())
                    if color.isValid():
                        border_color = color.name()

                def crop_value(prop: str) -> int:
                    match = re.search(
                        rf"--ns-crop-{prop}\\s*:\\s*(\\d+)%",
                        style,
                        flags=re.IGNORECASE,
                    )
                    return max(0, int(match.group(1))) if match else 0

                crop = (
                    crop_value("top"),
                    crop_value("right"),
                    crop_value("bottom"),
                    crop_value("left"),
                )

        return {
            "src": src,
            "width": max(40, int(width)),
            "wrap_mode": wrap_mode,
            "border_width": border_width,
            "border_color": border_color,
            "crop_factors": crop,
        }

    def replace_image_at_cursor(
        self,
        cursor: QTextCursor,
        src: str,
        width: int,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] = (0, 0, 0, 0),
    ):
        if not src:
            return
        work = self._image_object_cursor(cursor)
        if work is None:
            return
        if not work.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            1,
        ):
            return

        work.beginEditBlock()
        work.removeSelectedText()
        work.insertHtml(
            self._image_html_from_src(
                src,
                width,
                wrap_mode,
                border_width,
                border_color,
                crop_factors,
            )
        )
        work.endEditBlock()

    def remove_image_at_cursor(self, cursor: QTextCursor):
        work = self._image_object_cursor(cursor)
        if work is None:
            return
        if work.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            1,
        ) and work.charFormat().isImageFormat():
            work.removeSelectedText()

    def paste_plain_text(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        mime = clipboard.mimeData()
        if mime is not None and mime.hasText():
            self.insertPlainText(mime.text())
            self.pasted.emit()
            return

        text = clipboard.text()
        if text:
            self.insertPlainText(text)
            self.pasted.emit()

    def paste_without_theme_colors(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        mime = clipboard.mimeData()
        if mime is not None and mime.hasHtml():
            cleaned_html = self._sanitize_html_theme_colors(mime.html())
            if cleaned_html.strip():
                self._insert_html_without_theme_colors(cleaned_html)
                self.pasted.emit()
                return

        self.paste_plain_text()

    def _sanitize_html_theme_colors(self, html: str) -> str:
        if not html:
            return ""

        # Drop style blocks to avoid importing page-level dark mode CSS.
        cleaned = re.sub(r"<style\\b[^>]*>.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)

        def clean_style_attr(match: re.Match) -> str:
            style_content = match.group(2)
            declarations = [d.strip() for d in style_content.split(";") if d.strip()]
            kept: list[str] = []
            blocked_props = {
                "color",
                "background",
                "background-color",
                "caret-color",
                "text-fill-color",
                "-webkit-text-fill-color",
                "-webkit-text-stroke-color",
                "fill",
                "stroke",
            }

            for decl in declarations:
                if ":" not in decl:
                    continue
                prop, value = decl.split(":", 1)
                prop_name = prop.strip().lower()
                if prop_name in blocked_props:
                    continue
                if "background" in prop_name:
                    continue
                kept.append(f"{prop.strip()}: {value.strip()}")

            if not kept:
                return ""

            quote = match.group(1)
            return f' style={quote}{"; ".join(kept)}{quote}'

        cleaned = re.sub(
            r"\\sstyle\\s*=\\s*([\"'])(.*?)\\1",
            clean_style_attr,
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Remove legacy HTML color attributes often present in copied web snippets.
        cleaned = re.sub(
            r"\\s(?:bgcolor|color|text|link|vlink|alink)\\s*=\\s*(?:\"[^\"]*\"|'[^']*'|[^\\s>]+)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        # Remove helper attributes often injected by website themes/extensions.
        cleaned = re.sub(
            r"\\s(?:class|id|data-[a-z0-9_:-]+|aria-[a-z0-9_:-]+)\\s*=\\s*(?:\"[^\"]*\"|'[^']*'|[^\\s>]+)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    def _insert_html_without_theme_colors(self, html: str):
        cursor = self.textCursor()
        start = cursor.position()
        cursor.insertHtml(html)
        end = self.textCursor().position()
        if end <= start:
            return

        selection = QTextCursor(self.document())
        selection.setPosition(start)
        selection.setPosition(end, QTextCursor.MoveMode.KeepAnchor)

        # Keep formatting, but force editor-native text/background colors.
        char_fmt = QTextCharFormat()
        char_fmt.setForeground(self.palette().text())
        char_fmt.setBackground(QBrush())
        selection.mergeCharFormat(char_fmt)

        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QBrush())
        selection.mergeBlockFormat(block_fmt)

    def canInsertFromMimeData(self, source: QMimeData | None) -> bool:
        if source is None:
            return False
        if source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData | None):
        if source is None:
            return
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if isinstance(image, QImage) and not image.isNull():
                self._insert_qimage(
                    image,
                    self._image_width,
                    self._image_wrap,
                    self._image_border_width,
                    self._image_border_color,
                    self._image_crop,
                )
                self.pasted.emit()
                return
        super().insertFromMimeData(source)
        self.pasted.emit()


class ImageOptionsModal(QDialog):
    def __init__(
        self,
        *,
        width: int,
        wrap_mode: str,
        border_width: int = 0,
        border_color: str = "#EAF0FF",
        crop_factors: tuple[int, int, int, int] = (0, 0, 0, 0),
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(420)

        self._result: tuple[int, str, int, str, tuple[int, int, int, int]] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("ModalPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Edit Image")
        title.setObjectName("ModalTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        v.addLayout(header)

        self._width_spin = QSpinBox()
        self._width_spin.setObjectName("ModalInput")
        self._width_spin.setRange(40, 4000)
        self._width_spin.setValue(max(40, int(width)))
        self._width_spin.setSingleStep(10)

        self._wrap_combo = QComboBox()
        self._wrap_combo.setObjectName("ModalCombo")
        self._wrap_combo.addItem("inline")
        self._wrap_combo.addItem("left")
        self._wrap_combo.addItem("right")
        current = wrap_mode if wrap_mode in ("inline", "left", "right") else "inline"
        self._wrap_combo.setCurrentText(current)

        self._border_width_spin = QSpinBox()
        self._border_width_spin.setObjectName("ModalInput")
        self._border_width_spin.setRange(0, 40)
        self._border_width_spin.setValue(max(0, int(border_width)))

        self._border_color = QColor(border_color if QColor(border_color).isValid() else "#EAF0FF")
        self._border_color_btn = QPushButton(self._border_color.name())
        self._border_color_btn.setObjectName("BtnSecondary")
        self._border_color_btn.setFixedHeight(34)
        self._border_color_btn.clicked.connect(self._pick_border_color)
        self._apply_border_color_preview()

        top, right, bottom, left = crop_factors
        self._crop_top_spin = QSpinBox()
        self._crop_top_spin.setObjectName("ModalInput")
        self._crop_top_spin.setRange(0, 95)
        self._crop_top_spin.setValue(max(0, int(top)))

        self._crop_right_spin = QSpinBox()
        self._crop_right_spin.setObjectName("ModalInput")
        self._crop_right_spin.setRange(0, 95)
        self._crop_right_spin.setValue(max(0, int(right)))

        self._crop_bottom_spin = QSpinBox()
        self._crop_bottom_spin.setObjectName("ModalInput")
        self._crop_bottom_spin.setRange(0, 95)
        self._crop_bottom_spin.setValue(max(0, int(bottom)))

        self._crop_left_spin = QSpinBox()
        self._crop_left_spin.setObjectName("ModalInput")
        self._crop_left_spin.setRange(0, 95)
        self._crop_left_spin.setValue(max(0, int(left)))

        v.addWidget(QLabel("WIDTH (PX)"))
        v.addWidget(self._width_spin)
        v.addWidget(QLabel("WRAP MODE"))
        v.addWidget(self._wrap_combo)
        v.addWidget(QLabel("BORDER WIDTH (PX)"))
        v.addWidget(self._border_width_spin)
        v.addWidget(QLabel("BORDER COLOR"))
        v.addWidget(self._border_color_btn)
        v.addWidget(QLabel("CROP TOP (%)"))
        v.addWidget(self._crop_top_spin)
        v.addWidget(QLabel("CROP RIGHT (%)"))
        v.addWidget(self._crop_right_spin)
        v.addWidget(QLabel("CROP BOTTOM (%)"))
        v.addWidget(self._crop_bottom_spin)
        v.addWidget(QLabel("CROP LEFT (%)"))
        v.addWidget(self._crop_left_spin)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("BtnSecondary")
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("BtnPrimary")
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(cancel)
        btn_row.addWidget(apply_btn)
        v.addLayout(btn_row)

        outer.addWidget(panel)

    def _apply_border_color_preview(self):
        self._border_color_btn.setText(self._border_color.name())
        self._border_color_btn.setStyleSheet(
            f"text-align:left; padding-left:10px; border:2px solid {self._border_color.name()};"
        )

    def _pick_border_color(self):
        color = QColorDialog.getColor(self._border_color, self, "Select Border Color")
        if not color.isValid():
            return
        self._border_color = color
        self._apply_border_color_preview()

    def _normalized_crop(self) -> tuple[int, int, int, int] | None:
        top = self._crop_top_spin.value()
        right = self._crop_right_spin.value()
        bottom = self._crop_bottom_spin.value()
        left = self._crop_left_spin.value()

        if top + bottom >= 100:
            QMessageBox.warning(self, "Invalid Crop", "Top + Bottom crop must be less than 100%.")
            return None
        if left + right >= 100:
            QMessageBox.warning(self, "Invalid Crop", "Left + Right crop must be less than 100%.")
            return None
        return top, right, bottom, left

    def _on_apply(self):
        crop = self._normalized_crop()
        if crop is None:
            return
        self._result = (
            self._width_spin.value(),
            self._wrap_combo.currentText(),
            self._border_width_spin.value(),
            self._border_color.name(),
            crop,
        )
        self.accept()

    def result_options(self) -> tuple[int, str, int, str, tuple[int, int, int, int]] | None:
        return self._result


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
        self._outline_btn = QPushButton()
        self._outline_btn.setObjectName("ModalIconBtn")
        self._outline_btn.setFixedSize(28, 28)
        self._outline_btn.setIcon(make_icon("view.png", "#8B8BAA", 16))
        self._outline_btn.setIconSize(QSize(16, 16))
        self._outline_btn.setToolTip("Open outline")
        self._outline_btn.clicked.connect(self._open_outline)
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
        hdr.addWidget(self._outline_btn)
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

        # Content field
        self._add_section(v, "CONTENT")
        self._content_input = RichTextEdit()
        toolbar = self._build_toolbar()
        self._content_input.setObjectName("ModalTextEdit")
        self._content_input.setPlaceholderText("Enter content here…")
        self._content_input.setMinimumHeight(220)
        self._content_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_input.setTabChangesFocus(True)
        self._content_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._content_input.customContextMenuRequested.connect(self._show_content_context_menu)
        v.addWidget(toolbar)
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

        self._find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self._find_shortcut.activated.connect(self._focus_find)

        self._replace_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        self._replace_shortcut.activated.connect(self._replace_one)

        self._replace_all_shortcut = QShortcut(QKeySequence("Ctrl+Shift+H"), self)
        self._replace_all_shortcut.activated.connect(self._replace_all)

        self._paste_plain_shortcut = QShortcut(QKeySequence("Ctrl+Shift+V"), self._content_input)
        self._paste_plain_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._paste_plain_shortcut.activated.connect(self._content_input.paste_plain_text)

        self._set_button_shortcut_tooltip(self._find_next_btn, "Find next", self._find_shortcut)
        self._set_button_shortcut_tooltip(self._replace_btn, "Replace selection", self._replace_shortcut)
        self._set_button_shortcut_tooltip(self._replace_all_btn, "Replace all matches", self._replace_all_shortcut)

        self._content_input.textChanged.connect(self._on_content_changed)
        self._content_input.pasted.connect(lambda: self._trigger_autosave("paste"))
        self._title_input.textChanged.connect(self._on_any_field_changed)
        # _as_window title sync is connected after _build() in __init__
        self._tags_input.textChanged.connect(self._on_any_field_changed)
        self._tags_input.textChanged.connect(self._refresh_meta_pills)
        self._folder_combo.currentIndexChanged.connect(self._on_any_field_changed)
        self._folder_combo.currentIndexChanged.connect(self._refresh_meta_pills)

        self.setTabOrder(self._title_input, self._content_input)
        self.setTabOrder(self._content_input, self._find_input)
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

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setObjectName("RichToolbar")
        row = FlowLayout(toolbar, h_spacing=6, v_spacing=6)
        row.setContentsMargins(6, 6, 6, 6)

        h1_btn = self._make_format_btn("H1", "Heading 1", checkable=False)
        h2_btn = self._make_format_btn("H2", "Heading 2", checkable=False)
        h3_btn = self._make_format_btn("H3", "Heading 3", checkable=False)
        color_btn = self._make_format_btn("A", "Text color", checkable=False)
        highlight_btn = self._make_format_btn("HL", "Highlight color", checkable=False)
        self._bold_btn = self._make_format_btn("B", "Bold")
        self._italic_btn = self._make_format_btn("I", "Italic")
        self._underline_btn = self._make_format_btn("U", "Underline")
        clear_btn = self._make_format_btn("Tx", "Clear formatting", checkable=False)
        list_btn = self._make_format_btn("•", "Bullet list", checkable=False)
        num_btn = self._make_format_btn("1.", "Numbered list", checkable=False)
        table_btn = self._make_format_btn("Tbl", "Insert table", checkable=False)
        img_btn = self._make_format_btn("Img", "Insert image", checkable=False)
        paste_btn = self._make_format_btn("Paste", "Paste options", checkable=False)
        paste_btn.setMinimumWidth(90)

        self._paste_menu = QMenu(paste_btn)
        normal_action = self._paste_menu.addAction("Normal")
        preserve_action = self._paste_menu.addAction("Preserve source formatting")
        plain_action = self._paste_menu.addAction("Paste without formatting")
        no_theme_action = self._paste_menu.addAction("Paste without dark theme styling")
        if normal_action is not None:
            normal_action.triggered.connect(self._paste_normal)
        if preserve_action is not None:
            preserve_action.triggered.connect(self._paste_preserve_source_formatting)
        if plain_action is not None:
            plain_action.triggered.connect(self._paste_without_formatting)
        if no_theme_action is not None:
            no_theme_action.triggered.connect(self._paste_without_theme_styling)
        paste_btn.setMenu(self._paste_menu)
        paste_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        paste_btn.clicked.connect(self._paste_normal)

        h1_btn.clicked.connect(lambda: self._set_heading(1))
        h2_btn.clicked.connect(lambda: self._set_heading(2))
        h3_btn.clicked.connect(lambda: self._set_heading(3))
        color_btn.clicked.connect(self._set_text_color)
        highlight_btn.clicked.connect(self._set_highlight_color)
        self._bold_btn.clicked.connect(lambda: self._set_bold(self._bold_btn.isChecked()))
        self._italic_btn.clicked.connect(lambda: self._set_italic(self._italic_btn.isChecked()))
        self._underline_btn.clicked.connect(lambda: self._set_underline(self._underline_btn.isChecked()))
        clear_btn.clicked.connect(self._clear_formatting)
        list_btn.clicked.connect(lambda: self._insert_list(QTextListFormat.Style.ListDisc))
        num_btn.clicked.connect(lambda: self._insert_list(QTextListFormat.Style.ListDecimal))
        table_btn.clicked.connect(self._insert_table)
        img_btn.clicked.connect(self._insert_image)

        row.addWidget(h1_btn)
        row.addWidget(h2_btn)
        row.addWidget(h3_btn)
        row.addWidget(color_btn)
        row.addWidget(highlight_btn)
        row.addWidget(self._bold_btn)
        row.addWidget(self._italic_btn)
        row.addWidget(self._underline_btn)
        row.addWidget(clear_btn)
        row.addWidget(list_btn)
        row.addWidget(num_btn)
        row.addWidget(table_btn)
        row.addWidget(img_btn)
        row.addWidget(paste_btn)
        row.addWidget(self._build_find_replace_bar())

        self._content_input.currentCharFormatChanged.connect(self._sync_format_buttons)
        self._sync_format_buttons(self._content_input.currentCharFormat())
        return toolbar

    def _paste_normal(self):
        self._content_input.paste()
        self._trigger_autosave("paste")

    def _paste_preserve_source_formatting(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        mime = clipboard.mimeData()
        if mime is not None and mime.hasHtml():
            self._content_input.insertHtml(mime.html())
            self._trigger_autosave("paste")
            return
        self._content_input.paste()
        self._trigger_autosave("paste")

    def _paste_without_formatting(self):
        self._content_input.paste_plain_text()
        self._trigger_autosave("paste")

    def _paste_without_theme_styling(self):
        self._content_input.paste_without_theme_colors()
        self._trigger_autosave("paste")

    def _build_find_replace_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("FindReplaceInlineGroup")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)

        self._find_input = QLineEdit()
        self._find_input.setObjectName("ModalInput")
        self._find_input.setPlaceholderText("Find")
        self._find_input.setFixedHeight(30)
        self._find_input.setMinimumWidth(130)
        self._find_input.returnPressed.connect(self._find_next)

        self._replace_input = QLineEdit()
        self._replace_input.setObjectName("ModalInput")
        self._replace_input.setPlaceholderText("Replace")
        self._replace_input.setFixedHeight(30)
        self._replace_input.setMinimumWidth(130)
        self._replace_input.returnPressed.connect(self._replace_one)

        self._match_case_chk = QCheckBox("Match case")
        self._whole_word_chk = QCheckBox("Whole word")

        self._find_next_btn = QPushButton("Next")
        self._find_next_btn.setObjectName("BtnSecondary")
        self._find_next_btn.setFixedHeight(30)
        self._find_next_btn.clicked.connect(self._find_next)

        self._replace_btn = QPushButton("Replace")
        self._replace_btn.setObjectName("BtnSecondary")
        self._replace_btn.setFixedHeight(30)
        self._replace_btn.clicked.connect(self._replace_one)

        self._replace_all_btn = QPushButton("Replace All")
        self._replace_all_btn.setObjectName("BtnSecondary")
        self._replace_all_btn.setFixedHeight(30)
        self._replace_all_btn.clicked.connect(self._replace_all)

        self._find_status_lbl = QLabel("")
        self._find_status_lbl.setObjectName("FindStatusLabel")
        self._find_status_lbl.setMinimumWidth(72)
        self._find_status_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        row.addWidget(self._find_input)
        row.addWidget(self._replace_input)
        row.addWidget(self._match_case_chk)
        row.addWidget(self._whole_word_chk)
        row.addWidget(self._find_next_btn)
        row.addWidget(self._replace_btn)
        row.addWidget(self._replace_all_btn)
        row.addWidget(self._find_status_lbl)
        return bar

    def _set_button_shortcut_tooltip(self, button: QPushButton, label: str, shortcut: QShortcut):
        key = shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
        button.setToolTip(f"{label} ({key})")

    def _find_flags(self) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if self._match_case_chk.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self._whole_word_chk.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        return flags

    def _set_find_status(self, text: str):
        self._find_status_lbl.setText(text)

    def _find_next(self) -> bool:
        term = self._find_input.text()
        if not term:
            self._set_find_status("Enter find text")
            return False

        doc = self._content_input.document()
        if doc is None:
            self._set_find_status("Editor unavailable")
            return False
        flags = self._find_flags()
        start_cursor = self._content_input.textCursor()
        if start_cursor.hasSelection():
            start_cursor.setPosition(start_cursor.selectionEnd())

        found = doc.find(term, start_cursor, flags)
        wrapped = False
        if found.isNull():
            restart = QTextCursor(doc)
            restart.movePosition(QTextCursor.MoveOperation.Start)
            found = doc.find(term, restart, flags)
            wrapped = True

        if found.isNull():
            self._set_find_status("No matches")
            return False

        self._content_input.setTextCursor(found)
        self._content_input.ensureCursorVisible()
        self._set_find_status("Wrapped" if wrapped else "")
        return True

    def _selected_matches_find(self) -> bool:
        cursor = self._content_input.textCursor()
        if not cursor.hasSelection():
            return False

        selected = cursor.selectedText()
        term = self._find_input.text()
        if self._match_case_chk.isChecked():
            return selected == term
        return selected.lower() == term.lower()

    def _replace_one(self):
        term = self._find_input.text()
        if not term:
            self._set_find_status("Enter find text")
            return

        cursor = self._content_input.textCursor()
        if not self._selected_matches_find():
            if not self._find_next():
                return
            cursor = self._content_input.textCursor()

        cursor.insertText(self._replace_input.text())
        self._content_input.setTextCursor(cursor)
        self._set_find_status("Replaced 1")
        self._find_next()

    def _replace_all(self):
        term = self._find_input.text()
        if not term:
            self._set_find_status("Enter find text")
            return

        doc = self._content_input.document()
        if doc is None:
            self._set_find_status("Editor unavailable")
            return
        flags = self._find_flags()
        walker = QTextCursor(doc)
        walker.movePosition(QTextCursor.MoveOperation.Start)
        replace_text = self._replace_input.text()
        replaced = 0

        while True:
            found = doc.find(term, walker, flags)
            if found.isNull():
                break
            found.insertText(replace_text)
            walker = found
            replaced += 1

        self._set_find_status(f"Replaced {replaced}")

    def _focus_find(self):
        self._find_input.setFocus()
        self._find_input.selectAll()

    def _on_content_changed(self):
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
        content = self._content_input.toHtml().strip()
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
        text = self._content_input.toPlainText()
        words = len(re.findall(r"\b\w+\b", text, re.UNICODE))
        chars = len(text)
        minutes = 0 if words == 0 else max(1, (words + 199) // 200)
        read_text = "0 min read" if minutes == 0 else f"{minutes} min read"
        self._stats_lbl.setText(f"{words} words • {chars} chars • {read_text}")

    def _open_outline(self):
        headings = extract_headings_from_document(self._content_input.document())
        modal = OutlineModal(headings, title="Outline", parent=self)
        modal.heading_selected.connect(self._jump_to_heading_position)
        modal.exec()

    def _jump_to_heading_position(self, target_pos: int):
        cursor = self._content_input.textCursor()
        cursor.setPosition(int(target_pos))
        self._content_input.setTextCursor(cursor)
        self._content_input.ensureCursorVisible()
        self._content_input.setFocus()

    def _make_format_btn(self, label: str, tooltip: str, checkable: bool = True) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        btn.setObjectName("RichBtn")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return btn

    def _merge_format(self, fmt: QTextCharFormat):
        self._content_input.mergeCurrentCharFormat(fmt)

    def _set_bold(self, enabled: bool):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if enabled else QFont.Weight.Normal)
        self._merge_format(fmt)

    def _set_italic(self, enabled: bool):
        fmt = QTextCharFormat()
        fmt.setFontItalic(enabled)
        self._merge_format(fmt)

    def _set_underline(self, enabled: bool):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(enabled)
        self._merge_format(fmt)

    def _set_text_color(self):
        color = QColorDialog.getColor(QColor("#EAF0FF"), self, "Text Color")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge_format(fmt)

    def _set_highlight_color(self):
        color = QColorDialog.getColor(QColor("#F6E27A"), self, "Highlight Color")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        self._merge_format(fmt)

    def _set_heading(self, level: int):
        size_map = {1: 20.0, 2: 16.0, 3: 14.0}
        if level not in size_map:
            return

        cursor = self._content_input.textCursor()
        if not cursor.hasSelection():
            block_fmt = QTextBlockFormat()
            block_fmt.setHeadingLevel(level)
            char_fmt = QTextCharFormat()
            char_fmt.setFontWeight(QFont.Weight.Bold)
            char_fmt.setFontPointSize(size_map[level])
            cursor.mergeBlockFormat(block_fmt)
            cursor.mergeCharFormat(char_fmt)
            self._content_input.setTextCursor(cursor)
            return

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        walker = self._content_input.textCursor()
        walker.setPosition(start)
        walker.beginEditBlock()
        while True:
            walker.select(QTextCursor.SelectionType.BlockUnderCursor)

            block_fmt = QTextBlockFormat()
            block_fmt.setHeadingLevel(level)
            walker.mergeBlockFormat(block_fmt)

            char_fmt = QTextCharFormat()
            char_fmt.setFontWeight(QFont.Weight.Bold)
            char_fmt.setFontPointSize(size_map[level])
            walker.mergeCharFormat(char_fmt)

            if walker.position() >= end:
                break
            if not walker.movePosition(QTextCursor.MoveOperation.NextBlock):
                break
            if walker.position() > end:
                break
        walker.endEditBlock()

    def _clear_formatting(self):
        cursor = self._content_input.textCursor()
        cursor.beginEditBlock()

        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)

        plain = cursor.selectedText().replace("\u2029", "\n")
        cursor.insertText(plain)

        reset_fmt = QTextCharFormat()
        reset_fmt.setFontWeight(QFont.Weight.Normal)
        reset_fmt.setFontItalic(False)
        reset_fmt.setFontUnderline(False)
        reset_fmt.clearForeground()
        reset_fmt.clearBackground()
        self._content_input.setCurrentCharFormat(reset_fmt)

        cursor.endEditBlock()
        self._content_input.setTextCursor(cursor)
        self._sync_format_buttons(self._content_input.currentCharFormat())

    def _sync_format_buttons(self, fmt: QTextCharFormat):
        self._bold_btn.blockSignals(True)
        self._italic_btn.blockSignals(True)
        self._underline_btn.blockSignals(True)
        self._bold_btn.setChecked(fmt.fontWeight() == QFont.Weight.Bold)
        self._italic_btn.setChecked(fmt.fontItalic())
        self._underline_btn.setChecked(fmt.fontUnderline())
        self._bold_btn.blockSignals(False)
        self._italic_btn.blockSignals(False)
        self._underline_btn.blockSignals(False)

    def _insert_list(self, style: QTextListFormat.Style):
        cursor = self._content_input.textCursor()
        cursor.beginEditBlock()
        list_format = QTextListFormat()
        list_format.setStyle(style)
        current_list = cursor.currentList()
        if current_list:
            current_list.setFormat(list_format)
        else:
            cursor.createList(list_format)
        cursor.endEditBlock()

    def _insert_table(self):
        rows, ok = QInputDialog.getInt(self, "Insert Table", "Rows:", 2, 1, 20, 1)
        if not ok:
            return
        cols, ok = QInputDialog.getInt(self, "Insert Table", "Columns:", 2, 1, 20, 1)
        if not ok:
            return
        cursor = self._content_input.textCursor()
        table_format = QTextTableFormat()
        table_format.setBorder(1)
        table_format.setCellPadding(6)
        cursor.insertTable(rows, cols, table_format)

    def _ask_image_insert_options(
        self,
        *,
        default_width: int,
        default_wrap_mode: str,
        default_border_width: int,
        default_border_color: str,
        default_crop_factors: tuple[int, int, int, int],
    ) -> tuple[int, str, int, str, tuple[int, int, int, int]] | None:
        modal = ImageOptionsModal(
            width=max(40, int(default_width)),
            wrap_mode=default_wrap_mode,
            border_width=max(0, int(default_border_width)),
            border_color=default_border_color,
            crop_factors=default_crop_factors,
            parent=self,
        )
        if modal.exec() != QDialog.DialogCode.Accepted:
            return None
        return modal.result_options()

    def _insert_image(self):
        image = self._pick_image_from_disk()
        if image.isNull():
            return

        default_width, default_wrap, default_border_width, default_border_color, default_crop = self._content_input.image_insert_defaults()
        default_width = min(max(40, image.width()), max(40, default_width))
        options = self._ask_image_insert_options(
            default_width=default_width,
            default_wrap_mode=default_wrap,
            default_border_width=default_border_width,
            default_border_color=default_border_color,
            default_crop_factors=default_crop,
        )
        if options is None:
            return

        width, wrap_mode, border_width, border_color, crop_factors = options
        self._content_input.set_image_insert_options(
            width,
            wrap_mode,
            border_width,
            border_color,
            crop_factors,
        )
        self._content_input.insert_qimage(
            image,
            width,
            wrap_mode,
            border_width,
            border_color,
            crop_factors,
        )

    def _pick_image_from_disk(self) -> QImage:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if not path:
            return QImage()
        return QImage(path)

    def _show_content_context_menu(self, pos: QPoint):
        menu = self._content_input.createStandardContextMenu()
        if menu is None:
            return
        image_cursor = self._content_input.image_cursor_for_position(pos)

        menu.addSeparator()
        clean_theme_paste_action = menu.addAction("Paste without dark theme styling")

        menu.addSeparator()
        paste_image_action = menu.addAction("Paste image")
        upload_image_action = menu.addAction("Upload image")

        edit_image_action = None
        replace_image_action = None
        remove_image_action = None

        cursor_snapshot = QTextCursor(image_cursor) if image_cursor is not None else None
        if cursor_snapshot is not None:
            image_menu = menu.addMenu("Image")
            if image_menu is not None:
                edit_image_action = image_menu.addAction("Edit options...")
                replace_image_action = image_menu.addAction("Replace image...")
                image_menu.addSeparator()
                remove_image_action = image_menu.addAction("Remove image")

        chosen = menu.exec(self._content_input.mapToGlobal(pos))
        if chosen is None:
            return

        if chosen == clean_theme_paste_action:
            self._paste_without_theme_styling()
            return

        if chosen == paste_image_action:
            self._content_input.paste()
            return
        if chosen == upload_image_action:
            self._insert_image()
            return

        if cursor_snapshot is None:
            return

        if chosen == edit_image_action:
            self._open_image_options_modal(cursor_snapshot)
            return

        if chosen == replace_image_action:
            image = self._pick_image_from_disk()
            if image.isNull():
                return
            info = self._content_input.image_options_at_cursor(cursor_snapshot)
            if info is None:
                return

            options = self._ask_image_insert_options(
                default_width=info["width"],
                default_wrap_mode=info["wrap_mode"],
                default_border_width=info["border_width"],
                default_border_color=info["border_color"],
                default_crop_factors=info["crop_factors"],
            )
            if options is None:
                return

            width, wrap_mode, border_width, border_color, crop_factors = options
            self._content_input.set_image_insert_options(
                width,
                wrap_mode,
                border_width,
                border_color,
                crop_factors,
            )
            self._content_input.replace_image_at_cursor(
                cursor_snapshot,
                self._encode_image_as_data_url(self._content_input.crop_qimage(image, crop_factors)),
                width,
                wrap_mode,
                border_width,
                border_color,
                crop_factors,
            )
            return

        if chosen == remove_image_action:
            self._content_input.remove_image_at_cursor(cursor_snapshot)

    def _open_image_options_modal(self, cursor: QTextCursor):
        info = self._content_input.image_options_at_cursor(cursor)
        if info is None:
            return

        result = self._ask_image_insert_options(
            default_width=info["width"],
            default_wrap_mode=info["wrap_mode"],
            default_border_width=info["border_width"],
            default_border_color=info["border_color"],
            default_crop_factors=info["crop_factors"],
        )
        if result is None:
            return

        new_width, wrap_mode, border_width, border_color, crop_factors = result

        source_image = self._decode_data_url_image(info["src"])
        if source_image.isNull():
            source = info["src"]
        else:
            source = self._encode_image_as_data_url(self._content_input.crop_qimage(source_image, crop_factors))

        self._content_input.set_image_insert_options(
            new_width,
            wrap_mode,
            border_width,
            border_color,
            crop_factors,
        )
        self._content_input.replace_image_at_cursor(
            cursor,
            source,
            new_width,
            wrap_mode,
            border_width,
            border_color,
            crop_factors,
        )

    def _decode_data_url_image(self, src: str) -> QImage:
        if not src.lower().startswith("data:image"):
            return QImage(src)

        match = re.match(r"data:image/[^;]+;base64,(.+)", src, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return QImage()

        encoded = re.sub(r"\s+", "", match.group(1))
        raw = QByteArray.fromBase64(encoded.encode("ascii"))
        image = QImage()
        image.loadFromData(raw)
        return image

    def _encode_image_as_data_url(self, image: QImage) -> str:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        encoded = buffer.data().toBase64().data().decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _add_section(self, layout, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("ModalSectionLabel")
        layout.addWidget(lbl)

    def _prefill(self, data: dict):
        self._title_input.setText(data.get("title", ""))
        content = data.get("content", "")
        if _looks_like_rich_text(content):
            self._content_input.setHtml(content)
        else:
            self._content_input.setPlainText(content)
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
            if _looks_like_rich_text(self._default_content):
                self._content_input.setHtml(self._default_content)
            else:
                self._content_input.setPlainText(self._default_content)

    def _collect(self) -> dict | None:
        title = self._title_input.text().strip()
        plain = self._content_input.toPlainText().strip()
        content = self._content_input.toHtml().strip()
        has_image = "<img" in content.lower()
        if not plain and not has_image:
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
                self._content_input.setFocus()
                self._content_input.insertPlainText("\n")
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
