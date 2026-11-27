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


# On CI the test process crashes (access violation) while Qt's DLLs unload, after every test has
# passed, so the step fails. There, once pytest has printed its result, end the process with that
# result straight away. TerminateProcess skips DLL unloading; os._exit doesn't.
_exit_status = None


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if not os.environ.get("CI") or _exit_status is None:
        return
    print(f"pytest session status: {_exit_status}")
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), _exit_status)
    os._exit(_exit_status)
