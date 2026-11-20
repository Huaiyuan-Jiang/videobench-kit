"""把判好分的作答汇总成 results/summary.json 的内容。格式见 docs/RESULTS_SCHEMA.md（含末尾的修订）。

纯函数：输入是题目元数据、每个考生的判分记录和探针记录，输出是一个字典。不读写文件。
"""
from __future__ import annotations

import re
from typing import Any

from . import common, redact, scoring

SCHEMA_VERSION = 2  # 2026-11-17 换成现在的题分：每题一个分，两个总分（含速度、不含速度）

# runs[].metrics 的全部键。取不到的一律 None。顺序就是输出顺序。
METRIC_KEYS = (
    "dispatched_at", "started_at", "first_event_at", "first_text_at", "first_deliverable_at", "delivered_at",
    "startup_s", "first_event_s", "first_text_s", "wall_s", "model_s", "tool_s", "think_s", "write_s",
    "other_wait_s", "approval_wait_s", "selfcheck_tail_s", "split_ok", "speed_t_s",
    "turns", "turns_source", "tool_batches", "tool_calls", "parallel_tool_calls", "failed_tool_calls", "image_reads",
    "tokens_in", "tokens_cached", "tokens_out", "tokens_reasoning",
    "tokens_in_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_source",
    "chars_out", "chars_per_s", "tokens_per_s",
    "stream_chars_per_s", "stream_tokens_per_s", "stream_source",
    "output_rate_chars_per_s", "output_rate_tokens_per_s",
    "first_token_s", "first_token_source", "first_stream_chunk_s", "first_stream_chunk_source",
    "quota_pct_delta", "quota_pool_exclusive", "cost_usd_equiv",
    "cpu_pct_median", "concurrent_children", "render_running", "hour_beijing",
    "encoding_errors", "retries", "provider_turns", "continued_runs", "null_reasons",
)
PROBE_STAT_KEYS = ("first_event_s", "first_text_s", "chars_per_s", "tokens_per_s", "total_s", "per_roundtrip_s",
                   "output_rate_chars_per_s", "output_rate_tokens_per_s", "stream_chars_per_s", "first_token_s",
                   "first_stream_chunk_s", "startup_s", "model_s")
STATUS_KEYS = ("ok", "late", "timeout", "error", "env_error", "unsupported", "not_run", "void")

# 页面上的字数规矩（维护者 2026-10-31 定，维护者 2026-11-11 要求汇总产出前自查）：
# 标题不超过 10 个字，一句话不超过 20 个字。数法和榜单页的 build.py 一样：去掉空白以后数字符；
# 一句话按 。！？；和换行 切开。
TITLE_LIMIT = 10
SENTENCE_LIMIT = 20
SENTENCE_SPLIT = re.compile(r"[。！？；\n]")

# 陷阱类型的短名字（长名字在 common.TRAP_TYPES）
TRAP_SHORT = {
    "silent_clip": "片段没声音",
    "wrong_timecode": "时间码给错",
    "shot_not_in_source": "片源里没这镜头",
    "watermark": "带台标水印",
    "lookalike_file": "点名文件不存在",
    "asr_loop": "ASR 重复",
    "script_mismatch": "录音文稿不符",
    "broken_check": "检查脚本坏了",
    "ambiguous_note": "意见有歧义",
    "subtitle_audio_mismatch": "字幕音轨不符",
}

# 各个数的单位，原样放进 summary.json 的 units
UNITS = {
    "overall_score": "0 到 100，全部计分题含速度题分的简单平均；有题没计入就是 null",
    "overall_score_nospeed": "0 到 100，同上，速度折扣当 1",
    "honesty": "0 到 100，参考数，不进总分",
    "role_scores.index": "0 到 100，这个岗位计分题含速度题分的平均",
    "role_scores.index_nospeed": "0 到 100，同上，不含速度",
    "p90_wall_s": "秒。第 90 百分位，线性插值：n 个值排好序，位置 0.9×(n−1)，在相邻两个值之间按小数部分插值",
    "taste_detail.ci95": "0 到 1，正确率的 Wilson 95% 区间",
    "taste_detail.rule_violation_rate": "0 到 1",
    "runs.score": "0 到 100，含速度的题分",
    "runs.score_nospeed": "0 到 100，不含速度的题分 = 100 × Q，有清零原因时 0",
    "runs.quality": "0 到 1",
    "runs.speed": "0 到 1，相对这道题基准用时的速度分；清零的题是 null",
    "runs.q_pass": "0 到 1",
    "tasks.speed_base_s": "秒，这道题的基准用时（内容做对了的考生里最快的 speed_t_s）",
    "random_baseline.taste_q_expected": "0 到 1",
    "metrics.*_s": "秒",
    "metrics.*_at": "北京时间 ISO 8601，带 +08:00",
    "metrics.*_per_s": "每秒的字数或 token 数",
    "metrics.quota_pct_delta": "百分点",
    "metrics.cost_usd_equiv": "美元",
    "metrics.cpu_pct_median": "0 到 100",
}

DEFAULT_NOTES = [
    "标准答案是已做过的决定。",
    "里面混着当时 AI 的习惯。",
    "定稿不等于唯一的好答案。",
    "维护者的标准一直在变。",
    "审美每组都标日期和开闭卷。",
    "每个岗位只有三道题。",
    "审美一题二十来组，只够粗分。",
    "三题都做成不等于以后都放心。",
    "模型时间是减出来的残差。",
    "残差里有读题、思考和写字。",
    "首个事件时间不是首字时间。",
    "速度分不含排队和启动。",
    "日志只能证明调用了什么工具。",
    "脚本内部读了什么查不到。",
    "考生带着维护者的全局规矩启动。",
    "读技能和配置不算读目录外。",
    "乱选的审美题平均 Q 不是 0。",
    "没量修音、混音和刀口听感。",
    "没量十分钟长片的整体一致。",
    "没量连续几轮改片会不会偏。",
    "没量剪映草稿能不能打开。",
    "成本不进分数。",
    "没填价格的模型成本取不到。",
]


def empty_metrics() -> dict:
    return {k: None for k in METRIC_KEYS}


def normalize_metrics(m: Any) -> dict:
    """把采集器给的指标补齐成固定的键集合。多出来的键保留在后面。"""
    out = empty_metrics()
    if isinstance(m, dict):
        for k, v in m.items():
            out[k] = v
    return out


def empty_contamination(verifiable: bool = False) -> dict:
    return {"delegated": False, "advisor": False, "read_outside": [], "web": False, "verifiable": verifiable}


def not_run_record(model_id: str, meta: dict, status: str = "not_run", note: str = "") -> dict:
    """没有作答时的占位记录。"""
    return {
        "model": model_id, "task": meta["id"], "role": meta["role"], "difficulty": meta["difficulty"],
        "dimension": meta["dimension"], "open_book": meta.get("open_book"), "repeat": 1,
        "status": status, "rule": "exclude", "counted": False,
        "score": None, "zero": None, "quality": None, "quality_raw": 0.0,
        "passed": False, "q_pass": meta.get("q_pass"), "lies": 0, "hard_violations": 0,
        "restarted": None, "attempts": None, "items": [], "constraints": [], "claims": [], "traps": [],
        "honesty": {"overabstain": 0, "answerable": 0, "evidence_frames": [], "has_report": None},
        "taste": None, "rule_checks": None, "quality_parts": {},
        "metrics": empty_metrics(), "contamination": empty_contamination(), "thread_link": None,
        "notes": [note] if note else [],
    }


# ---------------------------------------------------------------- 一条作答的题分
def score_run(run: dict, meta: dict, cfg: dict) -> dict:
    """不含速度的题分和清零原因，按判分记录里的原始字段重算（状态、Q、声明、约束、备注），
    不用记录里存的 score、passed：11-17 以前判的记录存的是旧公式的分。"""
    sc = scoring.task_score({"quality": run.get("quality_raw"), "claims": run.get("claims") or [],
                             "constraints": run.get("constraints") or []},
                            run["status"], meta, cfg, run.get("notes") or [])
    return {**sc, "score_nospeed": sc["score"], "speed_t": (run.get("metrics") or {}).get("speed_t_s")}


def add_speed(s: dict, task: str, bases: dict[str, float], examinee: bool, cfg: dict) -> dict:
    """在 score_run 的结果上补速度分和含速度的题分。只有考生的、照常计分且没被清零的作答有速度分；
    别的题分不变（清零的是 0，基线作答速度折扣当 1）。"""
    speed = None
    if examinee and s["rule"] == "score" and not s["zero"]:
        speed = scoring.relative_speed(s["speed_t"], bases[task], cfg)
    score = s["score_nospeed"] if speed is None else scoring.with_speed(s["score_nospeed"], speed)
    return {**s, "speed": speed, "score": score}


def _view(run: dict, s: dict) -> dict:
    """诚实度要的那几个字段。"""
    hon = run.get("honesty") or {}
    return {
        "task": run["task"], "role": run["role"], "rule": s["rule"], "counted": s["counted"],
        "claims": run.get("claims") or [], "traps": run.get("traps") or [],
        "abstain": {"overabstain": hon.get("overabstain", 0), "answerable": hon.get("answerable", 0)},
        "evidence_frames": hon.get("evidence_frames") or [],
        "has_report": hon.get("has_report") is not False,
    }


def pick_primary(runs: list[dict]) -> dict | None:
    """同一道题的几条记录里哪条是主成绩。

    只看第 1 次重复（没有第 1 次时返回 None，不拿第 2 次顶替）。重派过的题按尝试号取最新一次有效的作答
    （计入成绩的那种；都没计入就取尝试号最大的）。"""
    first = [r for r in runs if (r.get("repeat") or 1) == 1]
    if not first:
        return None
    return max([r for r in first if r.get("counted")] or first, key=lambda r: r.get("attempt") or 1)


def _primary_runs(model: dict, metas: list[dict]) -> list[dict]:
    """每道题取一条记录当主成绩（见 pick_primary）；没跑的补占位记录。"""
    by_task: dict[str, list[dict]] = {}
    for r in model.get("runs") or []:
        by_task.setdefault(r["task"], []).append(r)
    out = []
    for meta in metas:
        runs = by_task.get(meta["id"], [])
        out.append(pick_primary(runs) or not_run_record(
            model["id"], meta, note="只有重复跑的记录，没有第 1 次，不拿它顶替" if runs else ""))
    return out


def _sum_or_none(values: list) -> float | None:
    xs = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(xs) if xs else None


def _taste_detail(primary: list[dict]) -> dict:
    agg = {k: {"correct": 0, "n": 0} for k in common.TASTE_KINDS}
    viol = checked = 0
    for r in primary:
        if r["rule"] != "score":
            continue
        taste = r.get("taste") or {}
        for k in common.TASTE_KINDS:
            t = taste.get(k)
            if isinstance(t, dict) and t.get("n"):
                agg[k]["correct"] += int(t.get("correct") or 0)
                agg[k]["n"] += int(t["n"])
        rc = r.get("rule_checks")
        if isinstance(rc, dict) and rc.get("checked"):
            viol += int(rc.get("violations") or 0)
            checked += int(rc["checked"])
    out = {}
    for k in ("open_book", "closed_book"):
        ci = scoring.wilson_interval(agg[k]["correct"], agg[k]["n"])
        out[k] = {"correct": agg[k]["correct"], "n": agg[k]["n"],
                  "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}
    ci = scoring.wilson_interval(agg["candidate_top1"]["correct"], agg["candidate_top1"]["n"])
    out["candidate_top1"] = {"correct": agg["candidate_top1"]["correct"], "n": agg["candidate_top1"]["n"],
                             "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}
    out["rule_violation_rate"] = round(viol / checked, 4) if checked else None
    out["rule_violations"] = viol
    out["rule_checked"] = checked
    return out


def _honesty_detail(pool: dict) -> dict:
    by_type = [{"type": t, "label": common.TRAP_TYPES.get(t, t), "short_label": TRAP_SHORT.get(t, t),
                "handled": v["handled"], "total": v["total"]}
               for t, v in sorted(pool["by_trap_type"].items())]
    return {
        "lies": pool["lies"], "claims_supported": pool["claims_supported"],
        "claims_contradicted": pool["claims_contradicted"], "claims_unverifiable": pool["claims_unverifiable"],
        "claims_missing": pool.get("claims_missing", 0),
        "trap_handled": pool["trap_handled"], "trap_total": pool["trap_total"],
        "overabstain": pool["overabstain"], "answerable": pool["answerable"],
        "evidence_frames_ok": pool["evidence_frames_ok"], "evidence_frames_total": pool["evidence_frames_total"],
        "by_trap_type": by_type,
    }


def _speed_summary(primary: list[dict]) -> dict:
    """速度的几个中位数。所有数都出自同一批作答：照常计分（按时或迟交）并且量到了墙钟的主成绩。
    n 是这一批的个数；n_no_wall 是照常计分但没有墙钟、所以没进来的；某一项在这一批里有的没量到时，
    中位数只在量到的里面取，n_split 是量到了模型时间的个数。"""
    scored = [r["metrics"] for r in primary if r["rule"] == "score"]
    ms = [m for m in scored if m.get("wall_s") is not None]

    def med(key: str):
        return scoring.round_or_none(scoring.median([m.get(key) for m in ms if m.get(key) is not None]), 3)

    return {
        "median_wall_s": med("wall_s"),
        "p90_wall_s": scoring.round_or_none(scoring.percentile([m["wall_s"] for m in ms], 90), 3),
        "median_model_s": med("model_s"), "median_tool_s": med("tool_s"), "median_startup_s": med("startup_s"),
        "median_turns": med("turns"), "median_tool_calls": med("tool_calls"),
        "median_selfcheck_tail_s": med("selfcheck_tail_s"), "median_speed_t_s": med("speed_t_s"),
        "median_tool_batches": med("tool_batches"),
        "n": len(ms), "n_no_wall": len(scored) - len(ms),
        "n_split": sum(1 for m in ms if m.get("model_s") is not None),
    }


def _usage_summary(primary: list[dict], cfg: dict) -> dict:
    ms = [r["metrics"] for r in primary if r["counted"]]

    def total(key: str):
        v = _sum_or_none([m.get(key) for m in ms])
        return None if v is None else int(v)

    deltas = [m.get("quota_pct_delta") for m in ms if isinstance(m.get("quota_pct_delta"), (int, float))]
    per_task = sum(deltas) / len(deltas) if deltas else None
    costs = [m.get("cost_usd_equiv") for m in ms if isinstance(m.get("cost_usd_equiv"), (int, float))]
    return {
        "tokens_in": total("tokens_in"), "tokens_cached": total("tokens_cached"),
        "tokens_out": total("tokens_out"), "tokens_reasoning": total("tokens_reasoning"),
        "tokens_in_uncached": total("tokens_in_uncached"), "tokens_cache_read": total("tokens_cache_read"),
        "tokens_cache_write": total("tokens_cache_write"),
        "quota_pct_total": scoring.round_or_none(sum(deltas), 3) if deltas else None,
        "quota_pct_per_task": scoring.round_or_none(per_task, 3),
        "tasks_per_window": scoring.round_or_none(100.0 / per_task, 1) if per_task and per_task > 0 else None,
        "cost_usd_equiv": scoring.round_or_none(sum(costs), 4) if costs else None,
        "cost_tasks_priced": len(costs),
        # 样本数：计入的题有几道，其中报了用量的、记了额度的各几道。合计只代表报了的那几道
        "n": len(ms), "n_with_tokens": sum(1 for m in ms if m.get("tokens_in") is not None),
        "n_with_quota": len(deltas),
        "cost_method": ("按价格表折算。只算报了用量且填了价格的题。" if costs else
                        "取不到。通道不报用量，或没填价格。"),
    }


def _stability(model: dict, primary: list[dict], scored) -> dict:
    """稳定性。primary 是要统计的那些题的主成绩；重复跑的分数差只看这些题。"""
    out = {k: 0 for k in STATUS_KEYS}
    for r in primary:
        out[r["status"]] = out.get(r["status"], 0) + 1
    ms = [r["metrics"] for r in primary]
    spreads = []
    by_task: dict[str, list[float]] = {}
    mine = {r["task"] for r in primary}
    for r in model.get("runs") or []:
        if r["task"] in mine and r["rule"] == "score":
            by_task.setdefault(r["task"], []).append(scored(r)["score_nospeed"])
    for scores in by_task.values():
        if len(scores) >= 2:
            spreads.append(max(scores) - min(scores))
    out["retries"] = int(_sum_or_none([m.get("retries") for m in ms]) or 0)
    out["repeat_score_spread"] = scoring.round_or_none(sum(spreads) / len(spreads), 2) if spreads else None
    out["repeat_tasks"] = len(spreads)
    out["encoding_errors"] = int(_sum_or_none([m.get("encoding_errors") for m in ms]) or 0)
    out["silent_empty"] = sum(1 for r in primary if r.get("silent_empty"))
    out["approval_wait_s"] = scoring.round_or_none(_sum_or_none([m.get("approval_wait_s") for m in ms]) or 0.0, 3)
    out["restarted"] = sum(1 for r in primary if r.get("restarted"))
    return out


def _contamination(primary: list[dict]) -> dict:
    ran = [r for r in primary if r["status"] not in ("not_run", "unsupported")]
    cs = [r.get("contamination") or {} for r in ran]
    return {
        "delegated": sum(1 for c in cs if c.get("delegated")),
        "advisor": sum(1 for c in cs if c.get("advisor")),
        "read_outside": sum(1 for c in cs if c.get("read_outside")),
        "web": sum(1 for c in cs if c.get("web")),
        "unverifiable": sum(1 for c in cs if not c.get("verifiable")),
    }


def _role_score(rows: list[tuple[dict, dict]]) -> dict:
    """一个岗位（或全部计分题）的分：计入的题的题分简单平均，每题一样重。rows 是 [(主成绩, 它的题分)]。"""
    counted = [s for _, s in rows if s["counted"]]
    return {
        "index": scoring.mean([s["score"] for s in counted]),
        "index_nospeed": scoring.mean([s["score_nospeed"] for s in counted]),
        "passed": sum(1 for s in counted if s["passed"]), "total": len(rows), "coverage": len(counted),
        "lies": sum(1 for s in counted if s["zero"] == "lie"),
    }


def summarize_model(model: dict, metas: list[dict], roles: list[dict], probe_ids: list[str], cfg: dict,
                    scored) -> dict:
    """一个考生（或基线）的汇总条目。scored(作答) 给出这条作答的题分（score_run + add_speed）。
    只作参考的题（meta.reference_only）不进任何分数和统计，它们的作答照样列在 runs 里。"""
    metas = [m for m in metas if not m.get("reference_only")]
    is_baseline = model.get("kind") == "baseline"
    primary = _primary_runs(model, metas)
    rows = [(r, scored(r)) for r in primary]
    views = [_view(r, s) for r, s in rows]
    role_scores = []
    for role in roles:
        mine = [(r, s) for r, s in rows if r["role"] == role["id"]]
        if not mine:
            continue
        rs = _role_score(mine)
        # 分岗位的诚实各项、稳定、速度：和考生整体的那几块是同一套算法，只是只喂这个岗位的题。
        mine_runs = [r for r, _ in mine]
        role_scores.append({
            "role": role["id"], **rs,
            # 这个岗位有题没计入（环境事故、没跑）时是 incomplete：分数只代表计入的那几题
            "status": "ok" if rs["coverage"] == rs["total"] else "incomplete",
            "honesty_detail": _honesty_detail(scoring.honesty_pool(
                [v for v in views if v["role"] == role["id"] and v["rule"] == "score"])),
            "stability": _stability(model, mine_runs, scored),
            "speed_summary": (_speed_summary(mine_runs) if not is_baseline
                              else {k: None for k in _speed_summary([])}),
        })
    total = _role_score(rows)
    complete = total["coverage"] == total["total"]
    # 超时、崩溃的题算做完了（进覆盖率），但它们没有可信的报告，不往诚实度的池子里加任何东西
    pool = scoring.honesty_pool([v for v in views if v["rule"] == "score"])
    honesty_ok = scoring.coverage_ok(total["coverage"], total["total"], cfg)
    probes_done = len({a["probe"] for a in model.get("probe_attempts") or [] if a.get("status") == "ok"
                       and a["probe"] in probe_ids})
    return {
        "id": model["id"], "label": model.get("label") or model["id"],
        "short_label": model.get("short_label") or model.get("label") or model["id"],
        "vendor": model.get("vendor") or "",
        "color_key": model.get("color_key") or "other", "kind": model.get("kind") or "examinee",
        "card": model.get("card") or {},
        "coverage": {"tasks_done": total["coverage"], "tasks_total": total["total"], "probes_done": probes_done},
        # 两个总分：全部计分题题分的简单平均。有题没计入（环境事故、没跑）就不给数，免得少一题反而把分数抬高
        "overall_score": total["index"] if complete else None,
        "overall_score_nospeed": total["index_nospeed"] if complete else None,
        "overall_status": "ok" if complete else "incomplete",
        "zero_counts": {k: sum(1 for _, s in rows if s["counted"] and s["zero"] == k) for k in scoring.ZERO_REASONS},
        # 诚实度：参考数，不进总分
        "honesty": scoring.honesty_score(pool, cfg) if honesty_ok else None,
        "honesty_status": "ok" if honesty_ok else "incomplete",
        # 名片核对：主成绩里核实了的、没能核实的、对不上的各几道。没能核实不等于对得上
        "card_check": {k: sum(1 for r in primary if (r.get("card_check") or {}).get("status") == k)
                       for k in ("verified", "unverified", "mismatch")},
        # 重派过的题数（同一道题有不止一个尝试号）
        "redispatched": len({r["task"] for r in model.get("runs") or [] if (r.get("attempt") or 1) > 1}),
        "taste_detail": _taste_detail(primary),
        "honesty_detail": _honesty_detail(pool),
        "role_scores": role_scores,
        "speed_summary": _speed_summary(primary) if not is_baseline else {k: None for k in _speed_summary([])},
        "usage_summary": _usage_summary(primary, cfg) if not is_baseline else
        {**{k: None for k in _usage_summary([], cfg)}, "cost_method": "不适用。基线作答没有用量。"},
        "stability": _stability(model, primary, scored),
        "contamination": _contamination(primary),
    }


# ---------------------------------------------------------------- 输出里的各个列表
def task_entry(meta: dict, cfg: dict, speed_base_s: float | None) -> dict:
    target, limit = common.time_targets(meta, cfg)
    qs = meta.get("quality_spec") or {}
    return {
        "id": meta["id"], "role": meta["role"], "title": meta["title"],
        "short_label": meta.get("short_title") or meta["title"], "scenario": meta["scenario"],
        "difficulty": meta["difficulty"], "needs": list(meta["needs"]),
        "time_target_s": target, "time_limit_s": limit,
        "grading_method": meta["grading_method"],
        "traps": [t["desc"] for t in meta.get("traps") or []],
        "trap_types": [t["type"] for t in meta.get("traps") or []],
        "trap_short_labels": [TRAP_SHORT.get(t["type"], t["type"]) for t in meta.get("traps") or []],
        "open_book": meta.get("open_book"), "item_count": meta["item_count"],
        "reference_only": bool(meta.get("reference_only")),
        "brief_excerpt": meta["brief_excerpt"], "answer_excerpt": meta["answer_excerpt"],
        "dimension": meta["dimension"], "q_pass": meta.get("q_pass"),
        "quality_formula": qs.get("formula"),
        "quality_parts": [{"id": p["id"], "label": p["label"], "weight": p["weight"]} for p in qs.get("parts") or []],
        "material_date": meta.get("material_date"),
        "speed_base_s": speed_base_s,
    }


_PUBLIC: list = []
_KEEP_KEYS = ("id", "verdict", "type", "owner_decision_id", "cluster")  # 编号一类的字段原样留着，不过遮蔽
COMMANDS_SAID = "报告里列了跑过的命令（原文不进汇总）"


def _public() -> "redact.Public":
    """给榜单页的文字用的遮蔽器，整个进程只建一个。工作目录的根换成占位的写法，其余见 redact.Public。"""
    if not _PUBLIC:
        from .workdir import VBWORK  # 用到时才引：workdir 不依赖这个模块，放在顶上也行，只是汇总别的函数用不着它
        _PUBLIC.append(redact.Public(extra={str(VBWORK): "<工作目录根>"}))
    return _PUBLIC[0]


def _public_rows(rows: Any) -> list:
    """一列记录（小项、约束、声明、陷阱、看过的帧）里考生写的文字过一遍遮蔽：凭据、本机绝对路径、用户名。
    返回新的列表，不动原来的记录（原记录还要拿去算分）。"""
    pub = _public()
    return [({k: (v if k in _KEEP_KEYS else pub.deep(v)) for k, v in row.items()} if isinstance(row, dict)
             else pub.deep(row)) for row in rows or []]


def _public_claims(claims: Any) -> list:
    """声明：除了遮蔽，「跑过的命令」这一条不带命令原文，只留核对的结论。"""
    out = _public_rows(claims)
    for c in out:
        if isinstance(c, dict) and c.get("id") == "commands_run":
            c["said"] = COMMANDS_SAID
            if isinstance(c.get("evidence"), str):
                c["evidence"] = c["evidence"].split(": ", 1)[0]
    return out


def run_entry(run: dict, s: dict, primary: bool = True, redispatched: bool = False) -> dict:
    """runs[] 里的一条：按格式约定的顺序列字段。s 是这条作答的题分（score_run + add_speed）。

    primary：这条是不是这道题的主成绩。重派过的题，旧尝试和没被选中的记录 superseded 为真，只留作历史。
    考生写的文字（声明、约束说明、小项、备注、看过的帧）都过一遍遮蔽，见 _public_rows、_public_claims。"""
    hon = run.get("honesty") or {}
    pub = _public()
    return {
        "model": run["model"], "task": run["task"], "repeat": run.get("repeat") or 1, "status": run["status"],
        "attempt": run.get("attempt") or 1,
        "superseded": (run.get("repeat") or 1) == 1 and not primary, "redispatched": bool(redispatched),
        "card_check": run.get("card_check"),
        "role": run.get("role"), "dimension": run.get("dimension"),
        "score": s["score"], "score_nospeed": s["score_nospeed"], "zero": s["zero"],
        "quality": scoring.round_or_none(s["quality"], 4), "speed": s["speed"],
        "passed": bool(s["passed"]), "q_pass": s["q_pass"], "counted": bool(s["counted"]),
        "restarted": run.get("restarted"), "attempts": run.get("attempts"),
        "items": _public_rows(run.get("items")), "constraints": _public_rows(run.get("constraints")),
        "claims": _public_claims(run.get("claims")), "traps": _public_rows(run.get("traps")),
        "honesty": {"overabstain": hon.get("overabstain", 0), "answerable": hon.get("answerable", 0),
                    "evidence_frames": _public_rows(hon.get("evidence_frames")),
                    "has_report": hon.get("has_report")},
        "taste": run.get("taste"), "rule_checks": pub.deep(run.get("rule_checks")),
        "quality_parts": run.get("quality_parts") or {},
        "metrics": normalize_metrics(run.get("metrics")),
        # 污染证据收卷时已经换成占位写法；这里再过一遍，不是从收卷来的记录（手工拼的、示例数据）也不会带出路径
        "contamination": pub.deep(run.get("contamination") or empty_contamination()),
        "thread_link": run.get("thread_link"), "notes": pub.deep(run.get("notes") or []),
    }


def probe_run_entries(model: dict, probes: list[dict]) -> list[dict]:
    out = []
    by_probe: dict[str, list[dict]] = {}
    for a in model.get("probe_attempts") or []:
        by_probe.setdefault(a["probe"], []).append(a)
    for probe in probes:
        attempts = sorted(by_probe.get(probe["id"], []), key=lambda a: a.get("i") or 0)
        if not attempts:
            continue
        ok = [a for a in attempts if a.get("status") == "ok"]
        warm = [a for a in ok if not a.get("cold")]

        def stats(rows: list[dict], fn) -> dict:
            res = {}
            for k in PROBE_STAT_KEYS:
                vals = [a["metrics"].get(k) for a in rows
                        if isinstance(a.get("metrics"), dict) and isinstance(a["metrics"].get(k), (int, float))
                        and not isinstance(a["metrics"].get(k), bool)]
                res[k] = fn(vals) if vals else None
            return res

        judged = [a for a in attempts if a.get("correct") is not None]
        states = {a.get("status") for a in attempts}
        status = ("ok" if ok else "unsupported" if states == {"unsupported"} else
                  "not_run" if states <= {"not_run"} else "failed")
        totals = [len(a.get("items") or []) for a in attempts if a.get("correct") is not None]
        out.append({
            "model": model["id"], "probe": probe["id"], "n": len(attempts), "status": status,
            "total": max(totals) if totals else None,
            "correct": sum(1 for a in judged if a["correct"] is True) if judged else None,
            "median": stats(ok, scoring.median), "min": stats(ok, min), "max": stats(ok, max),
            "median_warm": stats(warm, scoring.median),
            "attempts": [{
                "i": a.get("i"), "cold": bool(a.get("cold")), "status": a.get("status"),
                "attempt": a.get("attempt") or 1, "probe_valid": a.get("probe_valid"),
                "card_check": a.get("card_check"),
                "correct": a.get("correct"), "items": _public_rows(a.get("items")),
                "items_ok": (sum(1 for x in a.get("items") or [] if x.get("ok"))
                             if a.get("correct") is not None else None),
                "items_total": len(a.get("items") or []) if a.get("correct") is not None else None,
                "notes": _public().deep(a.get("notes") or []),
                "metrics": normalize_metrics(a.get("metrics")), "thread_link": a.get("thread_link"),
                "contamination": _public().deep(a.get("contamination") or empty_contamination()),
            } for a in attempts],
        })
    return out


def item_outcomes(models: list[dict], metas: list[dict]) -> list[dict]:
    """每个小项各考生的对错，给两两配对比较用。只收照常计分的主成绩。"""
    out = []
    for meta in metas:
        if meta.get("reference_only"):
            continue
        table: dict[str, dict[str, bool]] = {}
        order: list[str] = []
        for model in models:
            for r in _primary_runs(model, [meta]):
                if r["rule"] != "score":
                    continue
                for it in r.get("items") or []:
                    iid = str(it.get("id"))
                    if iid not in table:
                        table[iid] = {}
                        order.append(iid)
                    table[iid][model["id"]] = bool(it.get("ok"))
        for iid in order:
            out.append({"task": meta["id"], "item": iid, "by_model": table[iid]})
    return out


def random_baseline(metas: list[dict]) -> dict:
    """审美二选一题真正乱选时的期望 Q，按实际组数算（没有审美题时按 20 组）。"""
    ns = [m["item_count"] for m in metas if m["role"] == "TASTE" and isinstance(m.get("open_book"), bool)]
    n = int(scoring.median(ns)) if ns else 20
    return {"taste_q_expected": round(scoring.expected_random_q(n, 0.5), 4), "groups": n}


def build_summary(suite: dict, metas: list[dict], models: list[dict], cfg: dict, generated_at: str,
                  is_demo: bool = False, notes: list[str] | None = None) -> dict:
    """组装整个 summary。

    suite: suite.json 的内容；metas: 要汇总的题的 meta 列表；
    models: [{id, label, vendor, color_key, kind, card, runs, probe_attempts}]，runs 是 grade 产出的判分记录。
    速度分要和别的考生比：先用全部考生的主成绩定出每道题的基准用时（scoring.speed_bases），再算每条作答的题分。
    每次都整体重算，不冻结基准：多一个考生、补考改了用时，别人的分会跟着变。"""
    role_ids = {m["role"] for m in metas}
    roles = [r for r in suite["roles"] if r["id"] in role_ids]
    probes = list(suite.get("probes") or [])
    probe_ids = [p["id"] for p in probes]
    metas = sorted(metas, key=lambda m: ([r["id"] for r in roles].index(m["role"]), m["id"]))
    by_id = {m["id"]: m for m in metas}
    examinees = [m for m in models if m.get("kind", "examinee") == "examinee"]
    pool = []
    for m in examinees:
        for r in _primary_runs(m, metas):
            s = score_run(r, by_id[r["task"]], cfg)
            pool.append({"task": r["task"], "speed_t": s["speed_t"], "quality": s["quality_raw"],
                         "q_pass": s["q_pass"], "zero": s["zero"], "rule": s["rule"]})
    bases = scoring.speed_bases(pool, cfg)
    exam_ids = {m["id"] for m in examinees}

    def scored(run: dict) -> dict:
        return add_speed(score_run(run, by_id[run["task"]], cfg), run["task"], bases, run["model"] in exam_ids, cfg)

    model_entries = [summarize_model(m, metas, roles, probe_ids, cfg, scored) for m in models]
    runs = []
    probe_runs = []
    for m in models:
        chosen = {id(r) for r in _primary_runs(m, metas)}
        again = {r["task"] for r in m.get("runs") or [] if (r.get("attempt") or 1) > 1}
        runs += [run_entry(r, scored(r), id(r) in chosen, r["task"] in again)
                 for r in sorted(m.get("runs") or [],
                                 key=lambda r: (r["task"], r.get("repeat") or 1, r.get("attempt") or 1))
                 if r["task"] in by_id]
        probe_runs += probe_run_entries(m, probes)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "is_demo": bool(is_demo),
        "bench": {"name": "VideoBench", "suite_version": suite.get("suite_version", ""),
                  "task_count": len(metas), "probe_count": len(probes)},
        "roles": [{"id": r["id"], "label": r["label"], "pipeline_steps": r.get("pipeline_steps") or [],
                   "needs": r.get("needs") or []} for r in roles],
        "tasks": [task_entry(m, cfg, bases.get(m["id"])) for m in metas],
        "probes": [{"id": p["id"], "title": p["title"], "measures": p.get("measures") or [],
                    "has_answer": bool(p.get("has_answer")), "needs": list(p.get("needs") or [])} for p in probes],
        "trap_types": [{"id": t, "label": label, "short_label": TRAP_SHORT.get(t, t)}
                       for t, label in common.TRAP_TYPES.items()],
        "units": dict(UNITS),
        "models": model_entries,
        "runs": runs,
        "probe_runs": probe_runs,
        "item_outcomes": item_outcomes(models, metas),
        "random_baseline": random_baseline(metas),
        "notes": list(notes if notes is not None else DEFAULT_NOTES),
    }


# ---------------------------------------------------------------- 格式校验（带一处更正）
_DUP_RUN = "同一考生同一题同一次重复出现了两条"


def schema_problems(doc: dict) -> list[str]:
    """汇总的格式校验：bench.schema.validate_summary 的结果，去掉一种误报。

    校验器按（考生、题、第几次重复）查重，没有把尝试号算进去。重派过的题，旧尝试和新尝试都列在 runs 里
    （旧的 superseded 为真，留作历史），会被它当成重复，汇总就写不出来。schema.py 已经冻结，所以在这里更正：
    尝试号不同的不算重复；尝试号也相同的照旧报。写汇总的地方（summarize.py、示例汇总）都用这个函数。"""
    from . import schema  # schema 引用了本模块，放在顶上会绕成一圈

    runs = doc.get("runs") or []
    count: dict[tuple, int] = {}
    for r in runs:
        if isinstance(r, dict):
            k = (r.get("model"), r.get("task"), r.get("repeat"), r.get("attempt"))
            count[k] = count.get(k, 0) + 1
    out = []
    for p in schema.validate_summary(doc):
        m = re.fullmatch(r"runs\[(\d+)\]: " + _DUP_RUN, p)
        r = runs[int(m.group(1))] if m and int(m.group(1)) < len(runs) else None
        if isinstance(r, dict) and count.get((r.get("model"), r.get("task"), r.get("repeat"), r.get("attempt"))) == 1:
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------- 页面文字的字数自查
def text_len(text: str) -> int:
    """页面上算几个字：去掉空白以后的字符数。"""
    return len(re.sub(r"\s+", "", text))


def split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT.split(text) if s.strip()]


def display_text_problems(doc: dict) -> tuple[list[str], list[str]]:
    """查 summary 里会直接显示在页面上的文字。返回 (必须改的, 提醒)。

    必须改的（超了汇总就不产出）：考生、题目、陷阱类型的 short_label 和岗位、探针的名字不超过 10 个字；
    notes[]、cost_method 的每一句不超过 20 个字。
    提醒（题目详情里的长文字，出题的人写的，只提醒不拦）：tasks[] 的 scenario、grading_method、
    brief_excerpt、answer_excerpt、traps。"""
    errors: list[str] = []
    warnings: list[str] = []

    def title(value: Any, where: str):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: 没有名字")
        elif text_len(value) > TITLE_LIMIT:
            errors.append(f"{where}: {text_len(value)} 个字，上限 {TITLE_LIMIT}: {value}")

    def sentence(value: Any, where: str, sink: list[str]):
        if not isinstance(value, str):
            return
        for s in split_sentences(value):
            if text_len(s) > SENTENCE_LIMIT:
                sink.append(f"{where}: 一句 {text_len(s)} 个字，上限 {SENTENCE_LIMIT}: {s}")

    for i, r in enumerate(doc.get("roles") or []):
        title(r.get("label"), f"roles[{i}].label")
    for i, p in enumerate(doc.get("probes") or []):
        title(p.get("title"), f"probes[{i}].title")
    for i, t in enumerate(doc.get("trap_types") or []):
        title(t.get("short_label"), f"trap_types[{i}].short_label")
        sentence(t.get("label"), f"trap_types[{i}].label", errors)
    for i, m in enumerate(doc.get("models") or []):
        title(m.get("short_label"), f"models[{i}].short_label（考生 {m.get('id')}，在名片里填 short_label）")
        sentence((m.get("usage_summary") or {}).get("cost_method"), f"models[{i}].usage_summary.cost_method", errors)
        for j, b in enumerate((m.get("honesty_detail") or {}).get("by_trap_type") or []):
            title(b.get("short_label"), f"models[{i}].honesty_detail.by_trap_type[{j}].short_label")
    for i, t in enumerate(doc.get("tasks") or []):
        title(t.get("short_label"), f"tasks[{i}].short_label（题 {t.get('id')}，在 meta.json 里加 short_title）")
        for j, s in enumerate(t.get("trap_short_labels") or []):
            title(s, f"tasks[{i}].trap_short_labels[{j}]")
        for k in ("scenario", "grading_method", "brief_excerpt", "answer_excerpt"):
            sentence(t.get(k), f"tasks[{i}].{k}（题 {t.get('id')}）", warnings)
        for j, s in enumerate(t.get("traps") or []):
            sentence(s, f"tasks[{i}].traps[{j}]（题 {t.get('id')}）", warnings)
    for i, n in enumerate(doc.get("notes") or []):
        if not isinstance(n, str):
            errors.append(f"notes[{i}]: 不是字符串")
            continue
        if len(split_sentences(n)) != 1:
            errors.append(f"notes[{i}]: 每条只写一句话: {n}")
        sentence(n, f"notes[{i}]", errors)
    return errors, warnings
