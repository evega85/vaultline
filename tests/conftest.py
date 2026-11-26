import os
import sys

# Private pipe names for this test run, so tests can never reach the real running Vaultline.
os.environ["VAULTLINE_INSTANCE"] = f"test{os.getpid()}"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)
