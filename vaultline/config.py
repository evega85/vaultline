"""Local settings stored as JSON in %APPDATA%\\Vaultline.

Only non-secret preferences live here. The master password is never written.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME


def _base_dir(env: str, fallback: str) -> Path:
    root = os.environ.get(env) or str(Path.home() / fallback)
    return Path(root) / APP_NAME


CONFIG_DIR = _base_dir("APPDATA", "AppData/Roaming")
DATA_DIR = _base_dir("LOCALAPPDATA", "AppData/Local")
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_DIR = DATA_DIR / "backups"


@dataclass
class Config:
    last_database: str = ""
    last_keyfile: str = ""
    auto_lock_enabled: bool = True
    auto_lock_minutes: int = 10
    clipboard_clear_seconds: int = 20
    backups_to_keep: int = 10
    auto_download_icons: bool = True  # fetch a site's icon when an entry with a URL is saved
    pin_enabled: bool = False         # after a full unlock, locking asks for the PIN instead
    pin_salt: str = ""
    pin_hash: str = ""                # scrypt hash; the PIN itself is never stored
    cli_enabled: bool = False         # allow vaultline-cli to talk to the unlocked app
    agent_share_enabled: bool = False # let agents ask (with approval) for secrets in one folder
    agent_folder_uuid: str = ""       # that folder's UUID (hex), so renames don't break it
    agent_folder_pass: int = 15       # prompt's "Allow the folder" length: 15, 60, -1 (until locked), 0 (off)
    window_geometry: str = ""         # main window position and size (Qt saveGeometry, base64)
    compact_pinned: bool = False      # a compact (narrow) window stays on top of other windows
    full_height: int = 0              # the full view's height, brought back when leaving compact
    full_width: int = 0               # the full view's width (for the view button / Ctrl+M)
    compact_width: int = 0            # the palette's width
    icon_mode: bool = False           # shrunk to a floating search icon (always on top)
    icon_pos: list = field(default_factory=list)  # [x, y] of the icon, Qt screen coordinates
    hotkey_enabled: bool = False      # a global shortcut opens the search from any program
    hotkey: str = "Ctrl+Alt+Space"
    autotype_hotkey_enabled: bool = False  # a global shortcut types the matching login into the window in front
    autotype_hotkey: str = "Ctrl+Alt+A"
    autotype_suggest: bool = True  # quick box lists the logins for the window you came from
    newlogin_hotkey_enabled: bool = False  # a global shortcut makes a new login for the window in front
    newlogin_hotkey: str = "Ctrl+Alt+N"
    default_profile_uuid: str = ""   # sign-up profile whose email new logins use (hex UUID)
    newlogin_folder_uuid: str = ""   # folder new logins go in ("" = top level)
    newlogin_length: int = 20        # generated password length for new logins
    recent_databases: list = field(default_factory=list)  # [{"path", "keyfile"}], newest first
    close_action: str = "ask"         # the window's X: "ask", "tray" (keep running) or "quit"
    hidden_usernames: list = field(default_factory=list)  # usernames X'd out of suggestions, salted hashes only
    username_salt: str = ""

    RECENT_MAX = 6

    def remember_database(self, path: str, keyfile: str = "") -> None:
        """Put this database first in the recent list (paths only, never a password)."""
        key = os.path.normcase(os.path.abspath(path))
        rest = [r for r in self.recent_databases
                if isinstance(r, dict) and os.path.normcase(os.path.abspath(r.get("path", ""))) != key]
        self.recent_databases = [{"path": path, "keyfile": keyfile or ""}] + rest[: self.RECENT_MAX - 1]

    def _username_key(self, name: str) -> str:
        if not self.username_salt:
            self.username_salt = secrets.token_hex(16)
        return hashlib.sha256((self.username_salt + name.strip().casefold()).encode("utf-8")).hexdigest()

    def username_hidden(self, name: str) -> bool:
        return bool(self.hidden_usernames) and self._username_key(name) in self.hidden_usernames

    def hide_username(self, name: str) -> None:
        """Stop suggesting this username (until it's saved on an entry again). Stores a hash, not the name."""
        key = self._username_key(name)
        if key not in self.hidden_usernames:
            self.hidden_usernames.append(key)

    def unhide_username(self, name: str) -> bool:
        """Suggest this username again; True if it had been hidden."""
        if not name.strip() or not self.username_hidden(name):
            return False
        self.hidden_usernames.remove(self._username_key(name))
        return True

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
