"""Entry point (also the PyInstaller target)."""
import sys

from vaultline.app import main

if __name__ == "__main__":
    sys.exit(main())
