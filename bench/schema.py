"""summary.json 的格式校验。真的汇总和示例汇总都要过同一个校验器。

只查榜单页会读的字段：键在不在、类型对不对、取值在不在范围里。
"""
from __future__ import annotations

import math
from typing import Any

from . import common, scoring, summary

NUM = (int, float)
RUN_STATUSES = scoring.STATUSES

TOP_KEYS = ("schema_version", "generated_at", "is_demo", "bench", "roles", "tasks", "probes",
            "models", "runs", "probe_runs", "item_outcomes", "random_baseline", "notes", "trap_types", "units")
TASK_KEYS = ("id", "role", "title", "scenario", "difficulty", "needs", "time_target_s", "time_limit_s",
             "grading_method", "traps", "open_book", "item_count", "brief_excerpt", "answer_excerpt",
             "short_label", "dimension", "q_pass", "trap_types", "trap_short_labels", "speed_base_s")
MODEL_KEYS = ("id", "label", "short_label", "vendor", "color_key", "kind", "card", "coverage",
              "overall_score", "overall_score_nospeed", "overall_status", "zero_counts", "honesty", "honesty_status",
              "taste_detail", "honesty_detail", "role_scores",
              "speed_summary", "usage_summary", "stability", "contamination", "card_check", "redispatched")
CARD_KEYS = ("channel", "model", "effort", "fast", "context_tokens", "image_input", "video_input",
             "max_parallel", "quota_pool", "quota_reset", "plan_price", "notes")
ROLE_SCORE_KEYS = ("role", "index", "index_nospeed", "passed", "total", "lies", "status",
                   "coverage", "honesty_detail", "stability", "speed_summary")
RUN_KEYS = ("model", "task", "repeat", "status", "score", "score_nospeed", "zero", "quality", "speed",
            "passed", "items", "constraints", "claims", "traps", "metrics", "contamination", "thread_link",
            "restarted", "attempts", "honesty", "q_pass", "dimension", "attempt", "superseded", "redispatched",
            "card_check")
HONESTY_KEYS = ("lies", "claims_supported", "claims_contradicted", "claims_unverifiable", "trap_handled",
                "trap_total", "overabstain", "answerable", "evidence_frames_ok", "evidence_frames_total",
                "by_trap_type", "claims_missing")
SPEED_SUMMARY_KEYS = ("median_wall_s", "p90_wall_s", "median_model_s", "median_tool_s", "median_startup_s",
                      "median_turns", "median_tool_calls", "median_selfcheck_tail_s", "median_tool_batches",
                      "n", "n_no_wall", "n_split")
USAGE_SUMMARY_KEYS = ("tokens_in", "tokens_cached", "tokens_out", "tokens_reasoning", "quota_pct_total",
                      "quota_pct_per_task", "tasks_per_window", "cost_usd_equiv", "cost_method", "n", "n_with_tokens",
                      "n_with_quota")
STABILITY_KEYS = ("ok", "late", "timeout", "error", "env_error", "unsupported", "not_run", "retries",
                  "repeat_score_spread", "encoding_errors", "silent_empty", "approval_wait_s", "void", "restarted")
TIME_KEYS = ("dispatched_at", "started_at", "first_event_at", "first_text_at", "first_deliverable_at",
             "delivered_at")


def _is_num(x: Any) -> bool:
    return common._is_number(x)


def _num_or_none(x: Any) -> bool:
    return x is None or _is_num(x)


class _Checker:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def add(self, where: str, msg: str) -> None:
        if len(self.problems) < 200:
            self.problems.append(f"{where}: {msg}")

    def keys(self, where: str, obj: Any, keys: tuple[str, ...]) -> bool:
        if not isinstance(obj, dict):
            self.add(where, "应为对象")
            return False
        missing = [k for k in keys if k not in obj]
        if missing:
            self.add(where, "缺字段 " + ", ".join(missing))
        return True

    def num(self, where: str, x: Any, lo: float | None = None, hi: float | None = None,
            nullable: bool = True) -> None:
        if x is None:
            if not nullable:
                self.add(where, "不能是 null")
            return
        if not _is_num(x):
            self.add(where, f"应为数字，实际是 {x!r}")
        elif (lo is not None and x < lo - 1e-9) or (hi is not None and x > hi + 1e-9):
            self.add(where, f"超出范围 [{lo}, {hi}]: {x}")

    def ci(self, where: str, x: Any) -> None:
        if x is None:
            return
        if not (isinstance(x, list) and len(x) == 2 and all(_is_num(v) for v in x) and x[0] <= x[1] + 1e-9):
            self.add(where, f"区间应为 [下限, 上限] 或 null: {x!r}")

    def ts(self, where: str, x: Any, nullable: bool = True) -> None:
        if x is None:
            if not nullable:
                self.add(where, "不能是 null")
            return
        dt = common.parse_ts(x) if isinstance(x, str) else None
        if dt is None or not isinstance(x, str) or not x.endswith("+08:00"):
            self.add(where, f"时刻应为带 +08:00 的 ISO 字符串: {x!r}")


def _check_no_nan(c: _Checker, where: str, obj: Any, depth: int = 0) -> None:
    if isinstance(obj, float) and not math.isfinite(obj):
        c.add(where, "有 NaN 或无穷大")
    elif isinstance(obj, dict) and depth < 12:
        for k, v in obj.items():
            _check_no_nan(c, f"{where}.{k}", v, depth + 1)
    elif isinstance(obj, list) and depth < 12:
        for i, v in enumerate(obj[:2000]):
            _check_no_nan(c, f"{where}[{i}]", v, depth + 1)


def validate_summary(doc: Any) -> list[str]:
    """校验一份 summary，返回问题列表。空列表表示通过。"""
    c = _Checker()
    if not c.keys("顶层", doc, TOP_KEYS):
        return c.problems
    _check_no_nan(c, "顶层", doc)
    if doc.get("schema_version") != summary.SCHEMA_VERSION:
        c.add("schema_version", f"应为 {summary.SCHEMA_VERSION}")
    c.ts("generated_at", doc.get("generated_at"), nullable=False)
    if not isinstance(doc.get("is_demo"), bool):
        c.add("is_demo", "应为布尔")
    c.keys("bench", doc.get("bench"), ("name", "suite_version", "task_count", "probe_count"))

    role_ids = set()
    for i, r in enumerate(doc.get("roles") or []):
        if c.keys(f"roles[{i}]", r, ("id", "label", "pipeline_steps", "needs")):
            role_ids.add(r.get("id"))
    task_ids = set()
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        c.add("tasks", "应为列表")
        tasks = []
    for i, t in enumerate(tasks):
        w = f"tasks[{i}]"
        if not c.keys(w, t, TASK_KEYS):
            continue
        task_ids.add(t.get("id"))
        if t.get("role") not in role_ids:
            c.add(w, f"role {t.get('role')!r} 不在 roles 里")
        if t.get("difficulty") not in common.DIFFICULTIES:
            c.add(w, "difficulty 取值不对")
        c.num(w + ".time_target_s", t.get("time_target_s"), 0, None, nullable=False)
        c.num(w + ".time_limit_s", t.get("time_limit_s"), 0, None, nullable=False)
        if not isinstance(t.get("traps"), list) or any(not isinstance(x, str) for x in t.get("traps") or []):
            c.add(w + ".traps", "应为字符串列表")
        if t.get("open_book") not in (None, True, False):
            c.add(w + ".open_book", "应为布尔或 null")
    bench = doc.get("bench") if isinstance(doc.get("bench"), dict) else {}
    if bench.get("task_count") != len(tasks):
        c.add("bench.task_count", "和 tasks 的长度不一致")

    probe_ids = set()
    for i, p in enumerate(doc.get("probes") or []):
        if c.keys(f"probes[{i}]", p, ("id", "title", "measures")):
            probe_ids.add(p.get("id"))
    if bench.get("probe_count") != len(doc.get("probes") or []):
        c.add("bench.probe_count", "和 probes 的长度不一致")

    model_ids = set()
    models = doc.get("models")
    if not isinstance(models, list):
        c.add("models", "应为列表")
        models = []
    for i, m in enumerate(models):
        _check_model(c, f"models[{i}]", m, role_ids)
        if isinstance(m, dict):
            if m.get("id") in model_ids:
                c.add(f"models[{i}]", "id 重复")
            model_ids.add(m.get("id"))

    seen_runs = set()
    for i, r in enumerate(doc.get("runs") or []):
        w = f"runs[{i}]"
        _check_run(c, w, r)
        if isinstance(r, dict):
            if r.get("model") not in model_ids:
                c.add(w, f"model {r.get('model')!r} 不在 models 里")
            if r.get("task") not in task_ids:
                c.add(w, f"task {r.get('task')!r} 不在 tasks 里")
            key = (r.get("model"), r.get("task"), r.get("repeat"))
            if key in seen_runs:
                c.add(w, "同一考生同一题同一次重复出现了两条")
            seen_runs.add(key)

    for i, p in enumerate(doc.get("probe_runs") or []):
        w = f"probe_runs[{i}]"
        if not c.keys(w, p, ("model", "probe", "n", "correct", "median", "min", "max", "median_warm", "attempts")):
            continue
        if p.get("model") not in model_ids:
            c.add(w, "model 不在 models 里")
        if p.get("probe") not in probe_ids:
            c.add(w, "probe 不在 probes 里")
        if p.get("correct") is not None and not (isinstance(p["correct"], int) and 0 <= p["correct"] <= (p.get("n") or 0)):
            c.add(w + ".correct", "应为 0 到 n 之间的整数或 null")
        for k in ("median", "min", "max", "median_warm"):
            if not isinstance(p.get(k), dict):
                c.add(f"{w}.{k}", "应为对象")
        for j, a in enumerate(p.get("attempts") or []):
            wa = f"{w}.attempts[{j}]"
            if c.keys(wa, a, ("i", "cold", "status", "correct", "items", "metrics", "thread_link")):
                _check_metrics(c, wa + ".metrics", a.get("metrics"))
                if a.get("status") not in RUN_STATUSES:
                    c.add(wa + ".status", "取值不对")

    for i, r in enumerate(doc.get("item_outcomes") or []):
        w = f"item_outcomes[{i}]"
        if c.keys(w, r, ("task", "item", "by_model")) and not isinstance(r.get("by_model"), dict):
            c.add(w + ".by_model", "应为对象")
    rb = doc.get("random_baseline")
    if c.keys("random_baseline", rb, ("taste_q_expected",)):
        c.num("random_baseline.taste_q_expected", rb.get("taste_q_expected"), 0, 1, nullable=False)
    if not isinstance(doc.get("notes"), list) or any(not isinstance(x, str) for x in doc.get("notes") or []):
        c.add("notes", "应为字符串列表")
    return c.problems


def _check_model(c: _Checker, w: str, m: Any, role_ids: set) -> None:
    if not c.keys(w, m, MODEL_KEYS):
        return
    if m.get("kind") not in ("examinee", "baseline"):
        c.add(w + ".kind", "应为 examinee 或 baseline")
    c.keys(w + ".card", m.get("card"), CARD_KEYS)
    c.keys(w + ".coverage", m.get("coverage"), ("tasks_done", "tasks_total", "probes_done"))
    for k in ("overall_score", "overall_score_nospeed", "honesty"):
        c.num(f"{w}.{k}", m.get(k), 0, 100)
    for k, v in (("overall_status", "overall_score"), ("honesty_status", "honesty")):
        if m.get(k) not in ("ok", "incomplete"):
            c.add(f"{w}.{k}", "应为 ok 或 incomplete")
        elif m.get(k) == "incomplete" and m.get(v) is not None:
            c.add(f"{w}.{v}", "未完成时应为 null")
    c.keys(w + ".zero_counts", m.get("zero_counts"), scoring.ZERO_REASONS)
    td = m.get("taste_detail")
    if c.keys(w + ".taste_detail", td, ("open_book", "closed_book", "candidate_top1", "rule_violation_rate")):
        for k in ("open_book", "closed_book", "candidate_top1"):
            if c.keys(f"{w}.taste_detail.{k}", td.get(k), ("correct", "n")):
                c.ci(f"{w}.taste_detail.{k}.ci95", td[k].get("ci95"))
        c.num(w + ".taste_detail.rule_violation_rate", td.get("rule_violation_rate"), 0, None)
    hd = m.get("honesty_detail")
    if c.keys(w + ".honesty_detail", hd, HONESTY_KEYS):
        for j, bt in enumerate(hd.get("by_trap_type") or []):
            c.keys(f"{w}.honesty_detail.by_trap_type[{j}]", bt, ("type", "label", "handled", "total"))
    for j, rs in enumerate(m.get("role_scores") or []):
        wr = f"{w}.role_scores[{j}]"
        if not c.keys(wr, rs, ROLE_SCORE_KEYS):
            continue
        if rs.get("role") not in role_ids:
            c.add(wr + ".role", "不在 roles 里")
        c.num(wr + ".index", rs.get("index"), 0, 100)
        c.num(wr + ".index_nospeed", rs.get("index_nospeed"), 0, 100)
        if rs.get("status") not in ("ok", "incomplete"):
            c.add(wr + ".status", "应为 ok 或 incomplete")
        c.keys(wr + ".honesty_detail", rs.get("honesty_detail"), HONESTY_KEYS)
        c.keys(wr + ".stability", rs.get("stability"), STABILITY_KEYS)
        c.keys(wr + ".speed_summary", rs.get("speed_summary"), SPEED_SUMMARY_KEYS)
    c.keys(w + ".speed_summary", m.get("speed_summary"), SPEED_SUMMARY_KEYS)
    c.keys(w + ".usage_summary", m.get("usage_summary"), USAGE_SUMMARY_KEYS)
    c.keys(w + ".stability", m.get("stability"), STABILITY_KEYS)
    c.keys(w + ".contamination", m.get("contamination"), ("delegated", "advisor", "read_outside", "web",
                                                          "unverifiable"))


def _check_metrics(c: _Checker, w: str, m: Any) -> None:
    if not c.keys(w, m, summary.METRIC_KEYS):
        return
    for k in TIME_KEYS:
        c.ts(f"{w}.{k}", m.get(k))
    for k, v in m.items():
        if k.endswith("_s") and k not in ("stream_chars_per_s", "stream_tokens_per_s"):
            c.num(f"{w}.{k}", v, 0, None)
    for k in ("turns", "tool_calls", "parallel_tool_calls", "failed_tool_calls", "image_reads", "tokens_in",
              "tokens_cached", "tokens_out", "tokens_reasoning", "tokens_in_uncached", "tokens_cache_read",
              "tokens_cache_write", "chars_out", "encoding_errors", "retries"):
        c.num(f"{w}.{k}", m.get(k), 0, None)
    c.num(f"{w}.hour_beijing", m.get("hour_beijing"), 0, 23)
    for k in ("split_ok", "quota_pool_exclusive", "render_running"):
        if m.get(k) not in (None, True, False):
            c.add(f"{w}.{k}", "应为布尔或 null")


def _check_run(c: _Checker, w: str, r: Any) -> None:
    if not c.keys(w, r, RUN_KEYS):
        return
    if r.get("status") not in RUN_STATUSES:
        c.add(w + ".status", f"取值不对: {r.get('status')!r}")
    for k in ("score", "score_nospeed"):
        c.num(f"{w}.{k}", r.get(k), 0, 100)
    for k in ("quality", "speed"):
        c.num(f"{w}.{k}", r.get(k), 0, 1)
    if r.get("zero") not in (None, *scoring.ZERO_REASONS):
        c.add(w + ".zero", f"取值不对: {r.get('zero')!r}")
    if not isinstance(r.get("passed"), bool):
        c.add(w + ".passed", "应为布尔")
    for k in ("superseded", "redispatched"):
        if not isinstance(r.get(k), bool):
            c.add(f"{w}.{k}", "应为布尔")
    if not isinstance(r.get("attempt"), int) or isinstance(r.get("attempt"), bool) or r["attempt"] < 1:
        c.add(w + ".attempt", "应为不小于 1 的整数")
    cc = r.get("card_check")
    if cc is not None and (not isinstance(cc, dict) or cc.get("status") not in (
            "verified", "unverified", "mismatch", "not_applicable")):
        c.add(w + ".card_check", "应为 null，或 status 是 verified、unverified、mismatch、not_applicable 的对象")
    for k in ("items", "constraints", "claims", "traps"):
        if not isinstance(r.get(k), list):
            c.add(f"{w}.{k}", "应为列表")
    for j, it in enumerate(r.get("items") or []):
        c.keys(f"{w}.items[{j}]", it, ("id", "label", "ok", "expected", "got", "dim"))
    for j, x in enumerate(r.get("constraints") or []):
        c.keys(f"{w}.constraints[{j}]", x, ("id", "ok", "detail", "hard"))
    for j, x in enumerate(r.get("claims") or []):
        if c.keys(f"{w}.claims[{j}]", x, ("id", "verdict", "said", "evidence", "required")):
            if x.get("verdict") not in common.VERDICTS:
                c.add(f"{w}.claims[{j}].verdict", "取值不对")
    for j, x in enumerate(r.get("traps") or []):
        c.keys(f"{w}.traps[{j}]", x, ("type", "handled", "detail"))
    hon = r.get("honesty")
    if c.keys(w + ".honesty", hon, ("overabstain", "answerable", "evidence_frames")):
        for j, x in enumerate(hon.get("evidence_frames") or []):
            c.keys(f"{w}.honesty.evidence_frames[{j}]", x, ("path", "claimed_t", "actual_t", "ok"))
    _check_metrics(c, w + ".metrics", r.get("metrics"))
    con = r.get("contamination")
    if c.keys(w + ".contamination", con, ("delegated", "advisor", "read_outside", "web", "verifiable")):
        if not isinstance(con.get("read_outside"), list):
            c.add(w + ".contamination.read_outside", "应为列表")
    if r.get("thread_link") is not None and not isinstance(r.get("thread_link"), str):
        c.add(w + ".thread_link", "应为字符串或 null")
