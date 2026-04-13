"""
NoteStack — entry point.
Run with: python main.py
"""
import sys
import os
import hashlib
import ctypes

# Ensure repo root is on path regardless of CWD
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from database.db import USER_DATA_DIR, initialize_db
from ui.icon_utils import make_png_icon, resources_dir
from ui.main_window import MainWindow
from ui.styles import make_stylesheet


def _acquire_single_instance_lock() -> QLockFile | None:
    lock_path = os.path.join(USER_DATA_DIR, "notestack.lock")
    lock = QLockFile(lock_path)
    if not lock.tryLock(100):
        return None
    return lock


def _instance_server_name() -> str:
    digest = hashlib.sha1(USER_DATA_DIR.encode("utf-8")).hexdigest()[:12]
    return f"notestack-instance-{digest}"


def _notify_running_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_instance_server_name())
    if not socket.waitForConnected(300):
        return False
    socket.write(b"ACTIVATE")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def _create_activation_server(win: MainWindow) -> QLocalServer | None:
    server_name = _instance_server_name()
    QLocalServer.removeServer(server_name)
    server = QLocalServer()

    def _activate_existing_instance():
        sock = server.nextPendingConnection()
        while sock is not None:
            sock.readAll()
            sock.disconnectFromServer()
            win.bring_to_front()
            sock = server.nextPendingConnection()

    server.newConnection.connect(_activate_existing_instance)
    if not server.listen(server_name):
        return None
    return server


def _set_windows_app_id():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ABasuApps.NoteStack")
    except Exception:
        pass


def main():
    # Enable high-DPI scaling
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    _set_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("NoteStack")
    app.setOrganizationName("NoteStack")
    app.setQuitOnLastWindowClosed(False)

    app_icon_path = resources_dir() / "project_logo.png"
    if app_icon_path.exists():
        app_icon = make_png_icon(app_icon_path)
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

    app_lock = _acquire_single_instance_lock()
    if app_lock is None:
        if not _notify_running_instance():
            QMessageBox.warning(
                None,
                "NoteStack Is Already Running",
                "Another NoteStack window is already open.\n\n"
                "Please switch to the existing instance.",
            )
        return

    # Global font
    font = QFont("Segoe UI", 11)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    # Global stylesheet
    app.setStyleSheet(make_stylesheet())

    # Bootstrap DB
    initialize_db()

    # Launch window
    win = MainWindow()
    if not app.windowIcon().isNull():
        win.setWindowIcon(app.windowIcon())
    win.show()
    activation_server = _create_activation_server(win)

    exit_code = app.exec()
    if activation_server is not None:
        activation_server.close()
        QLocalServer.removeServer(_instance_server_name())
    app_lock.unlock()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
