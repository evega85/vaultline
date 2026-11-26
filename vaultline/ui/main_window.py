"""Top-level window: switches between the lock screens and the vault list."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QMainWindow, QStackedWidget, QWidget

from .. import APP_NAME, agent_access, icons, otp as otplib, pin as pinlib, theme
from ..clipboard import SecureClipboard
from ..config import Config
from ..controller import VaultController
from ..vault import CreateFolder, DeleteEntries, DeleteFolder, EntryEdit, EntryView, RenameFolder, Snapshot
from . import small_dialogs
from .entry_dialog import EntryDialog
from .pin_view import PinView
from .settings_page import SettingsPage
from .toast import Toast
from .unlock_view import UnlockView
from .vault_view import VaultView

_LABELS = {"username": "Username", "password": "Password", "otp": "OTP", "url": "URL",
           "card_code": "Card code", "card_number": "Card number"}


class MainWindow(QMainWindow):
    hidden_to_tray = Signal()
    settings_saved = Signal()

    def __init__(self, config: Config, controller: VaultController, clipboard: SecureClipboard):
        super().__init__()
        self.config = config
        self.vc = controller
        self.clipboard = clipboard
        self.allow_close = False
        self._dialog: QDialog | None = None
        self.settings_page: SettingsPage | None = None
        self._icons_running = False
        self._icons_asked = False  # True when the user asked for icons (vs. the automatic fetch on save)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(720, 600)
        self.setMinimumSize(560, 420)
        if config.window_geometry:  # reopen where it was; Qt moves it back if that screen is gone
            self.restoreGeometry(QByteArray.fromBase64(config.window_geometry.encode("ascii")))

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        self.stack = QStackedWidget(root)
        self.unlock_view = UnlockView(config.last_database, config.last_keyfile)
        self.pin_view = PinView()
        self.vault_view = VaultView()
        for w in (self.unlock_view, self.pin_view, self.vault_view):
            self.stack.addWidget(w)
        self.toast = Toast(root)

        # wiring
        self.unlock_view.unlock_requested.connect(self._unlock)
        self.pin_view.pin_entered.connect(self._pin_entered)
        self.pin_view.use_master.connect(lambda: self.vc.lock(full=True))
        vv = self.vault_view
        vv.copy_requested.connect(self.copy)
        vv.copy_text.connect(self.copy_text)
        vv.add_requested.connect(lambda: self.open_editor(None))
        vv.edit_requested.connect(self.open_editor)
        vv.delete_requested.connect(self.delete_entry)
        vv.lock_requested.connect(lambda: self.vc.lock())
        vv.hide_requested.connect(self.hide_to_tray)
        vv.settings_requested.connect(self.open_settings)
        vv.folder_create_requested.connect(self.create_folder)
        vv.folder_rename_requested.connect(self.rename_folder)
        vv.folder_delete_requested.connect(self.delete_folder)

        self.vc.unlocked.connect(self._on_unlocked)
        self.vc.unlock_failed.connect(self._on_unlock_failed)
        self.vc.entries_changed.connect(self._on_entries_changed)
        self.vc.save_failed.connect(self._on_save_failed)
        self.vc.locked.connect(self._on_locked)
        self.vc.pin_locked.connect(self._on_pin_locked)
        self.vc.pin_failed.connect(self.pin_view.wrong)
        self.vc.busy_changed.connect(self._on_busy)
        self.vc.icon_progress.connect(self._on_icon_progress)
        self.vc.applied.connect(self._on_applied)

    # ---- layout ----------------------------------------------------------------
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.stack.setGeometry(self.centralWidget().rect())
        self.toast.reposition()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.stack.setGeometry(self.centralWidget().rect())
        self.focus_input()

    def focus_input(self) -> None:
        if self.settings_page is not None:
            self.settings_page.setFocus()
        elif self.vc.is_unlocked:
            self.vault_view.focus_search()
        elif self.vc.is_pin_locked:
            self.pin_view.focus()
        else:
            self.unlock_view.focus()

    # ---- window / tray behaviour ----------------------------------------------------
    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.focus_input()

    def remember_geometry(self) -> None:
        """Store the window's position and size so the next start opens it in the same place."""
        g = bytes(self.saveGeometry().toBase64()).decode("ascii")
        if g != self.config.window_geometry:
            self.config.window_geometry = g
            self.config.save()

    def hide_to_tray(self) -> None:
        self.remember_geometry()
        self.hide()
        self.hidden_to_tray.emit()

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        if self.allow_close:
            self.remember_geometry()
            e.accept()
        else:
            e.ignore()
            self.hide_to_tray()

    # ---- unlock / lock --------------------------------------------------------
    def _unlock(self, path: str, password: str, keyfile: str) -> None:
        self.vc.unlock(path, password, keyfile or None)

    def _pin_entered(self, pin: str) -> None:
        self.vc.pin_unlock(pin)

    def _on_unlocked(self, snap: Snapshot) -> None:
        self.config.last_database = self.vc.path
        self.config.last_keyfile = self.unlock_view._keyfile
        self.config.save()
        self.unlock_view.reset()
        self.pin_view.set_busy(False)
        self.vault_view.set_snapshot(snap)
        self.stack.setCurrentWidget(self.vault_view)
        self.vault_view.focus_search()
        shared = agent_access.shared_folder_path(self.config, snap) if self.config.cli_enabled else None
        if shared:  # a reminder, so a setting changed behind your back is noticed
            self.toast.show_message("Shared with agents", f"{shared} (each request asks you)", "info", 4000)

    def _on_unlock_failed(self, message: str) -> None:
        self.unlock_view.set_busy(False)
        self.unlock_view.show_error(message)
        self.unlock_view.password.selectAll()
        self.unlock_view.password.setFocus()

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.reject()

    def _on_locked(self, reason: str) -> None:
        self._close_dialog()
        self.close_settings()
        self.clipboard.wipe()
        self.vault_view.clear()
        self.unlock_view.reset(reason)
        self.stack.setCurrentWidget(self.unlock_view)

    def _on_pin_locked(self, reason: str) -> None:
        self._close_dialog()
        self.close_settings()
        self.clipboard.wipe()
        self.vault_view.clear(remember=True)
        self.pin_view.reset(Path(self.vc.path).name, reason)
        self.stack.setCurrentWidget(self.pin_view)

    def _on_busy(self, busy: bool) -> None:
        if not self.vc.is_unlocked or self._icons_running:
            return
        if busy:
            self.vault_view.set_status("Working…", theme.WARN)
        else:
            self.vault_view.update_status()

    # ---- entries ------------------------------------------------------------
    def _on_entries_changed(self, snap: Snapshot, reason: str) -> None:
        self.vault_view.set_snapshot(snap)
        if reason == "saved":
            self.toast.show_message("Saved")
        elif reason == "merged":
            self.toast.show_message("Saved", "merged with changes from another device", "info", 2600)
        elif reason == "reloaded":
            self.toast.show_message("Vault updated", "changes synced from another device", "info", 2400)

    def _on_save_failed(self, edit: EntryEdit | None, message: str) -> None:
        self.vault_view.update_status()
        if edit is None:
            self.toast.show_message("Not saved", message, "error", 4000)
            return
        self.toast.show_message("Not saved", "see the dialog", "error", 3000)
        view = next((v for v in self.vault_view._views if v.uuid == edit.uuid), None)
        self.open_editor(view, draft=edit, error=f"Could not save: {message}")

    def _run_dialog(self, dlg: QDialog) -> int:
        self._dialog = dlg
        try:
            return dlg.exec()
        finally:
            self._dialog = None
            if self.vc.is_unlocked:
                self.vault_view.focus_search()

    def open_editor(self, view: EntryView | None, draft: EntryEdit | None = None, error: str = "") -> None:
        if not self.vc.is_unlocked or self._dialog is not None:
            return
        snap = self.vc.snapshot
        dlg = EntryDialog(self, view, draft, error,
                          groups=snap.groups if snap else [], root_uuid=snap.root_uuid if snap else "",
                          default_group=self.vault_view.folder_uuid)
        if self._run_dialog(dlg) and dlg.result_edit is not None and self.vc.is_unlocked:
            self.vc.save(dlg.result_edit)

    def delete_entry(self, view: EntryView) -> None:
        if not self.vc.is_unlocked or self._dialog is not None:
            return
        if small_dialogs.confirm(self, "Delete entry",
                                 f"Move “{view.title or 'untitled'}” to the recycle bin? "
                                 "You can restore it in KeePassXC, or from the backups folder.",
                                 "Delete", danger=True):
            self.vault_view.panel.close_panel()
            self.vc.apply([DeleteEntries([view.uuid])], token=("ui", "Moved to recycle bin"))

    # ---- folders -------------------------------------------------------------------
    def _group(self, uuid: str):
        snap = self.vc.snapshot
        return next((g for g in snap.groups if g.uuid == uuid), None) if snap else None

    def create_folder(self) -> None:
        if not self.vc.is_unlocked:
            return
        parent = self._group(self.vault_view.folder_uuid) if self.vault_view.folder_uuid else None
        hint = f"Created inside “{parent.name}”. Use / for deeper levels." if parent else "Use / for a subfolder, e.g. Work/Clients"
        name = small_dialogs.ask_text(self, "New folder", hint, placeholder="Folder name")
        if name:
            path = "/".join([*parent.path, name]) if parent else name
            self.vc.apply([CreateFolder(path)], token=("ui", "Folder created"))

    def rename_folder(self, uuid: str) -> None:
        g = self._group(uuid)
        if not g:
            return
        name = small_dialogs.ask_text(self, "Rename folder", "New name", g.name)
        if name and name != g.name:
            if "/" in name:
                self.toast.show_message("Folder names can't contain /", kind="warn")
                return
            self.vc.apply([RenameFolder(uuid, name)], token=("ui", "Folder renamed"))

    def delete_folder(self, uuid: str) -> None:
        g = self._group(uuid)
        snap = self.vc.snapshot
        if not g or not snap:
            return
        inside = sum(1 for v in snap.entries if v.group == g.display or v.group.startswith(g.display + " / "))
        what = f"its {inside} entr{'y' if inside == 1 else 'ies'} and subfolders" if inside else "its subfolders"
        if small_dialogs.confirm(self, "Delete folder",
                                 f"Move “{g.name}” with {what} to the recycle bin?", "Delete", danger=True):
            if self.vault_view.folder_uuid == uuid:
                self.vault_view.set_folder(None)
            self.vc.apply([DeleteFolder(uuid)], token=("ui", "Folder moved to recycle bin"))

    def _on_applied(self, token, ok: bool, msg: str, _report=None) -> None:
        if not isinstance(token, tuple):
            return
        if token[0] == "ui":
            if ok:
                self.toast.show_message(token[1])
            else:
                self.toast.show_message("Not saved", msg, "error", 4000)
        elif token[0] == "master":
            if ok:
                self.toast.show_message("Master password changed", "use the new one from now on", "ok", 3000)
            else:
                self.toast.show_message("Password not changed", msg, "error", 5000)

    # ---- settings ------------------------------------------------------------------
    def open_settings(self) -> None:
        if not self.vc.is_unlocked or self._dialog is not None or self.settings_page is not None:
            return
        snap = self.vc.snapshot
        page = SettingsPage(self.config, self.vc.path, snap.groups if snap else ())
        page.change_master.connect(self._change_master)
        page.pin_toggled.connect(self._pin_toggled)
        page.change_pin.connect(self._set_pin)
        page.download_icons.connect(lambda missing: (self.close_settings(), self._download_icons(missing)))
        page.done.connect(self.close_settings)
        self.settings_page = page
        self.vault_view.panel.close_panel()
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        page.setFocus()

    def close_settings(self) -> None:
        """Save the settings page and go back to the list (or wherever a lock sends us)."""
        page, self.settings_page = self.settings_page, None
        if page is None:
            return
        page.save()
        self.clipboard.clear_seconds = self.config.clipboard_clear_seconds
        self.settings_saved.emit()
        if self.vc.is_unlocked:
            self.stack.setCurrentWidget(self.vault_view)
            self.vault_view.focus_search()
        self.stack.removeWidget(page)
        page.deleteLater()

    def _change_master(self) -> None:
        vault = self.vc._vault
        got = small_dialogs.ask_master_change(self, lambda pw: bool(vault) and vault.check_password(pw))
        if got:
            err = self.vc.change_master_password(*got)
            if err:
                self.toast.show_message("Password not changed", err, "error", 4000)
            else:
                self.toast.show_message("Changing master password…", kind="info")

    def _set_pin(self) -> bool:
        pin = small_dialogs.ask_new_pin(self)
        if not pin:
            return False
        self.config.pin_salt, self.config.pin_hash = pinlib.make(pin)
        self.config.pin_enabled = True
        self.config.save()
        self.toast.show_message("PIN set", "locking now asks for the PIN")
        return True

    def _pin_toggled(self, on: bool) -> None:
        page = self.settings_page
        if on:
            ok = self._set_pin()
            if page is not None:
                page.set_pin_checked(ok)
        else:
            self.config.pin_enabled = False
            self.config.pin_salt = self.config.pin_hash = ""
            self.config.save()
            if page is not None:
                page.set_pin_checked(False)

    # ---- website icons -------------------------------------------------------------
    def _download_icons(self, missing_only: bool) -> None:
        snap = self.vc.snapshot
        if not snap:
            return
        todo = [e for e in snap.entries if e.url and not (missing_only and e.icon_uuid)]
        self._icons_asked = True
        if not self.vc.download_icons(todo):
            self._icons_asked = False
            self.toast.show_message("No icons to fetch", "entries need a website URL", "info")

    def _on_icon_progress(self, done: int, total: int) -> None:
        if total:
            self._icons_running = True
            self.vault_view.set_status(f"Downloading website icons  {done}/{total}", theme.ACCENT_SOFT)
            return
        self._icons_running = False
        self.vault_view.update_status()
        asked, self._icons_asked = self._icons_asked, False
        if done:
            self.toast.show_message(f"Found {done} website icon{'s' if done != 1 else ''}", "saving to vault", "info")
        elif asked:  # stay quiet when the automatic fetch on save finds nothing
            self.toast.show_message("No website icons found", kind="warn")

    # ---- clipboard --------------------------------------------------------------
    def copy_text(self, label: str, value: str) -> None:
        if not value:
            self.toast.show_message(f"{label} is empty", kind="warn")
            return
        self.clipboard.copy(value)
        self.toast.show_message(f"Copied {label}", f"clears in {self.clipboard.clear_seconds}s")

    def copy(self, kind: str, view: EntryView) -> None:
        if view.is_card and kind == "otp":
            kind = "card_code"  # Ctrl+T on a card copies its code
        if view.is_card and kind == "username":
            kind = "card_number"  # Ctrl+B on a card copies its number
        if kind == "otp":
            if view.totp is None:
                self.toast.show_message("No OTP for this entry", kind="warn")
                return
            value = otplib.current(view.totp)[0]
        else:
            value = getattr(view, kind, "")
        label = _LABELS[kind]
        if not value:
            self.toast.show_message(f"No {label.lower()} to copy", kind="warn")
            return
        self.clipboard.copy(value)
        self.toast.show_message(f"Copied {label}", f"clears in {self.clipboard.clear_seconds}s")


def enable_dark_title_bar(widget: QWidget) -> None:
    """Ask DWM for a dark title bar (Windows 10 1809+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                break
    except Exception:
        pass
