"""Icon mode (the window shrunk to a floating search icon) and the global shortcut."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from vaultline import config as cfgmod
from vaultline import hotkey
from vaultline.ui.floating_icon import EDGE_GAP, ICON_SIZE, panel_position, snap_point
from vaultline.ui.main_window import COMPACT_MAX_HEIGHT, EDGE_SWITCH, MIN_WIDTH

from test_gui import app, setup, unlock, wait_for  # noqa: F401  (fixtures)

AREA = QRect(0, 0, 1920, 1040)  # a work area above a 40px taskbar


def test_snap_point():
    assert snap_point(QPoint(800, 400), ICON_SIZE, AREA) == QPoint(800, 0)   # middle: nearest edge (top), flush
    assert snap_point(QPoint(800, 900), ICON_SIZE, AREA).y() == AREA.bottom() - ICON_SIZE + 1 - EDGE_GAP
    assert snap_point(QPoint(1700, 500), ICON_SIZE, AREA).x() == AREA.right() - ICON_SIZE + 1 - EDGE_GAP
    assert snap_point(QPoint(10, 400), ICON_SIZE, AREA) == QPoint(EDGE_GAP, 400)    # near left: sticks
    right = AREA.right() - ICON_SIZE + 1
    assert snap_point(QPoint(right - 5, 3), ICON_SIZE, AREA) == QPoint(right - EDGE_GAP, 0)  # corner
    assert snap_point(QPoint(5000, 5000), ICON_SIZE, AREA) == \
        QPoint(right - EDGE_GAP, AREA.bottom() - ICON_SIZE + 1 - EDGE_GAP)          # off screen: back on it


def test_panel_grows_out_of_the_icon():
    w = 600
    left_icon = QRect(6, 300, ICON_SIZE, ICON_SIZE)
    assert panel_position(left_icon, w, COMPACT_MAX_HEIGHT, AREA) == QPoint(6, 300)       # same corner
    right_icon = QRect(AREA.right() - ICON_SIZE + 1 - 6, 300, ICON_SIZE, ICON_SIZE)
    p = panel_position(right_icon, w, COMPACT_MAX_HEIGHT, AREA)
    assert (p.x() + w - 1, p.y()) == (right_icon.right(), 300)                             # right-aligned
    low_icon = QRect(800, 990, ICON_SIZE, ICON_SIZE)
    p = panel_position(low_icon, w, COMPACT_MAX_HEIGHT, AREA)
    assert p.x() == 800 and p.y() + COMPACT_MAX_HEIGHT - 1 <= AREA.bottom()              # room to grow
    second = QRect(1920, 0, 2560, 1400)  # a second monitor to the right
    p = panel_position(QRect(1926, 500, ICON_SIZE, ICON_SIZE), w, COMPACT_MAX_HEIGHT, second)
    assert second.contains(QRect(p.x(), p.y(), w, COMPACT_MAX_HEIGHT))                     # stays on that monitor


def test_hotkey_parse():
    assert hotkey.parse("Ctrl+Alt+Space") == (hotkey.MOD_CONTROL | hotkey.MOD_ALT, 0x20)
    assert hotkey.parse("Ctrl+Shift+F5") == (hotkey.MOD_CONTROL | hotkey.MOD_SHIFT, 0x74)
    assert hotkey.parse("Meta+K") == (hotkey.MOD_WIN, ord("K"))
    assert hotkey.parse("Alt+7") == (hotkey.MOD_ALT, ord("7"))
    for bad in ("", "A", "Shift+A", "Space", "Ctrl+A, Ctrl+B", "Ctrl+Alt"):
        assert hotkey.parse(bad) is None, bad


def _drag(win, wanted, edge=1):
    """What Windows asks while a side edge is dragged and the mouse wants `wanted` px."""
    win.frame.wanted_width = wanted
    return win._sizing_height(MIN_WIDTH, win.compact_height(), edge)


def _press(widget, kind, pos, buttons=Qt.LeftButton):
    g = widget.mapToGlobal(pos)
    ev = QMouseEvent(kind, QPointF(pos), QPointF(g), Qt.LeftButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def test_pull_past_minimum_shrinks_to_icon(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.resize(640, 300)
    app.processEvents()
    assert win.compact and not win.icon_mode
    win._size_move_started()
    _drag(win, MIN_WIDTH - 20)                    # a little past the minimum: nothing happens
    app.processEvents()
    assert not win.icon_mode and win.isVisible()
    _drag(win, MIN_WIDTH - EDGE_SWITCH - 10)      # well past it: the icon
    assert wait_for(app, lambda: win.icon_mode, 2)
    assert not win.isVisible() and win.icon.isVisible()
    assert win.config.compact_width == 640        # the palette keeps its width for next time
    assert win.on_top and not win.vault_view.pin_btn.isVisible() or not win.vault_view.pin_btn.isVisibleTo(win)
    saved = cfgmod.Config.load()
    assert saved.icon_mode and len(saved.icon_pos) == 2
    assert not win.icon.locked

    # click: the panel opens beside the icon; Esc steps back, then shrinks it to the icon again
    QTest.mouseClick(win.icon, Qt.LeftButton)
    assert wait_for(app, lambda: win.isVisible(), 2)
    assert win.compact and win.width() == 640
    assert not win.icon.isVisible()                # the icon grew into the bar
    assert win.frameGeometry().contains(win.icon.frameGeometry().topLeft()) or         win.frameGeometry().contains(win.icon.frameGeometry().topRight())
    view = win.vault_view
    QTest.keyClicks(view.search, "goo")
    QTest.keyClick(view.search, Qt.Key_Escape)
    assert win.isVisible() and view.search.text() == ""
    QTest.keyClick(view.search, Qt.Key_Escape)
    assert not win.isVisible() and win.icon.isVisible() and win.icon_mode

    # a click right after it closed doesn't reopen it (the same click that closed it)
    win._icon_clicked()
    assert not win.isVisible()
    win._collapsed_at = 0
    win._icon_clicked()
    assert win.isVisible()
    win._icon_clicked()  # clicking the icon while the panel is open closes it
    assert not win.isVisible()

    # Ctrl+M from the panel: the full window, where the icon is
    win._collapsed_at = 0
    win.open_panel()
    QTest.keyClick(view.search, Qt.Key_M, Qt.ControlModifier)
    app.processEvents()
    assert not win.icon_mode and win.isVisible() and not win.compact and not win.icon.isVisible()
    assert not cfgmod.Config.load().icon_mode
    assert view.pin_btn.isVisibleTo(win)


def test_icon_menu_lock_and_restart(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.resize(820, 600)
    app.processEvents()
    win.enter_icon_mode()  # from the full window (the ☰ menu / tray)
    assert win.icon_mode and win.config.full_width == 820 and not win.isVisible()
    labels = [a.text() for a in win.icon.menu().actions() if a.text()]
    assert labels[0] == "Search" and "Lock" in labels

    vc.lock(full=True)
    assert wait_for(app, lambda: win.icon.locked)
    assert "Lock" not in [a.text() for a in win.icon.menu().actions()]
    win.open_panel()  # locked: the panel holds the password box
    assert win.isVisible() and win.stack.currentWidget() is win.unlock_view
    QTest.keyClick(win.unlock_view.password, Qt.Key_Escape)
    assert not win.isVisible() and win.icon_mode
    win._collapsed_at = 0
    win.open_panel()
    unlock(app, win, vc)
    assert wait_for(app, lambda: not win.icon.locked)
    assert win.stack.currentWidget() is win.vault_view

    # the icon remembers where it was dropped (after snapping to an edge)
    area = win.icon.screen().availableGeometry()
    start = win.icon.rect().center()
    _press(win.icon, QEvent.MouseButtonPress, start)
    target = QPoint(area.left() + 5, area.top() + 200) - win.icon.pos() + start
    _press(win.icon, QEvent.MouseMove, target)
    _press(win.icon, QEvent.MouseButtonRelease, target, Qt.NoButton)
    app.processEvents()
    assert win.icon.pos() == QPoint(area.left() + EDGE_GAP, area.top() + 200 - start.y() + start.y())
    assert cfgmod.Config.load().icon_pos == [win.icon.x(), win.icon.y()]

    # near the top it sticks there while still being dragged (as the bar does), flush with it
    _press(win.icon, QEvent.MouseButtonPress, start)
    _press(win.icon, QEvent.MouseMove, QPoint(area.left() + 400, area.top() + 10) - win.icon.pos() + start)
    assert win.icon.y() == area.top()
    _press(win.icon, QEvent.MouseButtonRelease, start, Qt.NoButton)
    app.processEvents()
    assert win.icon.y() == area.top()

    # started again in icon mode, unlocked: just the icon
    win.collapse_panel()
    win.icon.hide()
    win.icon_mode = False
    win.start()
    assert win.icon_mode and win.icon.isVisible() and not win.isVisible()

    # locked (as at every real start): the panel opens beside the icon, at the password box
    win.collapse_panel()
    win.icon.hide()
    win.icon_mode = False
    vc.lock(full=True)
    app.processEvents()
    win.start()
    app.processEvents()
    assert win.icon_mode and win.isVisible() and not win.icon.isVisible()
    assert win.stack.currentWidget() is win.unlock_view
    win.collapse_panel()
    unlock(app, win, vc)
    win.leave_icon_mode(full=False)  # "Open compact window"
    assert win.isVisible() and win.compact and not win.icon_mode


def test_shortcut_setting(setup):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.open_settings()
    page = win.settings_page
    assert not page.hotkey_on.isChecked() and page.hotkey_text() == "Ctrl+Alt+Space"  # off by default
    page.hotkey_on.setChecked(True)
    page.hotkey_keys.setKeySequence(QKeySequence("Ctrl+Shift+K"))
    win.close_settings()
    saved = cfgmod.Config.load()
    assert saved.hotkey_enabled and saved.hotkey == "Ctrl+Shift+K"
    assert win.hotkey_error == ""
    win.open_settings()
    win.settings_page.hotkey_keys.setKeySequence(QKeySequence("Shift+K"))  # can't be global: kept
    win.close_settings()
    assert cfgmod.Config.load().hotkey == "Ctrl+Shift+K"

    # the shortcut brings the search up, and puts it away again
    win.hide_to_tray()
    win.summon()
    assert win.isVisible()
    win.enter_icon_mode()
    win.summon()
    assert win.isVisible() and win.icon_mode


@pytest.mark.parametrize("edge", [1, 2])
def test_panel_pulled_narrow_closes_to_icon(setup, edge):
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.enter_icon_mode()
    win.open_panel()
    width = win.width()
    win._size_move_started()
    _drag(win, MIN_WIDTH - EDGE_SWITCH - 1, edge)
    assert wait_for(app, lambda: not win.isVisible(), 2)
    assert win.icon_mode and win.config.compact_width == width


def test_rescaled_while_hidden_stays_the_icon(setup, monkeypatch):
    """Monitors asleep: Windows moved the hidden panel to a screen with other scaling, so its
    width grew past the palette's. Not the user's doing: it stays the icon, at the palette width."""
    app, win, vc, clip, db = setup
    unlock(app, win, vc)
    win.resize(560, 300)
    win.enter_icon_mode()
    win.open_panel()  # (Qt holds back a hidden window's resizes; Windows' own reach it either way)
    app.processEvents()
    monkeypatch.setattr(win, "_user_resizing", lambda: False)
    win.resize(896, win.height())  # 560 at 200% counted at 125%
    app.processEvents()
    wait_for(app, lambda: win.width() == 560, 2)
    assert win.icon_mode and win.compact and win.width() == 560 and cfgmod.Config.load().icon_mode
    win.collapse_panel()
    vc.lock("Locked after inactivity.")
    app.processEvents()
    win._collapsed_at = 0
    win._icon_clicked()                      # the icon still opens its panel
    assert win.isVisible() and win.compact and win.icon_mode

    monkeypatch.setattr(win, "_user_resizing", lambda: True)
    win.resize(800, win.height())            # the user pulling it wider still makes a normal window
    app.processEvents()
    assert not win.icon_mode and not win.compact
