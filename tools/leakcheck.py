"""Leak check for the public repository. Exit code 1 on any hit; prints path and category only.

  python -B tools/leakcheck.py               scan every file under the repo root
  python -B tools/leakcheck.py --staged      scan what is staged (index content) and the commit identity
  python -B tools/leakcheck.py --message F   scan a commit message file

Looks for user-home paths, e-mail addresses, UUIDs, common secret formats and image metadata.
The commit author and committer must use a GitHub noreply address.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", "out", "runs", "results", ".pytest_cache"}
PATTERNS = [
    ("local path", re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?:Users|Documents and Settings)(?![A-Za-z])"
                              r"|(?<![\w.])/(?:Users|home)/[A-Za-z0-9_.-]+|App[D]ata|(?<![A-Za-z0-9])[D-Z]:[\\/]+AI(?![A-Za-z])")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("uuid", re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])")),
    ("secret", re.compile(r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}"
                          r"|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{40,}")),
]
NOREPLY = re.compile(r"<\d+\+[A-Za-z0-9-]+@users\.noreply\.github\.com>")
TEXT = {".py", ".md", ".json", ".txt", ".html", ".css", ".svg", ".toml", ".cfg", ".yml", ".yaml", ".gitignore", ""}
IMAGE = {".png", ".jpg", ".jpeg"}


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def entries(staged: bool) -> list[tuple[str, bytes]]:
    """(path, content): staged mode reads the index, so a partly staged file is checked as it will be committed"""
    if staged:
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8").split("\0")
        return [(n, git("show", f":{n}")) for n in names if n]
    return [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in ROOT.rglob("*")
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def image_meta(data: bytes) -> bool:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return any(c in data for c in (b"tEXt", b"iTXt", b"zTXt", b"eXIf"))
    return b"Exif\x00" in data[:65536] or b"http://ns.adobe.com/xap" in data[:65536]


def scan(items: list[tuple[str, bytes]]) -> list[tuple[str, str]]:
    hits = []
    for rel, data in items:
        hits += [(rel, f"{kind} (file name)") for kind, rx in PATTERNS if rx.search(rel)]
        name = rel.rsplit("/", 1)[-1]
        ext = "" if name == ".gitignore" or "." not in name else "." + name.rsplit(".", 1)[-1].lower()
        if ext in IMAGE:
            if image_meta(data):
                hits.append((rel, "image metadata"))
            continue
        if ext not in TEXT:
            hits.append((rel, "file type not allowed"))
            continue
        text = data.decode("utf-8", "replace")
        hits += [(rel, kind) for kind, rx in PATTERNS if rx.search(text)]
    return hits


def identity() -> list[tuple[str, str]]:
    hits = []
    for who in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        if not NOREPLY.search(git("var", who).decode("utf-8")):
            hits.append((who, "commit identity is not a GitHub noreply address"))
    return hits


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["--message"]:
        items = [("commit message", Path(sys.argv[2]).read_bytes())]
        hits = [(rel, kind) for rel, data in items for kind, rx in PATTERNS if rx.search(data.decode("utf-8", "replace"))]
    else:
        staged = "--staged" in sys.argv
        items = entries(staged)
        hits = scan(items) + (identity() if staged else [])
    for rel, kind in hits:
        print(f"{rel}: {kind}")
    print(f"leakcheck: {len(items)} files, {len(hits)} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
