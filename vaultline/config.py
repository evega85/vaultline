"""Local settings stored as JSON in %APPDATA%\\Vaultline.

Only non-secret preferences live here. The master password is never written.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
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
