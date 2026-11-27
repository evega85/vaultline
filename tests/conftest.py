import os
import sys

# Private pipe names for this test run, so tests can never reach the real running Vaultline.
os.environ["VAULTLINE_INSTANCE"] = f"test{os.getpid()}"
# No windows on screen while tests run (set QT_QPA_PLATFORM=windows to watch them).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)
