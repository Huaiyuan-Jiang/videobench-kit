"""汇总：把所有考生的判分结果合成 results/summary.json，给榜单页用。

用法：
  python -B summarize.py [--examinees id …] [--only 岗位 …] [--include-sample] [--no-baselines] [--out 文件]

读 runs/*/grade_result.json（grade.py 写的）和 selfcheck/baselines/（selfcheck.py 写的参考作答、空作答、
故意做错三条基线），按 docs/RESULTS_SCHEMA.md 产出。产出前做两道检查，任何一道不过都不写文件：
  1. 格式校验（bench.schema）。
  2. 页面文字的字数（标题不超过 10 个字，一句话不超过 20 个字）。
样题岗位默认不进汇总；--include-sample 才带上，--only SAMPLE 只汇总样题。
suite.json 的 withdrawn_tasks、withdrawn_examinees 里列的题和考生不收，他们的判分记录照留。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import common, schema, summary  # noqa: E402

BASELINES = [("baseline-reference", "reference", "参考作答"), ("baseline-wrong", "wrong", "故意做错"),
             ("baseline-empty", "empty", "空作答")]
BASELINE_CARD = {"channel": "自检基线", "model": None, "effort": None, "fast": False, "context_tokens": None,
                 "image_input": None, "video_input": None, "max_parallel": None, "quota_pool": None,
                 "quota_reset": None, "plan_price": None, "notes": "自检作答，不是考生。速度和用量不适用。"}


def load_examinees(only: list[str] | None) -> tuple[list[dict], list[str]]:
    models, problems = [], []
    if not common.RUNS.is_dir():
        return models, problems
    for d in sorted(p for p in common.RUNS.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if only and d.name not in only:
            continue
        path = d / "grade_result.json"
        if not path.is_file():
            if only:
                problems.append(f"{d.name}: 没有 grade_result.json，先跑 grade.py")
            continue
        res, bad = common.load_json(path)
        if bad or not isinstance(res, dict) or not isinstance(res.get("model"), dict):
            problems.append(f"{d.name}: grade_result.json 读不了: {bad or '格式不对'}")
            continue
        m = dict(res["model"])
        m["card"] = {**{k: None for k in schema.CARD_KEYS}, **(m.get("card") or {})}
        m["runs"] = res.get("runs") or []
        m["probe_attempts"] = res.get("probe_attempts") or []
        models.append(m)
    for name in only or []:
        if not (common.RUNS / name).is_dir():
            problems.append(f"没有这个考生的目录: runs/{name}")
    return models, problems


def load_baselines(metas: list[dict]) -> list[dict]:
    """selfcheck 留下的三条基线。某一条一道题都没有就不出这条。"""
    out = []
    for bid, kind, label in BASELINES:
        runs = []
        for meta in metas:
            rec, bad = common.load_json(common.SELFCHECK / "baselines" / kind / f"{meta['id']}.json")
            if not bad and isinstance(rec, dict) and rec.get("task") == meta["id"]:
                rec = dict(rec)
                rec["model"] = bid
                runs.append(rec)
        if runs:
            out.append({"id": bid, "label": label, "short_label": label, "vendor": "基线", "color_key": "baseline",
                        "kind": "baseline", "card": dict(BASELINE_CARD), "runs": runs, "probe_attempts": []})
    return out


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="把所有考生的判分结果汇总成 results/summary.json")
    ap.add_argument("--examinees", nargs="*", help="只汇总这些考生，默认是 runs/ 下所有判过分的")
    ap.add_argument("--only", nargs="*", help="只汇总这些岗位")
    ap.add_argument("--include-sample", action="store_true", help="把样题岗位也带上")
    ap.add_argument("--no-baselines", action="store_true", help="不带自检的三条基线")
    ap.add_argument("--out", default=str(common.RESULTS / "summary.json"))
    ap.add_argument("--generated-at", help="把 generated_at 固定成这个时间（带时区的 ISO 写法）。"
                                           "同一批成绩要逐字节重现汇总文件时用；不给就是现在的北京时间")
    a = ap.parse_args(argv)
    cfg = common.read_scoring()
    suite = common.read_suite()
    tasks = [t for t in common.select_tasks(a.only, a.include_sample) if t[1] not in suite.get("withdrawn_tasks", {})]
    if not tasks:
        print("没有可汇总的题。", file=sys.stderr)
        return 2
    metas = []
    bad_meta = []
    for role, tid in tasks:
        meta = common.load_meta(role, tid)
        bad_meta += [f"{tid}: {x}" for x in common.validate_meta(meta, role, tid)]
        metas.append(meta)
    if bad_meta:
        print(f"题目元数据有 {len(bad_meta)} 个问题，先让出题的人改：", file=sys.stderr)
        for x in bad_meta[:40]:
            print("  - " + x, file=sys.stderr)
        return 1
    models, problems = load_examinees(a.examinees)
    models = [m for m in models if m["id"] not in suite.get("withdrawn_examinees", {})]
    if problems:
        for x in problems:
            print("考生: " + x, file=sys.stderr)
        return 1
    if not a.no_baselines:
        have = {m["id"] for m in models}
        models += [b for b in load_baselines(metas) if b["id"] not in have]
    if not models:
        print("还没有任何成绩：runs/ 下没有 grade_result.json，selfcheck/baselines/ 里也没有基线。", file=sys.stderr)
        return 1
    ids = [m["id"] for m in models]
    if len(ids) != len(set(ids)):
        print("考生 id 有重复。", file=sys.stderr)
        return 1
    stamp = common.beijing_iso(a.generated_at) if a.generated_at else common.beijing_iso(common.now_beijing())
    if not stamp:
        print(f"--generated-at 不是能认的时间: {a.generated_at}", file=sys.stderr)
        return 2
    doc = summary.build_summary(suite, metas, models, cfg, stamp)
    bad = summary.schema_problems(doc)  # 就是 schema.validate_summary，只更正了「重派过的题被当成重复」这一处
    if bad:
        print(f"汇总没通过格式校验（{len(bad)} 条），没有写文件：", file=sys.stderr)
        for x in bad[:40]:
            print("  - " + x, file=sys.stderr)
        return 1
    errors, warnings = summary.display_text_problems(doc)
    if errors:
        print(f"页面文字超了字数（{len(errors)} 条），没有写文件：", file=sys.stderr)
        for x in errors[:60]:
            print("  - " + x, file=sys.stderr)
        return 1
    common.write_json(a.out, doc, sort_keys=False)
    examinees = sum(1 for m in doc["models"] if m["kind"] == "examinee")
    print(f"汇总 -> {a.out}")
    print(f"考生 {examinees} 个，基线 {len(doc['models']) - examinees} 条，题 {len(doc['tasks'])} 道，"
          f"作答 {len(doc['runs'])} 条，探针 {len(doc['probe_runs'])} 条。")
    if warnings:
        print(f"提醒：题目详情里有 {len(warnings)} 句超过 20 个字（出题的人写的，没有拦）：")
        for x in warnings[:10]:
            print("  - " + x)
    if not examinees:
        print("注意：里面没有真考生，只有基线。榜单页需要至少一个考生才成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
