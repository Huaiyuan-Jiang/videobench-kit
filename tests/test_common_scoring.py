"""公共代码和计分的单元测试。跑法：python -B -m unittest discover -s tests"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import common, scoring, summary, schema  # noqa: E402

CFG = common.read_scoring()


def meta(**kw) -> dict:
    m = {"id": "SAMPLE1", "role": "SAMPLE", "difficulty": "easy", "dimension": "accuracy", "q_pass": 0.9,
         "required_claims": [{"id": "n", "desc": "数量"}, {"id": "end_s", "desc": "结束时间"}],
         "constraints": [], "traps": [], "allowed_writes": ["out/**"], "required_deliverables": ["out/a.json"]}
    m.update(kw)
    return m


class TimeAndPath(unittest.TestCase):
    def test_parse_and_beijing(self):
        self.assertEqual(common.beijing_iso("2026-11-11T16:30:00.123Z"), "2026-11-12T00:30:00.123+08:00")
        self.assertEqual(common.beijing_iso("2026-11-11T16:30:00"), "2026-11-12T00:30:00.000+08:00")  # 没时区按 UTC
        self.assertIsNone(common.parse_ts(""))
        self.assertIsNone(common.parse_ts(None))
        self.assertIsNone(common.parse_ts("不是时间"))
        self.assertEqual(common.seconds_between("2026-11-11T00:00:00Z", "2026-11-11T08:00:01+08:00"), 1.0)

    def test_norm_path(self):
        self.assertEqual(common.norm_path("X:\\\\proj\\\\x"), "X:/proj/x")  # 转义过的反斜杠
        self.assertEqual(common.norm_path("d:\\a\\..\\b\\.\\c"), "D:/b/c")
        self.assertEqual(common.norm_path("/x/vbwork/x"), "X:/vbwork/x")
        self.assertEqual(common.norm_path('"D:/a b/c"'), "D:/a b/c")
        self.assertTrue(common.is_inside("x:/VBWORK/b/x", "X:\\vbwork\\b"))
        self.assertFalse(common.is_inside("X:/vbwork/b2", "X:/vbwork/b"))  # 前缀相同但不是子目录

    def test_rel_in_work_and_alias(self):
        work = "X:/bench/runs/x/work/T1"
        self.assertEqual(common.rel_in_work(work, "out/a.json"), "out/a.json")
        self.assertIsNone(common.rel_in_work(work, "../other/a.json"))
        self.assertIsNone(common.rel_in_work(work, "X:/vbwork/b/x/T1/out/a.json"))
        common.set_work_alias(work, "X:\\vbwork\\b\\x\\T1")
        try:
            self.assertEqual(common.rel_in_work(work, "X:/vbwork/b/x/T1/out/a.json"), "out/a.json")
            self.assertEqual(common.rel_in_work(work, "/x/vbwork/b/x/T1/out/a.json"), "out/a.json")
            self.assertIsNone(common.rel_in_work(work, "X:/vbwork/b/x/T2/out/a.json"))
        finally:
            common.set_work_alias(work, None)
        self.assertIsNone(common.rel_in_work(work, "X:/vbwork/b/x/T1/out/a.json"))
        self.assertIsNone(common.rel_in_work(work, None))
        self.assertIsNone(common.rel_in_work(work, "  "))

    def test_json_rejects_nan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text('{"x": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                common.read_json(p)
            obj, problem = common.load_json(p)
            self.assertIsNone(obj)
            self.assertTrue(problem)
            p.write_bytes(b"\xef\xbb\xbf" + '{"名字": "中文"}'.encode("utf-8"))  # 带 BOM 也能读
            self.assertEqual(common.read_json(p), {"名字": "中文"})
            with self.assertRaises(ValueError):
                common.dumps_json({"x": float("inf")})


class Claims(unittest.TestCase):
    def test_tolerance_boundary_is_supported(self):
        # 代码核查 H04：5.5 和 5.501、容差 0.001，差正好等于容差，应判支持
        self.assertEqual(common.claim_eq("end_s", 5.501, 5.5, tol=0.001)["verdict"], "supported")
        self.assertEqual(common.claim_eq("end_s", 5.499, 5.5, tol=0.001)["verdict"], "supported")
        self.assertEqual(common.claim_eq("end_s", 5.5011, 5.5, tol=0.001)["verdict"], "contradicted")
        self.assertEqual(common.claim_eq("end_s", 5.4989, 5.5, tol=0.001)["verdict"], "contradicted")
        self.assertTrue(common.within_tol(0.1 + 0.2, 0.3))
        self.assertTrue(common.within_tol(1e6 + 0.35, 1e6, 0.35))
        self.assertFalse(common.within_tol(3, 4))

    def test_claim_eq_types(self):
        self.assertEqual(common.claim_eq("n", "3", 3)["verdict"], "supported")  # 数字写成字符串
        self.assertEqual(common.claim_eq("n", 4, 3)["verdict"], "contradicted")
        self.assertEqual(common.claim_eq("n", None, 3)["verdict"], "unverifiable")  # 没声明不算谎报
        self.assertEqual(common.claim_eq("n", True, 1)["verdict"], "contradicted")  # 布尔不当数字
        self.assertEqual(common.claim_eq("s", " a ", "a")["verdict"], "supported")

    def test_missing_required_claims(self):
        ids = ["n", "end_s"]
        self.assertEqual(common.missing_required_claims({"claims": {}}, ids), ids)
        self.assertEqual(common.missing_required_claims({"claims": {"n": 0, "end_s": None}}, ids), ["end_s"])
        self.assertEqual(common.missing_required_claims({"claims": {"n": False, "end_s": " "}}, ids), ["end_s"])
        self.assertEqual(common.missing_required_claims({"claims": "不是对象"}, ids), ids)
        self.assertEqual(common.missing_required_claims(None, ids), ids)

    def _generic(self, claims) -> dict:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "out").mkdir()
            (work / "out" / "a.json").write_text("{}", encoding="utf-8")
            report = {"task_id": "SAMPLE1", "status": "done", "deliverables": ["out/a.json"], "files_modified": ["out/a.json"],
                      "commands_run": [], "checks": [], "frames_viewed": [], "claims": claims, "unresolved": [],
                      "notes": ""}
            (work / "report.json").write_text(json.dumps(report), encoding="utf-8")
            rep, problem = common.load_report(work)
            return common.generic_checks(work, meta(), {}, rep, problem, None)

    def test_required_claims_constraint(self):
        # 代码核查 H06：必填声明一条不写，要有一条不守约，但不能多出谎报
        g = self._generic({})
        con = {c["id"]: c for c in g["constraints"]}
        self.assertFalse(con["required_claims_present"]["ok"])
        self.assertIn("n", con["required_claims_present"]["detail"])
        self.assertFalse([c for c in g["claims"] if c["verdict"] == "contradicted"])
        g = self._generic({"n": 2, "end_s": 5.5})
        self.assertTrue({c["id"]: c for c in g["constraints"]}["required_claims_present"]["ok"])

    def test_missing_claims_fail_the_task_without_lie(self):
        g = self._generic({})
        claims = scoring.mark_required_claims(g["claims"], meta(), CFG)
        cons = scoring.mark_hard_constraints(g["constraints"], meta())
        sc = scoring.task_score({"quality": 1.0, "constraints": cons, "claims": claims}, "ok", meta(), CFG, [])
        self.assertEqual(sc["lies"], 0)  # 少写声明不算谎报；不是硬性要求，不清零（SPEC 11.23）
        self.assertEqual((sc["zero"], sc["score"]), (None, 100.0))

    def test_meta_short_title(self):
        long_title = "这是一个超过十个字的题目名字"
        self.assertTrue(any("short_title" in p for p in common.validate_meta({"title": long_title}, "SAMPLE", "SAMPLE1")))
        self.assertFalse(any("short_title" in p for p in common.validate_meta(
            {"title": long_title, "short_title": "短名字"}, "SAMPLE", "SAMPLE1")))
        self.assertTrue(any("short_title" in p for p in common.validate_meta(
            {"title": "短", "short_title": "这个短名字其实一点也不短"}, "SAMPLE", "SAMPLE1")))


class Scoring(unittest.TestCase):


    def test_status_rules(self):
        g = {"quality": 1.0, "constraints": [], "claims": []}
        ok = scoring.task_score(g, "ok", meta(), CFG, [])
        self.assertEqual((ok["status"], ok["score"], ok["zero"], ok["passed"]), ("ok", 100.0, None, True))
        for st, why in (("timeout", "timeout"), ("error", "error"), ("void", "cheat")):
            r = scoring.task_score(g, st, meta(), CFG, [])
            self.assertEqual((r["score"], r["counted"], r["passed"], r["zero"]), (0.0, True, False, why))
        self.assertEqual(scoring.task_score(g, "error", meta(), CFG, [scoring.NOTE_UNDELIVERED])["zero"], "no_submit")
        for st in ("env_error", "not_run"):
            self.assertFalse(scoring.task_score(g, st, meta(), CFG, [])["counted"])
        with self.assertRaises(ValueError):
            scoring.task_score(g, "weird", meta(), CFG, [])

    def test_zero_order_and_speed(self):
        lie = [{"id": "x", "verdict": "contradicted"}]
        hard = [{"id": "h", "ok": False, "hard": True}]
        self.assertEqual(scoring.zero_reason("timeout", lie, []), "timeout")     # 一道题只落进一种
        self.assertEqual(scoring.zero_reason("ok", lie, []), "lie")
        r = scoring.task_score({"quality": 0.6, "constraints": hard, "claims": []}, "ok", meta(), CFG, [])
        self.assertEqual((r["score"], r["zero"], r["hard_violations"]), (60.0, None, 1))  # 算法乙：硬性要求不清零（SPEC 11.24）
        self.assertEqual(scoring.relative_speed(50.0, 60.0, CFG), 1.0)
        self.assertEqual(scoring.relative_speed(3600.0, 60.0, CFG), 0.0)
        self.assertAlmostEqual(scoring.relative_speed(1830.0, 60.0, CFG), 0.75)  # x = 0.5，1 − x²
        self.assertAlmostEqual(scoring.with_speed(80.0, 0.5), 40.0)  # 不保底（SPEC 11.24）
        rows = [{"task": "T", "speed_t": t, "quality": q, "q_pass": 0.9, "zero": z, "rule": "score"}
                for t, q, z in ((10.0, 0.5, None), (40.0, 0.95, None), (5.0, 1.0, "lie"))]
        self.assertEqual(scoring.speed_bases(rows, CFG), {"T": 40.0})            # 只看做对了、没被清零的
        self.assertEqual(scoring.speed_bases(rows[:1], CFG), {"T": 10.0})        # 没人做对：退到 Q 最高的那组

    def test_wilson_and_random(self):
        lo, hi = scoring.wilson_interval(10, 20)
        self.assertAlmostEqual(lo, 0.2993, places=3)
        self.assertAlmostEqual(hi, 0.7007, places=3)
        self.assertIsNone(scoring.wilson_interval(0, 0))
        self.assertAlmostEqual(scoring.expected_random_q(20, 0.5), 0.088, places=2)  # SPEC 11.9：约 0.09
        self.assertEqual(common.normalize_chance(5, 20, 0.5), 0.0)  # 低于随机记 0
        self.assertAlmostEqual(common.normalize_chance(19, 20, 0.5), 0.9)

    def _t(self, **kw):
        t = {"counted": True, "rule": "score", "lies": 0, "hard_violations": 0, "passed": True}
        t.update(kw)
        return t




class SummaryText(unittest.TestCase):
    def test_default_notes_fit(self):
        for n in summary.DEFAULT_NOTES:
            self.assertEqual(len(summary.split_sentences(n)), 1, n)
            self.assertLessEqual(summary.text_len(n), summary.SENTENCE_LIMIT, n)
        for t, label in common.TRAP_TYPES.items():
            self.assertLessEqual(summary.text_len(summary.TRAP_SHORT[t]), summary.TITLE_LIMIT, t)
            self.assertLessEqual(summary.text_len(label), summary.SENTENCE_LIMIT, t)

    def test_display_problems(self):
        doc = {"dimensions": [{"label": "准确度"}], "roles": [{"label": "这个岗位的名字超过了十个字"}], "probes": [],
               "trap_types": [], "models": [{"id": "m", "short_label": "Sonnet 5.5", "usage_summary": {},
                                             "honesty_detail": {}}],
               "tasks": [{"id": "T1", "short_label": "短名字", "scenario": "这一句话写得特别特别长，长到肯定超过了二十个字的上限"}],
               "role_recommendations": [{"note": "领先 12.3 分，要求 8.0 分"}],
               "notes": ["一句。两句。", "这一条备注写得特别特别长长到肯定超过了二十个字的上限"]}
        errors, warnings = summary.display_text_problems(doc)
        self.assertEqual(len(errors), 3, errors)  # 岗位名超长、一条两句、一条超长
        self.assertEqual(len(warnings), 1, warnings)  # 题目详情只提醒
        self.assertEqual(summary.text_len("GPT-6.1 Sol"), 10)



if __name__ == "__main__":
    unittest.main()
