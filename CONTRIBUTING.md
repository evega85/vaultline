# Contributing

Issues and pull requests are welcome. For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Run from source

You need Python 3.12 or newer on Windows 10/11.

```bat
git clone https://github.com/evega85/vaultline.git
cd vaultline
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python run.py
```

## Run the tests

```bat
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python -m pytest -q
```

The tests use their own private pipe names, so they never reach a Vaultline you have running. CI runs the same tests on every push and pull request.

## Build the .exe files

```bat
build.bat
```

This puts `Vaultline.exe` and `vaultline-cli.exe` in `dist\`.

## Code rules

Read [CLAUDE.md](CLAUDE.md) before changing anything. It lists the design rules: every save goes through `Vault.save`, the file must stay readable by KeePassXC and KeePassium, and the CLI never handles secrets outside the opt-in shared folder.

Use made-up data only, in tests, screenshots and issues. Never commit a real vault.
