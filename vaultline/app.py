"""Application startup: tray icon, idle auto-lock, single instance."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_NAME, icons, pipe_name, theme
from .cli_server import CliServer
from .clipboard import SecureClipboard
from .config import Config
from .controller import VaultController
from .ui.main_window import MainWindow, enable_dark_title_bar

_INPUT_EVENTS = {QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel}


class IdleLock(QObject):
    """Locks the vault after N minutes without keyboard/mouse input in the app."""

    def __init__(self, app: QApplication, controller: VaultController, config: Config):
        super().__init__(app)
        self.vc, self.config = controller, config
        self.last_input = time.monotonic()
        app.installEventFilter(self)
        self._timer = QTimer(self, interval=10_000)
        self._timer.timeout.connect(self._check)
        self._timer.start()
        controller.unlocked.connect(self.touch)

    def touch(self, *_):
        self.last_input = time.monotonic()

    def eventFilter(self, _obj, event) -> bool:  # noqa: N802
        if event.type() in _INPUT_EVENTS:
            self.last_input = time.monotonic()
        return False

    def _check(self) -> None:
        if not (self.config.auto_lock_enabled and self.vc.is_unlocked):
            return
        if time.monotonic() - self.last_input >= self.config.auto_lock_minutes * 60:
            self.vc.lock("Locked after inactivity.")


class Tray(QObject):
    def __init__(self, window: MainWindow, controller: VaultController, config: Config):
        super().__init__(window)
        self.win, self.vc, self.config = window, controller, config
        self.icon = QSystemTrayIcon(icons.locked_tray_icon(), window)
        self.icon.setToolTip(f"{APP_NAME} (locked)")

        menu = QMenu()
        self.show_action = menu.addAction("Show", self.toggle)
        self.lock_action = menu.addAction("Lock Vault", lambda: self.vc.lock())
        self.lock_full_action = menu.addAction("Lock completely (needs master password)",
                                               lambda: self.vc.lock(full=True))
        menu.addSeparator()
        self.auto_action = QAction(f"Auto-lock after {config.auto_lock_minutes} minutes", menu, checkable=True)
        self.auto_action.setChecked(config.auto_lock_enabled)
        self.auto_action.toggled.connect(self._set_auto_lock)
        menu.addAction(self.auto_action)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        menu.aboutToShow.connect(self._refresh)
        self._menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)

        self._told_about_tray = False
        window.hidden_to_tray.connect(self._hint_once)
        controller.unlocked.connect(lambda *_: self._set_locked(False))
        controller.locked.connect(lambda *_: self._set_locked(True))
        controller.pin_locked.connect(lambda *_: self._set_locked(True))
        self.icon.show()

    def _set_locked(self, locked: bool) -> None:
        self.icon.setIcon(icons.locked_tray_icon() if locked else icons.app_icon())
        self.icon.setToolTip(f"{APP_NAME} (locked)" if locked else APP_NAME)

    def _refresh(self) -> None:
        self.show_action.setText("Hide" if self.win.isVisible() else "Show")
        self.lock_action.setEnabled(self.vc.is_unlocked)
        self.lock_full_action.setEnabled(self.vc.is_unlocked or self.vc.is_pin_locked)
        self.lock_full_action.setVisible(self.vc.pin_available)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle()

    def toggle(self) -> None:
        if self.win.isVisible() and self.win.isActiveWindow():
            self.win.hide_to_tray()
        else:
            self.win.bring_to_front()

    def _set_auto_lock(self, on: bool) -> None:
        self.config.auto_lock_enabled = on
        self.config.save()

    def _hint_once(self) -> None:
        if not self._told_about_tray:
            self._told_about_tray = True
            self.icon.showMessage(APP_NAME, "Still running in the tray. Click the icon to bring it back.",
                                  QSystemTrayIcon.Information, 2500)

    def quit(self) -> None:
        self.win.clipboard.wipe()
        self.vc.shutdown()
        self.icon.hide()
        self.win.allow_close = True
        QApplication.quit()


def _single_instance(window_getter) -> QLocalServer | None:
    """Return a server if we are the first instance; otherwise wake the first one and return None."""
    name = pipe_name("")
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(name)

    def on_conn():
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(lambda: (conn.readAll(), window_getter().bring_to_front()))
    server.newConnection.connect(on_conn)
    return server


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        except Exception:
            pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    font = QFont(theme.UI_FONT)
    font.setFamilies([theme.UI_FONT, "Segoe UI"])
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(icons.app_icon())

    holder: dict = {}
    server = _single_instance(lambda: holder["win"])
    if server is None:
        return 0

    config = Config.load()
    clipboard = SecureClipboard(config.clipboard_clear_seconds, app)
    controller = VaultController(config, app)
    window = MainWindow(config, controller, clipboard)
    holder["win"] = window
    enable_dark_title_bar(window)
    IdleLock(app, controller, config)
    cli = CliServer(controller, app)
    cli.changed_by_cli.connect(lambda summary: window.toast.show_message("Command line", summary, "info", 3000))
    tray = Tray(window, controller, config) if QSystemTrayIcon.isSystemTrayAvailable() else None
    if tray is None:
        window.allow_close = True
        app.setQuitOnLastWindowClosed(True)
        app.aboutToQuit.connect(lambda: (clipboard.wipe(), controller.shutdown()))

    window.show()
    code = app.exec()
    server.close()
    return code
