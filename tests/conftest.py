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


# On CI the runner sometimes reports exit code 1 after every test passed: something crashes while
# Python and Qt shut down. There, end the process with pytest's own result once it has printed it.
_exit_status = None


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if os.environ.get("CI") and _exit_status is not None:
        print(f"pytest session status: {_exit_status}")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_status)
