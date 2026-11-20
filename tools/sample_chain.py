"""用样题把整条链路走一遍，不派任何模型：建考生目录、放现成作答、收卷、判分、汇总。

用法：python -B tools/sample_chain.py [--batch chain01]

三个「基线考生」分别放参考作答、空作答、故意做错的作答，工作目录建在工作目录根（系统临时目录下的 vbwork，VIDEOBENCH_WORK 可改）/<批次>/ 下，
最后汇总成 results/summary_sample_chain.json，并按自检的分数线检查三份作答的得分。
可以反复跑：已经建过的目录不重建，作答会重新放一遍。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import collect  # noqa: E402
import fill_answers  # noqa: E402
import grade  # noqa: E402
import make_workdir  # noqa: E402
import summarize  # noqa: E402
from bench import common, workdir  # noqa: E402

EXAMINEES = [("chain-ref", "reference", "链路·参考作答", "链路参考"),
             ("chain-empty", "empty", "链路·空作答", "链路空作答"),
             ("chain-wrong", "wrong", "链路·故意做错", "链路做错")]


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="用样题把整条链路走一遍")
    ap.add_argument("--batch", default="chain01")
    a = ap.parse_args(argv)
    for ex, kind, label, short in EXAMINEES:
        # 每次从头来：上一次留下的工作目录是按当时的题面建的，题重新出过以后再用它，题面会被当成考生改的
        old_work = workdir.VBWORK / a.batch / workdir.dir_slug(ex)
        if old_work.exists():
            workdir.remove_tree(old_work, workdir.VBWORK)
        if (common.RUNS / ex).exists():
            workdir.remove_tree(common.RUNS / ex, common.RUNS)
        card = dict(make_workdir.CARD_TEMPLATE)
        card.update({"id": ex, "label": label, "short_label": short, "vendor": "基线", "color_key": "baseline",
                     "kind": "baseline", "title_prefix": "链路"})
        card["card"] = {**card["card"], "channel": "链路验证", "model": label, "image_input": None,
                        "video_input": None, "notes": "不派模型，放现成作答，只为验证工具链"}
        common.write_json(common.RUNS / ex / "card.json", card, sort_keys=False)
        steps = [
            ("建目录", make_workdir.main, ["--batch", a.batch, "--examinee", ex, "--only", "SAMPLE"]),
            ("放作答", fill_answers.main, ["--examinee", ex, "--kind", kind]),
            ("收卷", collect.main, ["--examinee", ex, "--offline"]),
            ("判分", grade.main, ["--examinee", ex]),
        ]
        for name, fn, args in steps:
            print(f"--- {ex}: {name}")
            code = fn(args)
            if code != 0:
                print(f"{ex} 在「{name}」这一步失败（退出码 {code}）", file=sys.stderr)
                return 1
    out = common.RESULTS / "summary_sample_chain.json"
    print("--- 汇总")
    code = summarize.main(["--examinees"] + [e[0] for e in EXAMINEES] + ["--only", "SAMPLE", "--no-baselines",
                                                                        "--out", str(out)])
    if code != 0:
        return 1
    doc = common.read_json(out)
    cfg = common.read_scoring()["selfcheck"]
    bad = []
    print("--- 对照自检的分数线")
    for r in doc["runs"]:
        s = r["score"]
        if r["model"] == "chain-ref":
            ok = s is not None and s >= cfg["reference_min"] and r["passed"]
        elif r["model"] == "chain-empty":
            ok = s is not None and s <= cfg["empty_max"]
        else:
            ok = s is not None and (cfg["wrong_min_exclusive"] < s <= cfg["wrong_max"] or r.get("zero") == "lie")
        print(f"{r['model']:12s} {r['task']:8s} {s if s is None else round(s, 2)!s:>6s}  {'符合' if ok else '不符合'}")
        if not ok:
            bad.append(f"{r['model']} {r['task']}")
    if bad:
        print("不符合分数线: " + "、".join(bad), file=sys.stderr)
        return 1
    print(f"整条链路走通，汇总在 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
