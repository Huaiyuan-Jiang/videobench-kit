"""Turn the sample run into a local leaderboard page.

  python -B tools/sample_chain.py      # makes results/summary_sample_chain.json
  python -B tools/sample_site.py       # writes out/sample/out/site/index.html

The three baseline answer sets (reference, empty, wrong) are shown as if they were models.
Times and token counts on the page are placeholders: offline answers have neither.
The site code is copied to out/sample/site/ and run there with the sample data next to it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "sample"
ROLE = {"name": "样题", "what": "两道合成样题", "desc": "两道合成样题：看图登记数字，字幕断行二选一。",
        "tasks": {"SAMPLE1": {"title": "看图登记数字", "desc": "五张图各有一个两位数，登记成表。"},
                  "SAMPLE2": {"title": "字幕断行二选一", "desc": "二十组字幕断行，挑合格的写法。"}}}
ROLE_EN = {"name": "Sample", "what": "Two synthetic sample tasks",
           "desc": "Two synthetic sample tasks: read numbers from images, pick subtitle line breaks.",
           "tasks": {"SAMPLE1": {"title": "Read numbers", "desc": "Five images, one two-digit number each. Write them down."},
                     "SAMPLE2": {"title": "Pick line breaks", "desc": "Twenty subtitle pairs. Pick the one that follows the rule."}}}

SAMPLE_TEXT = {"card.desc.home": "VideoBench 样题演示：1 个样题岗位，2 道合成题，3 套固定作答。",
               "card.img.small": "样题演示：2 道合成题",
               "first.1": "这是样题演示页：1 个样题岗位、2 道合成题，考生是 3 套固定作答，不是模型。页上的用时、token 和口头禅是占位数。"}
SAMPLE_TEXT_EN = {"card.desc.home": "VideoBench sample demo: one sample role, two synthetic tasks, three fixed answer sets.",
                  "card.img.small": "Sample demo: 2 synthetic tasks",
                  "first.1": "This is the sample demo: one sample role, two synthetic tasks, and three fixed answer sets instead of models. Times, tokens and tic counts on this page are placeholders."}


def main() -> int:
    doc = json.loads((ROOT / "results" / "summary_sample_chain.json").read_text(encoding="utf-8"))
    names = {"chain-ref": "Reference answers", "chain-empty": "Empty answers", "chain-wrong": "Wrong answers"}
    for m in doc["models"]:
        m["kind"], m["label"] = "examinee", names.get(m["id"], m["id"])
    doc["probes"], doc["probe_runs"] = [], []
    for n, r in enumerate(doc["runs"]):  # offline answers have no timing or tokens; use placeholders so every chart has bars
        r["metrics"] = {**(r.get("metrics") or {}), "wall_s": 300.0 + 120 * n, "tokens_in": 200_000 + 50_000 * n,
                        "tokens_out": 8_000 + 1_000 * n, "tokens_cache_read": 150_000 + 40_000 * n}
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT / "site", ignore=shutil.ignore_patterns("__pycache__"))
    for name, role in (("roles.json", ROLE), ("roles_en.json", ROLE_EN)):
        (OUT / "site" / name).write_text(json.dumps({"SAMPLE": role}, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, texts in (("labels.json", SAMPLE_TEXT), ("labels_en.json", SAMPLE_TEXT_EN)):  # the real board's intro does not fit
        f = OUT / "site" / name
        labels = json.loads(f.read_text(encoding="utf-8"))
        for k, v in texts.items():
            labels[k]["text"] = v
        f.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    data = OUT / "data"
    for d in ("reviews/zh", "reviews/en"):
        (data / d).mkdir(parents=True)
    (data / "summary.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (data / "tics.json").write_text(json.dumps({"models": {m["id"]: {"per10k": 10.0 + 5 * n} for n, m in enumerate(doc["models"])}}),
                                    encoding="utf-8")
    env = {**os.environ, "VIDEOBENCH_ALLOW_UNUSED_LABELS": "1"}  # no probes and no reviews on the sample page
    return subprocess.run([sys.executable, "-B", str(OUT / "site" / "build.py"), "--no-card"] + sys.argv[1:], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
