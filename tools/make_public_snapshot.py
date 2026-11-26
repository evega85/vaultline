"""Copy the current committed code into a separate public repo folder, after a privacy check.

    python tools/make_public_snapshot.py [--target ../public] [--message "v1.0.0"] [--check-only]

What it does:
  1. Takes the files tracked by git at HEAD (not untracked or ignored files), minus EXCLUDE.
  2. Scans every file name and text file for patterns listed in `.private-patterns`
     (one regex per line; that file is gitignored so the patterns stay private),
     plus built-in checks for real vaults and settings files. Any hit stops everything.
  3. Mirrors the files into --target (its own git repo, created if missing) and commits
     with the author name/email from `.private-patterns` "author:" / "email:" lines.
It never pushes. Pushing is a separate, manual step.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = ("docs/worklog/",)          # private notes stay in the private repo
ALLOWED_VAULTS = {"demo/sample.kdbx"}
BLOCKED_NAMES = re.compile(r"(\.kdbx|\.kdbx\.bak|\.keyx|config\.json|\.env|CLAUDE\.local\.md|\.private-patterns)$",
                           re.I)


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def load_private() -> tuple[list[re.Pattern], str, str]:
    path = ROOT / ".private-patterns"
    if not path.exists():
        sys.exit("Missing .private-patterns (one regex per line). Refusing to publish without it.")
    pats, author, email = [], "", ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("author:"):
            author = line.split(":", 1)[1].strip()
        elif line.lower().startswith("email:"):
            email = line.split(":", 1)[1].strip()
        else:
            pats.append(re.compile(line))
    if not pats:
        sys.exit(".private-patterns has no patterns.")
    return pats, author, email


def tracked_files() -> list[str]:
    files = git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
    return [f for f in files if not f.startswith(EXCLUDE)]


def scan(files: list[str], pats: list[re.Pattern], extra_text: str = "") -> list[str]:
    problems = []
    for f in files:
        if BLOCKED_NAMES.search(f) and f not in ALLOWED_VAULTS:
            problems.append(f"{f}: file type must never be published")
        for p in pats:
            if p.search(f):
                problems.append(f"{f}: file name matches a private pattern ({p.pattern})")
        data = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, check=True, capture_output=True).stdout
        if b"\0" in data[:4096]:
            continue  # binary (icon, png, demo vault)
        text = data.decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            for p in pats:
                if p.search(line):
                    problems.append(f"{f}:{n}: matches a private pattern ({p.pattern})")
    for p in pats:
        if extra_text and p.search(extra_text):
            problems.append(f"commit message matches a private pattern ({p.pattern})")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(ROOT.parent / "public"))
    ap.add_argument("--message", default="Public snapshot")
    ap.add_argument("--check-only", action="store_true", help="scan only, copy nothing")
    a = ap.parse_args()

    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("Note: you have uncommitted changes; only what's committed (HEAD) is published.")
    pats, author, email = load_private()
    files = tracked_files()
    problems = scan(files, pats, a.message)
    if problems:
        print("STOPPED: possible personal details found. Nothing was copied.\n")
        print("\n".join(problems))
        return 1
    print(f"Privacy scan passed: {len(files)} files, {len(pats)} patterns.")
    if a.check_only:
        return 0
    if not (author and email):
        sys.exit("Add 'author:' and 'email:' lines (e.g. your GitHub no-reply address) to .private-patterns.")

    target = Path(a.target).resolve()
    if target == ROOT or ROOT in target.parents:
        sys.exit("Target must be outside this repo.")
    fresh = not (target / ".git").exists()
    target.mkdir(parents=True, exist_ok=True)
    if fresh:
        git("init", "-b", "main", cwd=target)
    git("config", "user.name", author, cwd=target)
    git("config", "user.email", email, cwd=target)

    # mirror: remove what the public repo tracks, then copy the snapshot
    if not fresh:
        for f in git("ls-files", cwd=target).splitlines():
            (target / f).unlink(missing_ok=True)
    for f in files:
        dest = target / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, check=True,
                                        capture_output=True).stdout)
    git("add", "-A", cwd=target)
    if not git("status", "--porcelain", cwd=target).strip():
        print(f"No changes since the last snapshot in {target}.")
        return 0
    git("commit", "-q", "-m", a.message, cwd=target)
    print(f"Committed snapshot to {target} as {author} <{email}>. Nothing was pushed.")
    print(git("log", "--oneline", "-3", cwd=target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
