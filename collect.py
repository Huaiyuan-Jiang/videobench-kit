"""Collect answers offline: copy each working directory back into runs/<examinee>/work/.

Usage:
  python -B collect.py --examinee <id> [--only KEY ...]

For every task entry in runs/<examinee>/dispatch.json this copies the working directory, records the
file count and writes runs/<examinee>/collected/<key>.json. Timing and token metrics stay empty: the
public version has no adapter for any agent platform.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import common, summary, workdir  # noqa: E402


def collect_offline(entry: dict, examinee: str) -> dict:
    key = entry["key"]
    m = summary.empty_metrics()
    m["null_reasons"] = {"all": "offline collection, no metrics"}
    rec = {"key": key, "kind": "task", "tag": entry["tag"], "attempt": int(entry.get("attempt") or 1),
           "workdir": entry.get("workdir"),
           "collected_at": common.beijing_iso(common.now_beijing()), "notes": ["offline collection"],
           "task": entry["task"], "role": entry["role"], "repeat": entry.get("repeat") or 1,
           "thread_id": None, "thread_link": None, "prompt_exact": None, "metrics": m, "log": None,
           "contamination": summary.empty_contamination(False), "restarted": None, "attempts": None,
           "silent_empty": False, "final_text": ""}
    if entry.get("status") == "unsupported":
        rec["status"] = "unsupported"
        return rec
    work = Path(entry["workdir"])
    if not work.is_dir():
        rec.update({"status": "not_run", "work_copy": None})
        rec["notes"].append(f"working directory missing: {work}")
        return rec
    dest = common.RUNS / examinee / "work" / key
    if dest.exists():
        workdir.remove_tree(dest, common.RUNS / examinee / "work")
    rec["work_files"] = len(workdir.copy_files(work, dest))
    rec["work_copy"] = common.norm_path(str(dest))
    rec["status"] = "ok"
    return rec


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="Collect answers offline")
    ap.add_argument("--examinee", required=True)
    ap.add_argument("--only", nargs="*", help="only these keys")
    ap.add_argument("--offline", action="store_true", help="accepted for compatibility; collection is always offline")
    a = ap.parse_args(argv)
    run_dir = common.RUNS / a.examinee
    doc, problem = common.load_json(run_dir / "dispatch.json")
    if problem:
        print(f"cannot read {run_dir / 'dispatch.json'}: {problem}. Run make_workdir.py first.", file=sys.stderr)
        return 2
    want = set(a.only or [])
    counts: dict[str, int] = {}
    for entry in doc.get("entries") or []:
        if (want and entry["key"] not in want) or entry["kind"] != "task":
            continue
        rec = collect_offline(entry, a.examinee)
        common.write_json(run_dir / "collected" / f"{entry['key']}.json", rec, sort_keys=False)
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        print(f"{entry['key']}: {rec['status']}")
    print("collected: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
