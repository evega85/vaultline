"""Dark zinc palette and the application stylesheet."""
from __future__ import annotations

BG = "#0e0e10"
SURFACE = "#161618"
RAISED = "#1d1d20"
HOVER = "#232327"
SELECTED = "#222226"
CHIP_BORDER = "#34343b"
BORDER = "#27272a"
BORDER_STRONG = "#3f3f46"
TEXT = "#f4f4f5"
TEXT_2 = "#a1a1aa"
TEXT_3 = "#71717a"
ACCENT = "#6366f1"
ACCENT_HOVER = "#7275f5"
ACCENT_SOFT = "#a5b4fc"
DANGER = "#f87171"
DANGER_STRONG = "#dc2626"
WARN = "#fbbf24"
SUCCESS = "#4ade80"

UI_FONT = "Segoe UI Variable Text"
MONO_FONT = "Cascadia Mono"

AVATAR_COLORS = ["#3b3f6b", "#4a3560", "#2f4f4f", "#5a3d33", "#2e4a63",
                 "#4d4a2a", "#3d4f35", "#5b3346", "#36474f", "#4b3b6b"]

STYLESHEET = f"""
* {{
    font-family: "{UI_FONT}", "Segoe UI", sans-serif;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog, #Root {{ background: {BG}; }}
QToolTip {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
    border-radius: 6px; padding: 4px 8px; font-size: 12px;
}}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:hover, QPlainTextEdit:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; background: {RAISED}; }}
QLineEdit[error="true"] {{ border-color: {DANGER}; }}
QLineEdit#Search {{
    font-size: 15px; padding: 10px 12px; border-radius: 10px; background: {SURFACE};
}}
QLineEdit#Search:focus {{ border-color: {BORDER_STRONG}; background: {SURFACE}; }}
QLineEdit#Search[compact="true"] {{ font-size: 14px; padding: 5px 10px; border-radius: 8px; }}
QLineEdit#Mono {{ font-family: "{MONO_FONT}", Consolas, monospace; font-size: 13px; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_3}; background: {SURFACE}; }}
QPushButton#Primary {{
    background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #3b3d8f; border-color: #3b3d8f; color: #c7c8f5; }}
QPushButton#Ghost, QToolButton#Ghost {{
    background: transparent; border: 1px solid transparent; color: {TEXT_2};
}}
QPushButton#Ghost:hover, QToolButton#Ghost:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton#Caution {{ background: transparent; border: 1px solid #5c4a1a; color: {WARN}; }}
QPushButton#Caution:hover {{ background: #2a2414; border-color: {WARN}; }}
QPushButton#Caution:disabled {{ color: #7a6a3a; border-color: #3a3220; background: transparent; }}
QPushButton#Link {{
    background: transparent; border: none; padding: 2px 0; color: {ACCENT_SOFT}; font-size: 12px;
}}
QPushButton#Link:hover {{ color: white; }}
QPushButton#Chip {{
    background: transparent;
    border: 1px solid {CHIP_BORDER};
    border-radius: 7px;
    padding: 0px 10px;
    font-size: 12px;
    color: {TEXT_2};
    min-height: 26px; max-height: 26px;
}}
QPushButton#Chip:hover {{ background: {HOVER}; border-color: {BORDER_STRONG}; color: {TEXT}; }}
QPushButton#Chip:pressed {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton#Chip:disabled {{ color: {BORDER_STRONG}; border-color: {SURFACE}; }}
QPushButton#FolderBtn {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 0px 12px; color: {TEXT_2}; font-size: 13px;
}}
QPushButton#FolderBtn:hover {{ border-color: {BORDER_STRONG}; color: {TEXT}; }}
QPushButton#FolderBtn[active="true"] {{ border-color: {ACCENT}; color: {TEXT}; }}
QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; font-size: 13px;
}}
QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox[shared="true"] {{ border-color: {WARN}; }}
QListView#Suggestions {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: 6px; color: {TEXT};
    selection-background-color: {ACCENT}; selection-color: white; outline: none; padding: 0; font-size: 13px;
}}
QListView#Suggestions::item {{ padding: 6px 40px 6px 8px; border-radius: 4px; }}
QListView#Suggestions::item:hover {{ background: {CHIP_BORDER}; color: {TEXT}; }}
QListView#Suggestions::item:selected {{ background: {ACCENT}; color: white; }}
QComboBox QAbstractItemView {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: 6px;
    selection-background-color: {HOVER}; outline: none; padding: 4px;
}}
QPushButton#Template {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 4px 12px; font-size: 12px; color: {TEXT_2};
}}
QPushButton#Template:hover {{ border-color: {BORDER_STRONG}; color: {TEXT}; }}
QPushButton#Template:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QScrollArea {{ background: transparent; border: none; }}
QPushButton#Danger {{ background: #b91c1c; border: 1px solid #b91c1c; color: white; font-weight: 600; }}
QPushButton#Danger:hover {{ background: #dc2626; border-color: #dc2626; }}
QLineEdit#Pin {{ font-size: 20px; letter-spacing: 6px; padding: 8px; }}
QCheckBox {{ font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_STRONG}; background: {BG};
}}
QCheckBox::indicator:hover {{ border-color: {TEXT_3}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}
QSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px; font-size: 13px;
}}
QSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QPushButton#Hidden {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 0px 8px; font-size: 11px; color: {TEXT_3}; min-height: 32px;
}}
QPushButton#Hidden:checked {{ border-color: {ACCENT}; color: {ACCENT_SOFT}; }}
QToolButton {{
    background: transparent; border: none; border-radius: 6px; padding: 4px; color: {TEXT_2};
}}
QToolButton:hover {{ background: {HOVER}; color: {TEXT}; }}

/* ---------- list ---------- */
QListWidget#Entries {{
    background: transparent; border: none; padding: 0px;
}}
QListWidget#Entries::item {{
    border: none; border-radius: 10px; margin: 1px 0px; padding: 0px;
}}
QListWidget#Entries::item:hover {{ background: {SURFACE}; }}
QListWidget#Entries::item:selected {{ background: {SELECTED}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 30px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- text ---------- */
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: 13.5px; font-weight: 600; }}
QLabel#Title[compact="true"] {{ font-size: 13px; }}
QLabel#Sub {{ font-size: 12px; color: {TEXT_2}; }}
QLabel#Muted {{ font-size: 12px; color: {TEXT_3}; }}
QLabel#FieldLabel {{ font-size: 12px; color: {TEXT_2}; font-weight: 600; }}
QLabel#Error {{ font-size: 12px; color: {DANGER}; }}
QLabel#Hint {{ font-size: 12px; color: {TEXT_3}; }}
QLabel#Heading {{ font-size: 20px; font-weight: 600; }}
QLabel#DialogTitle {{ font-size: 16px; font-weight: 600; }}
#Footer {{ border-top: 1px solid {BORDER}; }}
#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}

/* ---------- menus (tray, context) ---------- */
QMenu {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; font-size: 13px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
"""
