"""Entry point (also the PyInstaller target).

    Vaultline.exe                                  start the app
    Vaultline.exe --background                     start in the tray (used when Windows signs in)
    Vaultline.exe --selftest demo\\sample.kdbx      open the demo vault (password demo123) and exit:
                                                   0 = OK, 1 = failed. Used by the release build to make
                                                   sure the packaged exe can really decrypt a vault.
"""
import sys


def selftest(path: str) -> int:
    import tempfile
    from pathlib import Path

    try:
        from vaultline.vault import Vault  # inside try: a missing DLL must fail the test, not show a dialog
        snap = Vault(path, "demo123", None, Path(tempfile.mkdtemp())).open()
        import vaultline.app  # noqa: F401  (the whole UI must import too)
        return 0 if snap.entries else 1
    except Exception as exc:  # windowed exe has no console: leave a note next to the vault
        try:
            Path(path).with_suffix(".selftest-error.txt").write_text(repr(exc), encoding="utf-8")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    from vaultline.app import main
    sys.exit(main())
