"""判分：把一个考生收回来的作答判成分数。

用法：
  python -B grade.py --examinee <考生id> [--only 键 …]

读 runs/<考生id>/ 下的 card.json、dispatch.json、collected/*.json 和 work/，
写 runs/<考生id>/grade_result.json：每道题一条成绩记录（不含速度的题分、清零原因、逐项对错、守约、声明、陷阱、指标、污染），
每次探针一条记录。这一步不碰 会话库，可以反复跑。再跑 summarize.py 把所有考生汇总成 results/summary.json。

名片里 kind 是 baseline 的考生（自检用的参考作答、空作答、故意做错）不参与速度比较。
含速度的题分要和别的考生比，在 summarize.py 里算。

重派过的题：dispatch.json 的 history 里的旧尝试也照样判，记录上带 attempt 和 superseded=true，
汇总时取最新一次有效的尝试当主成绩，旧的留作历史。
名片核对：只能拿收卷时从 会话库读到的通道、模型、选项去比。读到了并且不一样，记环境事故、要重派；
读不到（库里没有模型名，或没有那个选项）就在记录里写明「名片未能核实」，不判错，也不当成核实过。
一道题判分出错不影响别的题：那道题记成环境事故并写明原因，最后退出码是 1。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import common, grading, probes, scoring, summary  # noqa: E402

GRADEABLE = ("ok", "late", "timeout", "error", "void")


def _model_name(v) -> str:
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            v = json.loads(v)
        except ValueError:
            pass
    if isinstance(v, (list, tuple)):
        v = "/".join(str(x) for x in v)  # [组名, 模型名] 变成「组名/模型名」
    return str(v).strip().casefold() if v is not None else ""


def _opt_key(k) -> str:
    """选项名统一：不分大小写和下划线；reasoningEffort、reasoning_effort、effort 是同一个东西。"""
    s = str(k).replace("_", "").replace("-", "").casefold()
    return "effort" if s in ("reasoningeffort", "effort") else s


def _same(a, b) -> bool:
    return a.strip().casefold() == b.strip().casefold() if isinstance(a, str) and isinstance(b, str) else a == b


def approved_channel(card: dict, rec: dict | None) -> dict | None:
    """名片 approved_channels 里维护者批准换用的通道（每条带 model、since、why、attempts）。
    库里实际的「组名/模型名」和其中一条逐字一致（不分大小写）才算，返回那一条；别的情形返回 None。
    只换组名/模型名：通道实例、选项（档位）、加速档仍按名片 target 核。"""
    got = _model_name((rec.get("model") or {}).get("model")) if isinstance(rec, dict) and isinstance(rec.get("model"), dict) else ""
    if not got or not isinstance(card.get("target"), dict):
        return None
    return next((a for a in card.get("approved_channels") or []
                 if isinstance(a, dict) and _model_name(a.get("model")) == got), None)


def target_check(card: dict, rec: dict | None, target: dict | None = None) -> tuple[list[str], list[str]]:
    """收卷时从 会话库里读到的通道、模型、选项，和名片 target 写的对不对。
    返回 (不一致的地方, 核实不了的地方)。两个都空才是核实过。

    读不到的不猜：记进第二个列表。模型名不分大小写；名片只写了模型名、库里是「组名/模型名」也算一致。
    选项名按 _opt_key 统一后再比。库里看到开了加速档（fastMode 是 true，或 Codex 的 serviceTier 是 priority）
    而名片的 card.fast 不是 true，算不一致（加速档要作为单独的考生）。
    这里核的是派活平台记下的模型选择，不是服务商后台最后真正跑的权重。"""
    if not isinstance(rec, dict) or not isinstance(card, dict):
        return [], ["没有收卷记录或名片"]
    if target is None and (alt := approved_channel(card, rec)):
        return target_check(card, rec, {**card["target"], "model": alt["model"]})
    target = target if target is not None else card.get("target") if isinstance(card.get("target"), dict) else {}
    seen = rec.get("model") if isinstance(rec.get("model"), dict) else {}
    out, unknown = [], []
    want_inst, got_inst = _model_name(target.get("provider")), _model_name(rec.get("driver"))
    if want_inst and got_inst and want_inst != got_inst:
        out.append(f"通道不对：名片写 {target.get('provider')}，实际是 {rec.get('driver')}")
    elif want_inst and not got_inst:
        unknown.append("库里读不到实际走的通道")
    want, got = _model_name(target.get("model")), _model_name(seen.get("model"))
    if want and got and want != got and not got.endswith("/" + want):
        out.append(f"模型不对：名片写 {target.get('model')}，实际是 {seen.get('model')}")
    elif want and not got:
        unknown.append("库里读不到实际的模型名")
    want_opts = target.get("options") if isinstance(target.get("options"), dict) else {}
    got_opts = seen.get("options") if isinstance(seen.get("options"), dict) else {}
    got_norm = {_opt_key(k): (k, v) for k, v in got_opts.items()}
    for k in sorted(want_opts):
        if _opt_key(k) in got_norm:
            gk, gv = got_norm[_opt_key(k)]
            if not _same(gv, want_opts[k]):
                out.append(f"选项 {k} 不对：名片写 {want_opts[k]!r}，实际是 {gk}={gv!r}")
        else:
            unknown.append(f"库里读不到选项 {k}")
    fast_card = (card.get("card") or {}).get("fast") is True if isinstance(card.get("card"), dict) else False
    if not fast_card and (got_opts.get("fastMode") is True
                          or str(got_opts.get("serviceTier") or "").casefold() == "priority"):
        out.append("实际开了加速档（fastMode 或 serviceTier=priority），名片的 card.fast 不是 true")
    return out, unknown


def target_problems(card: dict, rec: dict | None) -> list[str]:
    """只要不一致的那部分（老接口，保留）。"""
    return target_check(card, rec)[0]


def card_check(card: dict | None, rec: dict | None, baseline: bool) -> dict:
    """这次作答的名片核对结论：{status: verified / unverified / mismatch / not_applicable, problems, unverified}。"""
    if baseline or card is None:
        return {"status": "not_applicable", "problems": [], "unverified": []}
    wrong, unknown = target_check(card, rec)
    out = {"status": "mismatch" if wrong else "unverified" if unknown else "verified",
           "problems": wrong, "unverified": unknown}
    alt = approved_channel(card, rec)
    if alt and not wrong:
        out["approved_channel"] = {k: alt.get(k) for k in ("model", "since", "why")}
    return out


def _tag_attempt(run: dict, entry: dict, superseded: bool, check: dict | None = None) -> dict:
    run["attempt"] = int(entry.get("attempt") or 1)
    run["superseded"] = superseded
    if check is not None:
        run["card_check"] = check
        if check.get("approved_channel"):
            a = check["approved_channel"]
            run["notes"] = list(run.get("notes") or []) + [
                f"走的是名片上批准换用的通道 {a['model']}（{a['since']} 起，{a['why']}），不是名片 target 的通道"]
        if check["status"] == "unverified":
            run["notes"] = list(run.get("notes") or []) + ["名片未能核实：" + "；".join(check["unverified"])]
    return run


def wrong_target_notes(problems: list[str]) -> list[str]:
    return ["派错了，这次不算，要重派：" + "；".join(problems)]


def grade_task(entry: dict, rec: dict | None, model_id: str, cfg: dict, baseline: bool,
               card: dict | None = None) -> dict:
    meta = common.load_meta(entry["role"], entry["task"])
    repeat = int(entry.get("repeat") or 1)
    if entry.get("status") == "unsupported":
        return grading.assemble_run(model_id, meta, None, "unsupported", cfg, repeat=repeat,
                                    notes=[entry.get("note") or "考生不支持这道题需要的能力"])
    if rec is None:
        return grading.assemble_run(model_id, meta, None, "not_run", cfg, repeat=repeat, notes=["还没收卷"])
    status = rec.get("status") or "not_run"
    notes = [n for n in rec.get("notes") or [] if n]
    m = rec.get("metrics")
    con = rec.get("contamination")
    if con and (rec.get("contamination_detail") or {}).get("evidence"):
        con = {**con, "evidence": rec["contamination_detail"]["evidence"]}  # 哪条命令越界、作弊，汇总时会遮掉本机路径
    common_kw = dict(repeat=repeat, metrics=m, contamination=con,
                     thread_link=rec.get("thread_link"), restarted=rec.get("restarted"),
                     attempts=rec.get("attempts"), silent_empty=bool(rec.get("silent_empty")))
    wrong = [] if baseline else target_problems(card or {}, rec)
    if wrong and status != "not_run":
        # 跑的不是名片上的那个模型或档位：成绩不能记在这个考生头上，按环境事故处理（不计分），等重派
        return grading.assemble_run(model_id, meta, None, "env_error", cfg,
                                    notes=notes + wrong_target_notes(wrong), **common_kw)
    copy = rec.get("work_copy")
    if status not in GRADEABLE or not copy or not Path(copy).is_dir():
        if status in GRADEABLE and status != "void":
            notes.append(scoring.NOTE_NO_COPY)
        return grading.assemble_run(model_id, meta, None, status, cfg, notes=notes, **common_kw)
    log = rec.get("log") if isinstance(rec.get("log"), dict) and rec["log"].get("verifiable") else None
    work = Path(copy)
    if status == "ok" and not baseline and not (work / common.REPORT_NAME).is_file() and not any(
            (work / r).is_file() for r in meta.get("required_deliverables") or []):
        status = "error"  # 0 分计入，不算正常完成，也不算没跑
        notes.append(scoring.NOTE_UNDELIVERED)
    common.set_work_alias(work, rec.get("workdir"))
    try:
        g = grading.grade_work(entry["role"], entry["task"], work, log, source_links=rec.get("source_links"))
    finally:
        common.set_work_alias(work, None)
    return grading.assemble_run(model_id, meta, g, status, cfg, notes=notes, **common_kw)


def grade_probe(entry: dict, rec: dict | None, cfg: dict, card: dict | None = None) -> dict:
    pid = entry["probe"]
    base = {"probe": pid, "i": entry.get("i"), "order": entry.get("order"), "cold": False, "correct": None,
            "items": [], "notes": [], "metrics": summary.empty_metrics(),
            "contamination": summary.empty_contamination(), "thread_link": None}
    if entry.get("status") == "unsupported":
        return {**base, "status": "unsupported", "notes": [entry.get("note") or "考生不支持"]}
    if rec is None or rec.get("status") in (None, "not_run"):
        return {**base, "status": "not_run", "notes": [n for n in (rec or {}).get("notes") or [] if n]}
    status = rec["status"]
    con = rec.get("contamination") or summary.empty_contamination()
    notes = [n for n in rec.get("notes") or [] if n]
    wrong = target_problems(card or {}, rec)
    if wrong:
        status = "env_error"
        notes += wrong_target_notes(wrong)
    if status in ("ok", "timeout", "error") and any(con.get(k) for k in cfg["contamination"]["void_on"]):
        hard = [k for k in cfg["contamination"]["void_on"] if con.get(k)]
        status = "void"
        notes.append("作废：查出污染（" + "、".join(hard) + "）")
    out = {**base, "status": status, "metrics": summary.normalize_metrics(rec.get("metrics")),
           "contamination": con, "thread_link": rec.get("thread_link"), "notes": notes,
           "probe_valid": rec.get("probe_valid")}
    if status == "ok" and rec.get("probe_valid") is False:
        # 过程不合要求（该不用工具的用了工具，或 P6 没有真的往返五次）：这次的时间不能和别人比，答案也不算。
        # 不新增状态值，记成失败并带 probe_valid=false；无效的原因收卷时已经写在 notes 里。
        out["status"] = status = "error"
        try:
            has_answer = bool(probes.load_probe(pid)["key"].get("has_answer"))
        except (OSError, ValueError, KeyError, AttributeError):
            has_answer = False
        out["correct"] = False if has_answer else None
    if status == "ok":
        try:
            j = probes.judge(pid, rec.get("final_text"), probes.load_probe(pid)["key"])
        except (OSError, ValueError, KeyError) as e:
            out["notes"].append(f"探针判不了: {e}")
        else:
            out.update({"correct": j["correct"], "items": j["items"]})
            out["notes"] += j["notes"]
    return out


def mark_cold(attempts: list[dict]) -> None:
    """这个考生最早派出去的那次探针标成「冷」。"""
    dated = [(common.parse_ts(a["metrics"].get("dispatched_at")), a) for a in attempts
             if a["status"] not in ("not_run", "unsupported")]
    dated = [(t, a) for t, a in dated if t is not None]
    if dated:
        min(dated, key=lambda x: x[0])[1]["cold"] = True


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="把一个考生收回来的作答判成分数")
    ap.add_argument("--examinee", required=True)
    ap.add_argument("--only", nargs="*", help="只判这些键，只打印不写文件")
    a = ap.parse_args(argv)
    run_dir = common.RUNS / a.examinee
    card, bad = common.load_json(run_dir / "card.json")
    doc, bad2 = common.load_json(run_dir / "dispatch.json")
    if bad or bad2 or not isinstance(card, dict) or not isinstance(doc, dict):
        print(f"runs/{a.examinee}/ 里的 card.json 或 dispatch.json 读不了: {bad or bad2}", file=sys.stderr)
        return 2
    cfg = common.read_scoring()
    baseline = card.get("kind") == "baseline"
    want = set(a.only or [])
    runs, attempts, probe_history = [], [], []
    failed = 0
    # 先判被重派顶掉的旧尝试（留作历史），再判当前的尝试
    jobs = [(h, True) for h in doc.get("history") or [] if isinstance(h, dict) and "key" in h]
    jobs += [(e, False) for e in doc.get("entries") or []]
    for entry, superseded in jobs:
        key = entry["key"]
        if want and key not in want:
            continue
        if superseded:
            moved = entry.get("moved") if isinstance(entry.get("moved"), dict) else {}
            rec, problem = common.load_json(Path(moved["collected"])) if moved.get("collected") else (None, "没收过")
            if not problem and isinstance(rec, dict) and moved.get("work_copy"):
                rec = {**rec, "work_copy": moved["work_copy"]}
        else:
            rec, problem = common.load_json(run_dir / "collected" / f"{key}.json")
        if problem or not isinstance(rec, dict):
            rec = None
        check = card_check(card, rec, baseline) if rec is not None and rec.get("status") not in (
            None, "not_run", "unsupported") else None
        tag = f"{key}（第 {int(entry.get('attempt') or 1)} 次尝试，已被重派顶掉）" if superseded else key
        if entry["kind"] == "task":
            try:
                r = grade_task(entry, rec, a.examinee, cfg, baseline, card)
            except Exception as e:  # noqa: BLE001  一道题判分出错不能拖垮整批
                failed += 1
                r = grading.assemble_run(
                    a.examinee, common.load_meta(entry["role"], entry["task"]), None, "env_error", cfg,
                    repeat=int(entry.get("repeat") or 1),
                    notes=[f"判分程序出错，这道题没判成，别的题不受影响: {type(e).__name__}: {str(e)[:200]}"])
            if (rec or {}).get("prompt_addendum"):
                r["notes"] = list(r.get("notes") or []) + [f"题面末尾有名片批准的加注「{rec['prompt_addendum']}」"]
            runs.append(_tag_attempt(r, entry, superseded, check))
            score = "不计" if r["score"] is None else f"{r['score']:.1f}"
            extra = f"  判分器问题 {len(r['grader_problems'])} 条" if r.get("grader_problems") else ""
            print(f"{tag:12s} {r['status']:11s} 不含速度分 {score:>5s}  Q {r['quality_raw']:.2f}  "
                  f"清零 {r['zero'] or '-'}  谎报 {r['lies']}{extra}")
        else:
            try:
                p = grade_probe(entry, rec, cfg, None if baseline else card)
            except Exception as e:  # noqa: BLE001
                failed += 1
                p = {"probe": entry["probe"], "i": entry.get("i"), "order": entry.get("order"), "cold": False,
                     "correct": None, "items": [], "status": "env_error", "metrics": summary.empty_metrics(),
                     "contamination": summary.empty_contamination(), "thread_link": None,
                     "notes": [f"判分程序出错，这次探针没判成: {type(e).__name__}: {str(e)[:200]}"]}
            (probe_history if superseded else attempts).append(_tag_attempt(p, entry, superseded, check))
    mark_cold(attempts)
    for p in attempts:
        verdict = "不判" if p["correct"] is None else ("全对" if p["correct"] else "有错")
        total = p["metrics"].get("total_s")
        print(f"{p['probe']}#{p['i']}  {p['status']:11s} {verdict}  "
              f"{'取不到' if total is None else f'{total:.1f} 秒'}{'  冷' if p['cold'] else ''}")
    result = {
        "examinee": a.examinee, "graded_at": common.beijing_iso(common.now_beijing()),
        "suite_version": common.read_suite().get("suite_version"), "batch": doc.get("batch"),
        "model": {"id": a.examinee, "label": card.get("label") or a.examinee,
                  "short_label": card.get("short_label") or card.get("label") or a.examinee,
                  "vendor": card.get("vendor") or "", "color_key": card.get("color_key") or "other",
                  "kind": "baseline" if baseline else "examinee", "card": card.get("card") or {}},
        "runs": runs, "probe_attempts": attempts, "probe_history": probe_history,
    }
    unverified_n = sum(1 for r in runs + attempts if (r.get("card_check") or {}).get("status") == "unverified")
    if unverified_n:
        print(f"注意：有 {unverified_n} 次作答的名片没能核实（库里读不到模型名或选项），没有判错，也不算核实过。",
              file=sys.stderr)
    wrong_n = sum(1 for r in runs + attempts if any(str(n).startswith("派错了") for n in r.get("notes") or []))
    if wrong_n:
        print(f"注意：有 {wrong_n} 次跑的不是名片上写的通道、模型或档位，已经记成环境事故、不计分，要重派。"
              "细节在各条记录的 notes 里。", file=sys.stderr)
    if want:
        print("带了 --only，只打印不写文件（免得把别的题的成绩冲掉）。")
        return 0
    common.write_json(run_dir / "grade_result.json", result, sort_keys=False)
    print(f"判分结果 -> {run_dir / 'grade_result.json'}（岗位题 {len(runs)} 条，探针 {len(attempts)} 条）")
    if failed:
        print(f"有 {failed} 条判分时出错，已记成环境事故并写明原因。结果文件写了，但这几条要查。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
