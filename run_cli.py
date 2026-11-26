"""Entry point for vaultline-cli (PyInstaller console target). No Qt needed."""
import sys

from vaultline.cli import main

if __name__ == "__main__":
    sys.exit(main())
