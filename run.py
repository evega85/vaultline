"""Entry point (also the PyInstaller target).

    Vaultline.exe                                  start the app
    Vaultline.exe --background                     start in the tray (used when Windows signs in)
    Vaultline.exe --selftest demo\\sample.kdbx      open the demo vault (password demo123), create a new
                                                   vault and import into it, then exit: 0 = OK, 1 = failed.
                                                   Used by the release build to make sure the packaged exe
                                                   can really decrypt and create a vault.
"""
import sys


def selftest(path: str) -> int:
    import tempfile
    from pathlib import Path

    try:
        from vaultline.vault import ImportEntries, Vault, create_vault  # inside try: a missing DLL must fail
        from vaultline.bitwarden import ImportItem                    # the test, not show a dialog
        snap = Vault(path, "demo123", None, Path(tempfile.mkdtemp())).open()
        # creating needs pykeepass's blank template bundled in the exe (it once wasn't)
        work = Path(tempfile.mkdtemp())
        new = work / "selftest-new.kdbx"
        create_vault(str(new), "selftest-pw")
        v = Vault(str(new), "selftest-pw", None, work / "bk")
        v.open()
        made, _ = v.save([ImportEntries([ImportItem(title="Selftest", username="u", password="p")])])
        import vaultline.app  # noqa: F401  (the whole UI must import too)
        return 0 if snap.entries and [e.title for e in made.entries] == ["Selftest"] else 1
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
