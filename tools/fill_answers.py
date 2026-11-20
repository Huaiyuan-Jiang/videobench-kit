"""往一个「基线考生」的工作目录里放现成的作答：参考作答、空作答或故意做错。

用途：不派任何模型，就把「建目录、收卷、判分、汇总」整条链路走一遍。先用 make_workdir.py 建好目录
（名片里 kind 写 baseline），再跑这个脚本，然后 collect.py --offline、grade.py、summarize.py。

用法：python -B tools/fill_answers.py --examinee <考生id> --kind reference|empty|wrong

只往派活清单里列的工作目录写（都在 bench 外的工作目录根里）。空作答就是什么都不放。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import common, grading  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="往基线考生的工作目录里放现成的作答")
    ap.add_argument("--examinee", required=True)
    ap.add_argument("--kind", required=True, choices=("reference", "empty", "wrong"))
    a = ap.parse_args(argv)
    run_dir = common.RUNS / a.examinee
    card, bad = common.load_json(run_dir / "card.json")
    doc, bad2 = common.load_json(run_dir / "dispatch.json")
    if bad or bad2:
        print(f"runs/{a.examinee}/ 里的 card.json 或 dispatch.json 读不了: {bad or bad2}", file=sys.stderr)
        return 2
    if card.get("kind") != "baseline":
        print("只给名片里 kind 是 baseline 的考生放现成作答，免得把真考生的目录写脏。", file=sys.stderr)
        return 2
    n = 0
    for e in doc.get("entries") or []:
        if e.get("kind") != "task" or e.get("status") != "ready":
            continue
        work = Path(e["workdir"])
        if not work.is_dir():
            print(f"{e['key']}: 工作目录不在 {work}", file=sys.stderr)
            return 1
        key = common.GRADING / e["role"] / e["task"]
        meta = common.load_meta(e["role"], e["task"])
        grader = grading.load_grader(e["role"])
        if a.kind == "reference":
            grader.make_reference(work, key, meta)
        elif a.kind == "wrong":
            grader.make_wrong(work, key, meta)
        n += 1
        print(f"{e['key']}: 放了{'参考作答' if a.kind == 'reference' else '做错的作答' if a.kind == 'wrong' else '空作答（什么都没放）'}")
    print(f"共 {n} 道题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
