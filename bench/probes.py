"""Speed probes: loader only.

The seven probes (prompts, input files, answers and per-probe judges) are not part of the public
repository. Put your own probes in probes/<id>/ (prompt.txt, optional files/, key.json) and add a
judge here if you want them graded.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from . import common

PROBE_IDS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
NO_WORKDIR = ("P1", "P2", "P7")  # probes that need no tools and no working directory


def probe_dir(pid: str, root: Path = common.ROOT) -> Path:
    return root / "probes" / pid


def load_probe(pid: str, root: Path = common.ROOT) -> dict:
    d = probe_dir(pid, root)
    prompt = (d / "prompt.txt").read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip("\n")
    files = d / "files"
    return {"id": pid, "prompt": prompt, "key": common.read_json(d / "key.json"),
            "files_dir": files if files.is_dir() else None}


def squash(text: Any) -> str:
    s = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(ch for ch in s if not ch.isspace() and unicodedata.category(ch)[0] not in ("P", "S", "C"))


def judge(pid: str, final_text: Any, key: dict) -> dict:
    raise ValueError(f"no public judge for probe {pid!r}")
