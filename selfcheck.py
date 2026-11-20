"""题库质量自检。

用法：python -B selfcheck.py [岗位 或 题号 ...] [--keep] [--skip-regen]
不给参数就是全部正式岗位；样题要点名（python -B selfcheck.py SAMPLE）。

每道题查：
  1. 参考作答、空作答、故意做错三份，分数落在 scoring.json 的 selfcheck 门槛里。
  2. 审美题的每种傻瓜作答没有泄方向。
  3. 泄漏检查。
  4. 判分器的输出和 meta 里声明的对得上；同一份作答判两次结果一样。
每个岗位另查：
  5. 出题脚本连跑两次产物一致，并且和入库的题一致（--skip-regen 可跳过，题大时省时间）。

三份作答都按「刚好在目标时间交回」计时。结果写到 selfcheck/，三份作答的成绩记录另存在
selfcheck/baselines/，汇总时作为基线显示。作答目录建在系统临时目录里，不在 bench 里。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bench import common, grading, leak  # noqa: E402

KINDS = ("reference", "empty", "wrong")
REQUIRED_TASTE_DUMMIES = ("all_a", "all_shorter", "all_bigger_change")


def _fresh_copy(role: str, tid: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(common.TASKS / role / tid, dest)
    return dest


def _grade(role: str, tid: str, work: Path, meta: dict, cfg: dict, model_id: str) -> dict:
    g = grading.grade_work(role, tid, work, None)
    return grading.assemble_run(model_id, meta, g, "ok", cfg, notes=["自检基线"])


def _expect(kind: str, rec: dict, cfg: dict) -> tuple[bool, str]:
    sc = cfg["selfcheck"]
    s = rec["score"]
    if kind == "reference":
        return (s >= sc["reference_min"] and rec["passed"]), f"需要不低于 {sc['reference_min']} 分并且整题合格"
    if kind == "empty":
        return s <= sc["empty_max"], f"需要不高于 {sc['empty_max']} 分"
    return (sc["wrong_min_exclusive"] < s <= sc["wrong_max"] or rec.get("zero") == "lie"), \
        f"需要高于 {sc['wrong_min_exclusive']}、不高于 {sc['wrong_max']} 分"


def _stable_view(rec: dict) -> dict:
    return {k: rec[k] for k in ("score", "quality", "items", "constraints", "claims", "traps", "honesty", "taste",
                                "rule_checks", "quality_parts")}


MUST_FAIL, MAY_PASS = "must_fail", "may_pass"


def dummy_plan(grader, meta: dict) -> tuple[list[dict], list[str]]:
    """这道题要跑哪些傻瓜作答。返回 ([{id, make, expect, why}], 问题)。

    策略从判分器的两对钩子里来，写法一样：dummy_strategies(meta) 配 make_dummy(work, key, meta, strategy)，
    regression_strategies(meta) 配 make_regression(work, key, meta, strategy)。列表里每一项是策略名，
    或者 {"id": 策略名, "expect": "must_fail" 或 "may_pass", "why": 理由}。
    期望默认是必须不合格。按题目给的材料正常做就该得到的正确交付
    可以登记成允许合格：在上面的对象里写，或者在 meta.dummy_expect 里写 {策略名: {"expect": "may_pass",
    "why": 理由}}（meta 里的优先）。只作参考的题（meta.reference_only）默认允许合格。"""
    plan, problems, seen = [], [], set()
    override = meta.get("dummy_expect") if isinstance(meta.get("dummy_expect"), dict) else {}
    for list_fn, make_fn in (("dummy_strategies", "make_dummy"), ("regression_strategies", "make_regression")):
        if not callable(getattr(grader, list_fn, None)):
            continue
        if not callable(getattr(grader, make_fn, None)):
            problems.append(f"判分器有 {list_fn} 却没有 {make_fn}")
            continue
        for x in getattr(grader, list_fn)(meta) or []:
            row = dict(x) if isinstance(x, dict) else {"id": x}
            sid = str(row.get("id"))
            if sid in seen:
                continue
            seen.add(sid)
            o = override.get(sid)
            o = {"expect": o} if isinstance(o, str) else o if isinstance(o, dict) else {}
            expect = o.get("expect") or row.get("expect") or (MAY_PASS if meta.get("reference_only") else MUST_FAIL)
            why = str(o.get("why") or row.get("why") or meta.get("reference_only_reason") or "").strip()
            if expect not in (MUST_FAIL, MAY_PASS):
                problems.append(f"傻瓜作答 {sid} 的期望只能是 {MUST_FAIL} 或 {MAY_PASS}，现在是 {expect!r}")
                expect = MUST_FAIL
            if expect == MAY_PASS and not why:
                problems.append(f"傻瓜作答 {sid} 登记成允许合格，必须写理由（why）")
            plan.append({"id": sid, "make": getattr(grader, make_fn), "expect": expect, "why": why})
    return plan, problems


def check_dummies(role: str, tid: str, meta: dict, grader, cfg: dict, tmp: Path) -> tuple[list[dict], list[str], list[str]]:
    """跑傻瓜作答。返回 (每种策略的结果, 问题, 提醒)。

    每种策略都查合不合格（必须不合格的合格了就是问题；登记成允许合格的不报错）。
    判分器返回了 taste 计数的，再查有没有泄方向。"""
    rows, problems, warnings = [], [], []
    plan, problems = dummy_plan(grader, meta)
    strategies = [p["id"] for p in plan]
    if role == "TASTE":
        na = meta.get("dummy_na") or {}
        if not hasattr(grader, "make_dummy"):
            problems.append("审美题的判分器必须实现 dummy_strategies 和 make_dummy")
        for need in REQUIRED_TASTE_DUMMIES:
            if need not in strategies and need not in na:
                problems.append(f"缺傻瓜策略 {need}（不适用就在 meta.dummy_na 里写理由）")
    dc = cfg["dummy"]
    for p in plan:
        strategy = p["id"]
        work = _fresh_copy(role, tid, tmp / f"dummy_{strategy}" / tid)
        try:
            p["make"](work, common.GRADING / role / tid, meta, strategy)
        except Exception as e:  # noqa: BLE001
            problems.append(f"傻瓜作答 {strategy} 构造失败: {e!r}")
            continue
        rec = _grade(role, tid, work, meta, cfg, f"dummy-{strategy}")
        verdict = {"strategy": strategy, "kind": None, "correct": None, "n": None, "rate": None,
                   "quality": rec["quality"], "score": round(rec["score"], 2), "passed": rec["passed"],
                   "expect": p["expect"], "why": p["why"], "ok": not (rec["passed"] and p["expect"] == MUST_FAIL)}
        rows.append(verdict)
        if not verdict["ok"]:
            problems.append(f"傻瓜作答 {strategy} 合格了（{rec['score']:.1f} 分，Q={rec['quality']:.3f}）。"
                            f"不看内容的策略不能合格；如果这是按题目给的材料正常做就该得到的正确交付，"
                            f"登记成 {MAY_PASS} 并写理由")
        elif rec["passed"]:
            warnings.append(f"傻瓜作答 {strategy} 合格了，已登记为允许合格: {p['why']}")
        taste = rec.get("taste") or {}
        kinds = [k for k in common.TASTE_KINDS if taste.get(k)]
        if not kinds and role == "TASTE":
            problems.append(f"傻瓜作答 {strategy}: 判分器没有返回 taste 计数，没法判方向")
        for k in kinds:
            c, n, chance = taste[k]["correct"], taste[k]["n"], taste[k]["chance"]
            rate = c / n if n else 0.0
            hi = float(dc["max_rate"]) if abs(chance - 0.5) < 1e-9 else chance + float(dc["kchoice_margin"])
            lo = 1.0 - float(dc["max_rate"]) if abs(chance - 0.5) < 1e-9 else None
            row = {"strategy": strategy, "kind": k, "correct": c, "n": n, "rate": round(rate, 4),
                   "quality": rec["quality"], "passed": rec["passed"], "expect": p["expect"], "ok": True}
            if n < int(dc["min_groups"]):
                warnings.append(f"傻瓜作答 {strategy}（{k}）只有 {n} 组，不到 {dc['min_groups']} 组，判不了方向；"
                                f"这次对了 {c} 组。请确认出题时已经配平。")
            elif rate >= hi - 1e-9:
                row["ok"] = False
                problems.append(f"傻瓜作答 {strategy}（{k}）对了 {c}/{n}，正确率 {rate:.0%} 达到 {hi:.0%}，题泄了方向")
            elif lo is not None and rate <= lo + 1e-9:
                row["ok"] = False
                problems.append(f"傻瓜作答 {strategy}（{k}）只对了 {c}/{n}，正确率 {rate:.0%} 低到 {lo:.0%}，"
                                f"反着选就能拿高分，题泄了方向")
            rows.append(row)
    return rows, problems, warnings


def check_task(role: str, tid: str, cfg: dict, tmp: Path) -> dict:
    """查一道题。返回 {task, rows, problems, warnings, records}。"""
    key = common.GRADING / role / tid
    problems: list[str] = []
    warnings: list[str] = []
    meta, bad = common.load_json(key / "meta.json")
    if bad:
        return {"task": tid, "role": role, "problems": [f"meta.json {bad}"], "warnings": [], "kinds": {},
                "dummies": [], "records": {}}
    problems += ["meta: " + x for x in common.validate_meta(meta, role, tid)]
    if problems:
        return {"task": tid, "role": role, "problems": problems, "warnings": [], "kinds": {}, "dummies": [],
                "records": {}}
    manifest_now = common.build_manifest(common.TASKS / role / tid)
    if manifest_now != common.read_json(key / "manifest.json"):
        problems.append("tasks 目录和 manifest.json 对不上（改过题面后要重跑出题脚本）")
    try:
        grader = grading.load_grader(role)
    except Exception as e:  # noqa: BLE001
        return {"task": tid, "role": role, "problems": [f"判分器加载失败: {e!r}"], "warnings": [], "kinds": {},
                "dummies": [], "records": {}}
    for fn in ("grade", "make_reference", "make_wrong"):
        if not callable(getattr(grader, fn, None)):
            problems.append(f"判分器缺函数 {fn}")
    kinds: dict[str, dict] = {}
    records: dict[str, dict] = {}
    for kind in KINDS:
        work = _fresh_copy(role, tid, tmp / kind / tid)
        try:
            if kind == "reference":
                grader.make_reference(work, key, meta)
            elif kind == "wrong":
                grader.make_wrong(work, key, meta)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{kind} 作答构造失败: {e!r}")
        t0 = time.monotonic()
        rec = _grade(role, tid, work, meta, cfg, f"baseline-{kind}")
        seconds = time.monotonic() - t0
        ok, rule = _expect(kind, rec, cfg)
        kinds[kind] = {"score": round(rec["score"], 2), "quality": round(rec["quality"], 4),
                       "zero": rec["zero"], "lies": rec["lies"], "passed": rec["passed"],
                       "ok": ok, "grade_seconds": round(seconds, 2)}
        records[kind] = rec
        if not ok:
            problems.append(f"{kind}: {rec['score']:.1f} 分，Q={rec['quality']:.3f}，谎报 {rec['lies']}，"
                            f"合格={rec['passed']}（{rule}）")
        for p in rec["grader_problems"]:
            problems.append(f"{kind}: 判分器输出有问题: {p}")
        if seconds > 30:
            problems.append(f"{kind}: 判分用了 {seconds:.0f} 秒，超过 30 秒")
        if kind == "reference":
            if _stable_view(_grade(role, tid, work, meta, cfg, "baseline-reference")) != _stable_view(rec):
                problems.append("同一份参考作答判两次，结果不一样")
            if rec["lies"]:
                problems.append("参考作答里有被判成矛盾的声明")
            bad_cons = [c["id"] for c in rec["constraints"] if not c["ok"]]
            if bad_cons:
                problems.append("参考作答没守住约束: " + ", ".join(bad_cons))
            unhandled = [t["type"] for t in rec["traps"] if not t["handled"]]
            if unhandled:
                problems.append("参考作答没处理陷阱: " + ", ".join(unhandled))
            if meta["item_count"] != len(rec["items"]):
                warnings.append(f"meta.item_count 是 {meta['item_count']}，判分器给了 {len(rec['items'])} 个小项")
        if kind == "empty" and any(v > 0 for v in rec["quality_parts"].values()):
            problems.append("空作答有分项得分不是 0")
    dummies, dp, dw = check_dummies(role, tid, meta, grader, cfg, tmp)
    problems += dp
    warnings += dw
    problems += ["泄漏: " + x for x in leak.check_task(role, tid)]
    return {"task": tid, "role": role, "difficulty": meta["difficulty"], "title": meta["title"],
            "kinds": kinds, "dummies": dummies, "problems": problems, "warnings": warnings, "records": records}


def _tree_hashes(root: Path, role: str) -> dict[str, str]:
    out = {}
    for sub in ("tasks", "grading"):
        base = root / sub / role
        if not base.is_dir():
            continue
        for rel in common.list_files(base):
            if sub == "grading" and "/" not in rel:
                continue  # grading/<岗位>/ 根上的手写文件（grader.py、DESIGN.md）不是生成物
            out[f"{sub}/{role}/{rel}"] = common.sha256_file(base / rel)
    return out


def check_regen(role: str, tmp: Path) -> list[str]:
    """出题脚本连跑两次到两个临时目录，比较产物；再和入库的题比较。"""
    gen = common.GENERATORS / f"{role}.py"
    if not gen.is_file():
        return [f"没有出题脚本 generators/{role}.py"]
    trees = []
    for i in (1, 2):
        out_root = tmp / f"regen{i}"
        r = common.run_cmd([sys.executable, "-B", str(gen), "--out-root", str(out_root)], cwd=common.ROOT,
                           timeout=3600)
        if r["code"] != 0:
            tail = (r["stdout"] + "\n" + r["stderr"]).strip()[-600:]
            return [f"出题脚本第 {i} 次运行失败（退出码 {r['code']}）: {tail}"]
        trees.append(_tree_hashes(out_root, role))
    problems = []
    a, b = trees
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if diff:
        problems.append("连跑两次产物不一致: " + ", ".join(diff[:12]) + ("…" if len(diff) > 12 else ""))
    here = _tree_hashes(common.ROOT, role)
    diff = sorted(k for k in set(a) | set(here) if a.get(k) != here.get(k))
    if diff:
        problems.append("入库的题和出题脚本现在生成的不一致（要重跑出题脚本）: " + ", ".join(diff[:12])
                        + ("…" if len(diff) > 12 else ""))
    return problems


def run_role_checks(roles: list[str]) -> list[dict]:
    """跑岗位自己的常设检查脚本（suite.json 的 role_checks 里登记的），一个脚本只跑一次。

    这些脚本里的策略是岗位自己造作答、自己判的，没法拆成判分器钩子逐条接进来，所以整份跑、看退出码：
    非零就是有不该合格的合格了。脚本的明细结果在它自己写的文件里。"""
    out = []
    for item in common.read_json(common.ROOT / "suite.json").get("role_checks") or []:
        mine = sorted(set(item.get("roles") or []) & set(roles))
        script = common.ROOT / str(item.get("script"))
        if not mine:
            continue
        if not script.is_file():
            out.append({"script": item.get("script"), "roles": mine, "ok": False, "code": None, "tail": "脚本不存在"})
            continue
        r = common.run_cmd([sys.executable, "-B", "-X", "utf8", str(script)], cwd=common.ROOT, timeout=3600)
        tail = (r["stdout"] + "\n" + r["stderr"]).strip()[-400:]
        out.append({"script": item.get("script"), "roles": mine, "ok": r["code"] == 0, "code": r["code"],
                    "tail": tail, "note": item.get("note", "")})
    return out


def write_report(results: list[dict], regen: dict[str, list[str]], suffix: str, seconds: float, cfg: dict,
                 role_checks: list[dict] | None = None) -> bool:
    sc = cfg["selfcheck"]
    role_checks = role_checks or []
    n_problems = (sum(len(r["problems"]) for r in results) + sum(len(v) for v in regen.values())
                  + sum(1 for c in role_checks if not c["ok"]))
    doc = {"generated_at": common.beijing_iso(common.now_beijing()), "tasks": len(results),
           "all_ok": n_problems == 0, "seconds": round(seconds, 1),
           "regen": regen, "role_checks": role_checks,
           "rows": [{k: v for k, v in r.items() if k != "records"} for r in results]}
    common.write_json(common.SELFCHECK / f"results{suffix}.json", doc)
    lines = [f"# 自检结果{suffix.replace('_', ' ')}", "",
             f"{doc['generated_at']}。题数 {len(results)}；不符合期望 {n_problems} 条；用时 {doc['seconds']} 秒。", "",
             f"门槛：参考作答不低于 {sc['reference_min']} 分并且整题合格；空作答不高于 {sc['empty_max']} 分；"
             f"故意做错高于 {sc['wrong_min_exclusive']}、不高于 {sc['wrong_max']} 分（不含速度的题分）。", "",
             "| 题 | 难度 | 参考 | 空作答 | 故意做错（Q，谎报） | 傻瓜作答 | 符合 |", "|---|---|---:|---:|---:|---|---|"]
    for r in results:
        k = r.get("kinds") or {}
        if not k:
            lines.append(f"| {r['task']} | | | | | | 不符合 |")
            continue
        w = k["wrong"]
        dummies = "；".join(
            f"{d['strategy']} {d['correct']}/{d['n']}" if d.get("kind") else
            f"{d['strategy']} {'合格' if d['passed'] else '不合格'}" + ("（允许）" if d["expect"] == MAY_PASS else "")
            for d in r["dummies"]) or "没有登记"
        lines.append(f"| {r['task']} | {r.get('difficulty', '')} | {k['reference']['score']} | {k['empty']['score']} | "
                     f"{w['score']}（{w['quality']}，{w['lies']}） | {dummies} | {'符合' if not r['problems'] else '不符合'} |")
    lines += ["", "## 出题脚本连跑两次", ""]
    if not regen:
        lines.append("这次没有查（--skip-regen，或只点了题号）。")
    for role, ps in sorted(regen.items()):
        lines.append(f"- {role}: " + ("一致，并且和入库的题一致。" if not ps else "不符合。"))
        lines += [f"  - {p}" for p in ps]
    lines += ["", "## 岗位常设检查", ""]
    if not role_checks:
        lines.append("这次没有跑（--skip-role-checks、只点了题号，或这些岗位没有登记脚本）。"
                     "判分器钩子里登记的傻瓜作答在上表的「傻瓜作答」一栏。")
    for c in role_checks:
        lines.append(f"- {c['script']}（{'、'.join(c['roles'])}）: " + ("通过。" if c["ok"] else f"不符合（退出码 {c['code']}）。"))
        if not c["ok"]:
            lines.append(f"  - {c['tail']}")
    bad = [r for r in results if r["problems"]]
    if bad:
        lines += ["", "## 不符合期望", ""]
        for r in bad:
            lines += [f"- {r['task']}"] + [f"  - {p}" for p in r["problems"]]
    warn = [r for r in results if r["warnings"]]
    if warn:
        lines += ["", "## 提醒（不算不符合）", ""]
        for r in warn:
            lines += [f"- {r['task']}"] + [f"  - {p}" for p in r["warnings"]]
    common.write_text(common.SELFCHECK / f"SELFCHECK{suffix}.md", "\n".join(lines) + "\n")
    return n_problems == 0


def main() -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="题库质量自检")
    ap.add_argument("targets", nargs="*", help="岗位名或题号；不给就是全部正式岗位")
    ap.add_argument("--keep", action="store_true", help="保留临时作答目录，方便排查")
    ap.add_argument("--skip-regen", action="store_true", help="不查出题脚本连跑两次")
    ap.add_argument("--skip-role-checks", action="store_true", help="不跑岗位自己的常设检查脚本")
    a = ap.parse_args()
    cfg = common.read_scoring()
    tasks = common.select_tasks(a.targets or None)
    if not tasks:
        print("没有选中任何题。先跑出题脚本，或检查岗位名、题号。")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="vbsc_"))
    t0 = time.monotonic()
    results = []
    try:
        for role, tid in tasks:
            r = check_task(role, tid, cfg, tmp)
            results.append(r)
            k = r.get("kinds") or {}
            line = f"{tid:8s}"
            if k:
                line += (f" 参考 {k['reference']['score']:6.1f}  空 {k['empty']['score']:5.1f}"
                         f"  做错 {k['wrong']['score']:5.1f}")
            line += "" if not r["problems"] else f"  不符合 {len(r['problems'])} 条"
            print(line, flush=True)
            for kind, rec in r["records"].items():
                common.write_json(common.SELFCHECK / "baselines" / kind / f"{tid}.json", rec)
        regen: dict[str, list[str]] = {}
        wanted = {str(x).upper() for x in a.targets}
        whole_roles = sorted({role for role, _ in tasks if not wanted or role.upper() in wanted})
        if not a.skip_regen:
            for role in whole_roles:
                regen[role] = check_regen(role, tmp / f"regen_{role}")
                print(f"{role:8s} 出题脚本连跑两次: " + ("一致" if not regen[role] else "不符合"), flush=True)
        role_checks = [] if a.skip_role_checks else run_role_checks(whole_roles)
        for c in role_checks:
            print(f"{c['script']}: " + ("通过" if c["ok"] else f"不符合（退出码 {c['code']}）"), flush=True)
    finally:
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    suffix = "" if not a.targets else "_" + "_".join(str(x).upper().replace("/", "-") for x in a.targets)
    ok = write_report(results, regen, suffix, time.monotonic() - t0, cfg, role_checks)
    print(f"\n{len(results)} 道题，{'全部符合' if ok else '有不符合的'} -> selfcheck/SELFCHECK{suffix}.md")
    if a.keep:
        print(f"临时作答目录保留在: {tmp}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
