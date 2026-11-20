"""Privacy scan run by build.py before anything is written.

Finds local paths, ids, e-mail addresses, and any extra words listed (one per line) in the file named
by the VIDEOBENCH_PRIVATE_WORDS environment variable. The list itself is never printed.
"""
import html
import os
import re
import sys
import unicodedata
from pathlib import Path

PATTERNS = [
    ("local path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|[\\/](?:Users|home|tmp)[\\/]|~[\\/]\.|App[D]ata", re.I)),
    ("id", re.compile(r"(?<![0-9a-z])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                      r"|(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}|(?:thread|task)[_-]?id)(?![0-9a-z])", re.I)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
]


def lists():
    f = os.environ.get("VIDEOBENCH_PRIVATE_WORDS")
    words = {ln.strip() for ln in Path(f).read_text(encoding="utf-8").splitlines() if ln.strip()} if f else set()
    return {"private words": words}, []


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s))


def scan_text(name, text, words, forbid=()):
    """-> [(file, category, context)]; every hit is masked with ▇ in the context"""
    t = norm(html.unescape(re.sub(r"data:font/\w+;base64,[A-Za-z0-9+/=]+", "data:", text)))
    low = t.lower()
    spans = [(kind, m.span()) for kind, rx in PATTERNS for m in rx.finditer(t)]
    for kind, ws in words.items():
        for w in (norm(w).lower() for w in ws):
            spans += [(kind, m.span()) for m in re.finditer(
                rf"(?<![a-z]){re.escape(w)}(?![a-z])" if w.isascii() else re.escape(w), low)]
    spans += [("operator field", (i, i + len(s))) for s in map(norm, forbid) if (i := t.find(s)) >= 0]
    mask = list(t)
    for _, (a, b) in spans:
        mask[a:b] = "▇" * (b - a)
    mask = "".join(mask)
    return [(name, kind, mask[max(0, a - 30):b + 30]) for kind, (a, b) in spans]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    words, _ = lists()
    found = [h for f in sys.argv[1:] for h in scan_text(f, Path(f).read_text(encoding="utf-8"), words)]
    for h in found:
        print(" | ".join(h))
    sys.exit(1 if found else 0)
