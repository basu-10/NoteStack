"""
TuiEditorWidget — QWebEngineView wrapper embedding Toast UI Editor.

Responsibilities (each isolated for clarity):
  - _EditorBridge  : QObject registered on QWebChannel; JS calls its slots.
  - TuiEditorWidget: Manages the WebView, bridge lifecycle, and content cache.

Bridge protocol
───────────────
Python → JS (via page.runJavaScript):
    window.tuiEditor.initEditor(content, theme, mode, viewerOnly)
    window.tuiEditor.setMarkdown(md)
    window.tuiEditor.setTheme(theme)
    window.tuiEditor.setMode(mode)
    window.tuiEditor.focusEditor()

JS → Python (via QWebChannel slots):
    bridge.on_editor_ready()          — fired once after channel handshake
    bridge.on_content_changed(md)     — fired (300 ms debounced) on edit

Content cache
─────────────
_current_markdown is updated synchronously whenever:
  - set_content() is called (optimistic)
  - on_content_changed() arrives from JS

This means get_content() is always synchronous and never blocks the UI.
"""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget


# ─── File-system helpers ──────────────────────────────────────────────────────

def _tui_resources_dir() -> str:
    """Absolute path to resources/tui_editor/ — works in dev and installed."""
    # tui_editor_widget.py lives at NoteStack/ui/; 2 levels up = repo/app root.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "resources", "tui_editor")


def _html_path() -> str:
    """Absolute path to tui_editor.html (lives next to this module)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tui_editor.html")


# ─── Bridge (JS ↔ Python) ─────────────────────────────────────────────────────

class _EditorBridge(QObject):
    """
    Registered on QWebChannel as 'bridge'.

    JS calls the @pyqtSlot methods; those re-emit as Qt signals so the rest
    of Python can connect with normal signal/slot syntax.
    """

    # Emitted once the JS QWebChannel handshake completes.
    editor_ready = pyqtSignal()
    # Emitted (300 ms debounced by JS) on every content change.
    content_changed = pyqtSignal(str)

    @pyqtSlot()
    def on_editor_ready(self) -> None:
        self.editor_ready.emit()

    @pyqtSlot(str)
    def on_content_changed(self, markdown: str) -> None:
        self.content_changed.emit(markdown)


# ─── Widget ───────────────────────────────────────────────────────────────────

class TuiEditorWidget(QWidget):
    """
    Drop-in editor/viewer widget backed by Toast UI Editor in QWebEngineView.

    Signals
    -------
    content_changed(str)
        Emitted (300 ms debounced) on every edit.  Updated content is the
        full current Markdown string.
    editor_ready()
        Emitted once the JS editor is fully initialized and ready to accept
        set_content / set_theme / set_mode calls.

    Constructor parameters
    ----------------------
    theme : 'dark' | 'light'
    mode  : 'markdown' | 'wysiwyg'  (ignored when viewer=True)
    viewer: True → read-only viewer; no toolbar displayed.
    """

    content_changed = pyqtSignal(str)
    editor_ready    = pyqtSignal()

    def __init__(
        self,
        *,
        theme: str = "dark",
        mode: str = "markdown",
        viewer: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._theme             = theme
        self._mode              = mode
        self._viewer            = viewer
        self._current_markdown  = ""
        self._pending_content: str | None = None  # queued before bridge is ready
        self._ready             = False

        # ── Web view ──────────────────────────────────────────────────────
        self._view = QWebEngineView(self)
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # ── QWebChannel ───────────────────────────────────────────────────
        self._bridge  = _EditorBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

        # ── Connect bridge → widget signals ───────────────────────────────
        self._bridge.editor_ready.connect(self._on_bridge_ready)
        self._bridge.content_changed.connect(self._on_content_from_js)

        # ── Load HTML (reads file from disk; assets resolved via base URL) ─
        self._load_html()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_html(self) -> None:
        html_path     = _html_path()
        resources_dir = _tui_resources_dir()

        if not os.path.isfile(html_path):
            self._view.setHtml(
                "<body style='color:#EF4444;font-family:sans-serif;padding:16px'>"
                f"<b>tui_editor.html not found:</b><br>{html_path}"
                "</body>"
            )
            return

        with open(html_path, encoding="utf-8") as fh:
            html = fh.read()

        # Base URL points at resources/tui_editor/ so that relative hrefs
        # (toastui-editor-all.min.js, .css) resolve correctly.
        base_url = QUrl.fromLocalFile(resources_dir + "/")
        self._view.setHtml(html, base_url)

    # ── Bridge callbacks ──────────────────────────────────────────────────────

    def _on_bridge_ready(self) -> None:
        """
        Called once after the JS QWebChannel handshake completes.
        We now call window.tuiEditor.initEditor() with all startup params
        in a single round-trip, then flush any queued content.
        """
        self._ready = True
        content = self._pending_content if self._pending_content is not None else ""
        self._pending_content = None
        self._current_markdown = content
        self._run_js(
            "window.tuiEditor.initEditor("
            f"{json.dumps(content)}, "
            f"{json.dumps(self._theme)}, "
            f"{json.dumps(self._mode)}, "
            f"{'true' if self._viewer else 'false'}"
            ");"
        )
        self.editor_ready.emit()

    def _on_content_from_js(self, markdown: str) -> None:
        """Called by JS (debounced 300 ms) whenever the editor content changes."""
        self._current_markdown = markdown
        self.content_changed.emit(markdown)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_content(self) -> str:
        """Return current Markdown content synchronously (from local cache).

        The cache is kept up-to-date by on_content_changed callbacks from JS
        and by set_content() calls, so this never blocks the UI thread.
        """
        return self._current_markdown

    def set_content(self, markdown: str) -> None:
        """Set editor content.

        Updates the local cache immediately (so get_content() is consistent
        even before the async JS call completes).  If the editor is not yet
        ready, the content is queued and flushed after initEditor().
        """
        self._current_markdown = markdown
        if self._ready:
            self._run_js(f"window.tuiEditor.setMarkdown({json.dumps(markdown)});")
        else:
            self._pending_content = markdown

    def set_theme(self, theme: str) -> None:
        """Switch theme ('dark' | 'light') without reinitializing the editor."""
        self._theme = theme
        if self._ready:
            self._run_js(f"window.tuiEditor.setTheme({json.dumps(theme)});")

    def set_mode(self, mode: str) -> None:
        """Switch editing mode ('markdown' | 'wysiwyg').  No-op in viewer mode."""
        self._mode = mode
        if self._ready and not self._viewer:
            self._run_js(f"window.tuiEditor.setMode({json.dumps(mode)});")

    def focus_editor(self) -> None:
        """Give keyboard focus to the embedded editor."""
        self._view.setFocus()
        if self._ready:
            self._run_js("window.tuiEditor.focusEditor();")

    def find_text(self, term: str, forward: bool = True) -> None:
        """Highlight next occurrence of *term* inside the web view."""
        from PyQt6.QtWebEngineCore import QWebEnginePage

        flags = QWebEnginePage.FindFlag(0)
        if not forward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        self._view.page().findText(term, flags)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run_js(self, script: str) -> None:
        self._view.page().runJavaScript(script)
