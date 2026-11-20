"""给一个考生建工作目录，并写出派活清单。

用法：
  python -B make_workdir.py --batch <批次> --examinee <考生id> [--only 岗位或题号 …] [--repeats N]
                            [--probes] [--no-tasks] [--include-sample] [--root 根目录]
  python -B make_workdir.py --batch <批次> --examinee <考生id> --redispatch 键 [键 …]

做的事：
  1. 读 runs/<考生id>/card.json（考生名片）。没有就写一份模板让你填，然后退出。
  2. 把选中的题从 tasks/ 复制到 <root>/<批次>/<考生目录>/<题号>/（重复跑的第 2 次起是 <题号>_r2），
     抹平文件时间，对建好的目录再做一遍泄漏检查。考生不支持的题不建目录，记成 unsupported。
  3. 加 --probes 时给 7 个探针各建 probe_repeats 次（suite.json），顺序按固定种子打乱。
  4. 写 runs/<考生id>/dispatch.json：每次派活一条，有线程标题、话术原文、工作目录。每条的话术交给考生模型，一条开一个新会话。

重派（--redispatch）：某一次作答记成了环境事故或作废，要重新派。每道题有尝试号，第一次是 1。
重派把这道题的尝试号加一，换一个新的记号和标题，建一个干净的新目录（<键>_a<尝试号>）；
旧尝试的清单条目挪进 dispatch.json 的 history，旧的收卷文件和作答副本挪进 collected/_history、work/_history，
旧的工作目录也挪走（不留在新目录旁边）。旧尝试不删，判分和汇总里作为历史保留。

这个脚本不派任何任务，不联网。工作目录建在 bench 外面，路径里不带 bench 字样。
同一个批次、同一个考生已经建过的目录不覆盖；想整批重来就换一个批次名。
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import common, leak, probes, summary, workdir  # noqa: E402

CAPABILITIES = ("text", "vision", "audio", "shell", "python", "ffmpeg")
CHANNEL_NOTES = Path(__file__).resolve().parent / "channel_notes"
CARD_TEMPLATE = {
    "_doc": ("考生名片。填好以后再跑 make_workdir.py。label 是全名，short_label 是榜单页上用的短名字（不超过 10 个字）。"
             "title_prefix 是线程标题开头的模型短名，比如 Sonnet。target 是调用考生时用的通道、模型和选项，"
             "按考生所用工具的实际能力填，fast 档不开。capabilities 里填 false 的能力，需要它的题不派、"
             "记成不支持。card 是榜单页名片表里显示的内容，取不到的留 null。"),
    "id": "", "label": "", "short_label": "", "vendor": "", "color_key": "other", "kind": "examinee",
    "title_prefix": "",
    "target": {"provider": "", "model": "", "options": {}},
    "capabilities": {"text": True, "vision": True, "audio": False, "shell": True, "python": True, "ffmpeg": True},
    "card": {"channel": "", "model": "", "effort": None, "fast": False, "context_tokens": None,
             "image_input": True, "video_input": False, "max_parallel": None, "quota_pool": None,
             "quota_reset": None, "plan_price": None, "notes": ""},
}


def is_fast(options: dict) -> bool:
    """选项里是不是开了加速档：Claude、Cursor 是 fastMode，Codex 是 serviceTier 等于 priority。"""
    return options.get("fastMode") is True or str(options.get("serviceTier") or "").casefold() == "priority"


def load_card(examinee: str) -> tuple[dict | None, list[str]]:
    """读考生名片并检查。返回 (名片, 问题)。文件不存在时写模板，名片返回 None。"""
    path = common.RUNS / examinee / "card.json"
    if not path.is_file():
        tpl = dict(CARD_TEMPLATE)
        tpl["id"] = examinee
        common.write_json(path, tpl, sort_keys=False)
        return None, [f"没有考生名片，已写了一份模板: {path}。填好再跑。"]
    card, problem = common.load_json(path)
    if problem:
        return None, [f"名片读不了: {problem}"]
    p = []
    if not isinstance(card, dict):
        return None, ["名片顶层不是对象"]
    if card.get("id") != examinee:
        p.append(f"名片里的 id 应为 {examinee}")
    for k in ("label", "short_label", "vendor", "title_prefix"):
        if not isinstance(card.get(k), str) or not card[k].strip():
            p.append(f"名片缺 {k}")
    if isinstance(card.get("short_label"), str) and summary.text_len(card["short_label"]) > summary.TITLE_LIMIT:
        p.append(f"short_label 超过 {summary.TITLE_LIMIT} 个字")
    if card.get("kind", "examinee") not in ("examinee", "baseline"):
        p.append("kind 只能是 examinee 或 baseline（baseline 是不派模型、放现成作答的自检考生）")
    caps = card.get("capabilities")
    if not isinstance(caps, dict) or any(not isinstance(caps.get(c), bool) for c in CAPABILITIES):
        p.append("capabilities 里 " + "、".join(CAPABILITIES) + " 都要填 true 或 false")
    tgt = card.get("target")
    model = tgt.get("model") if isinstance(tgt, dict) else None
    # 模型一般是字符串；也认 [提供方, 模型名] 这样的列表
    model_ok = (isinstance(model, str) and bool(model.strip())) or (
        isinstance(model, list) and bool(model) and all(isinstance(x, str) and x.strip() for x in model))
    if card.get("kind") != "baseline" and not model_ok:
        p.append("target.model 没填（字符串，或者 [提供方, 模型名] 这样的列表）")
    opts = (tgt or {}).get("options") if isinstance(tgt, dict) else None
    if isinstance(opts, dict) and is_fast(opts) and (card.get("card") or {}).get("fast") is not True:
        p.append("target.options 里开了加速档（fastMode 是 true，或 serviceTier 是 priority），但 card.fast 不是 true。"
                 "加速档要作为单独的考生，并在名片里标明")
    if not isinstance(card.get("card"), dict):
        p.append("名片缺 card")
    if card.get("channel_note") and not (CHANNEL_NOTES / f"{card['channel_note']}.txt").is_file():
        p.append(f"channel_note 指的说明 channel_notes/{card['channel_note']}.txt 不存在")
    return card, p


def missing_needs(needs: list, caps: dict) -> list[str]:
    return [n for n in needs or [] if caps.get(n) is False]


def entry(kind: str, key: str, batch: str, examinee: str, card: dict, prompt: str, work: Path | None,
          extra: dict, attempt: int = 1) -> dict:
    tag = workdir.tag_for(batch, examinee, key, attempt)
    title = f"{card['title_prefix']}: VideoBench {tag}"
    if (note := card.get("channel_note")) and prompt:  # 通道说明：只有名片声明了的考生，接在题面后面
        prompt += "\n\n" + (CHANNEL_NOTES / f"{note}.txt").read_text(encoding="utf-8").strip().replace(
            "{title}", title).replace("{tag}", tag)
        extra = {**extra, "channel_note": note}
    return add_addendum({"key": key, "kind": kind, "attempt": attempt, "tag": tag, "title": title,
                         "workdir": common.norm_path(str(work)) if work else None, "prompt": prompt,
                         "prompt_sha256": common.sha256_text(prompt), "status": "ready", **extra}, card)


def add_addendum(e: dict, card: dict) -> dict:
    """名片批准给这一次尝试的题面加注空一行接在题面末尾，条目记 prompt_addendum。加过的不重复加。"""
    text = workdir.prompt_addendum(card, e["key"], e.get("attempt") or 1)
    if not text or not e.get("prompt") or e.get("prompt_addendum"):
        return e
    prompt = e["prompt"] + "\n\n" + text
    return {**e, "prompt": prompt, "prompt_sha256": common.sha256_text(prompt), "prompt_addendum": text}


def redispatch(doc: dict, keys: list[str], card: dict, examinee: str, root: Path, base: Path, template: str) -> list[str]:
    """给清单里的这几条各开一个新的尝试。直接改 doc，返回失败说明。"""
    failures = []
    run_dir = common.RUNS / examinee
    by_key = {e.get("key"): i for i, e in enumerate(doc.get("entries") or []) if isinstance(e, dict)}
    for key in keys:
        if key not in by_key:
            failures.append(f"{key}: 派活清单里没有这一条")
            continue
        old = doc["entries"][by_key[key]]
        if old.get("status") != "ready":
            failures.append(f"{key}: 这一条是 {old.get('status')}，不用重派")
            continue
        n_old = int(old.get("attempt") or 1)
        n_new = n_old + 1
        dest = None
        try:
            if old["kind"] == "task":
                dest = base / f"{key}_a{n_new}"
                if dest.exists() and any(dest.iterdir()):
                    raise ValueError(f"新尝试的目录已经存在: {dest}")
                leaks = workdir.build_task_workdir(old["role"], old["task"], dest)
                prompt = workdir.render_prompt(template, dest)
            else:
                probe = probes.load_probe(old["probe"])
                if old["probe"] in probes.NO_WORKDIR:
                    leaks, prompt = [], probe["prompt"]
                else:
                    dest = base / f"{key}_a{n_new}"
                    if dest.exists() and any(dest.iterdir()):
                        raise ValueError(f"新尝试的目录已经存在: {dest}")
                    workdir.copy_files(probe["files_dir"], dest)
                    workdir.flatten_times(dest)
                    leaks = leak.check_directory(dest, [], None)
                    prompt = workdir.render_prompt(probe["prompt"], dest)
            if leaks:
                workdir.remove_tree(dest, root)
                raise ValueError("泄漏检查没过，目录已删: " + "；".join(leaks[:5]))
        except (OSError, ValueError, KeyError) as e:
            failures.append(f"{key}: 新尝试的目录没建成: {e}")
            continue
        # 旧尝试留档：收卷文件、作答副本、原工作目录都挪进 _history，不留在新目录旁边
        moved = {}
        tag = f"{key}.a{n_old}"
        pairs = [(run_dir / "collected" / f"{key}.json", run_dir / "collected" / "_history" / f"{tag}.json", "collected"),
                 (run_dir / "work" / key, run_dir / "work" / "_history" / tag, "work_copy")]
        if old.get("workdir"):
            pairs.append((Path(old["workdir"]), run_dir / "work" / "_history" / f"{tag}.workdir", "workdir_moved_to"))
        try:
            for src, dst, name in pairs:
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    moved[name] = common.norm_path(str(dst))
        except OSError as e:
            failures.append(f"{key}: 旧尝试没能挪走（旧线程还在跑？），没有重派: {e}")
            if dest is not None:
                workdir.remove_tree(dest, root)
            continue
        doc.setdefault("history", []).append(
            {**old, "superseded_at": common.beijing_iso(common.now_beijing()), "moved": moved})
        extra = {k: old[k] for k in ("task", "role", "repeat", "probe", "i", "order") if k in old}
        doc["entries"][by_key[key]] = entry(old["kind"], key, doc["batch"], examinee, card, prompt, dest, extra, n_new)
        print(f"{key}: 第 {n_new} 次尝试，新标题「{doc['entries'][by_key[key]]['title']}」"
              + (f"，新目录 {dest}" if dest else ""))
    return failures


def main(argv: list[str] | None = None) -> int:
    common.setup_stdout()
    ap = argparse.ArgumentParser(description="给一个考生建工作目录并写派活清单")
    ap.add_argument("--batch", required=True, help="批次名，只用字母数字下划线连字符")
    ap.add_argument("--examinee", required=True, help="考生 id，对应 runs/<id>/")
    ap.add_argument("--only", nargs="*", help="只建这些岗位或题号")
    ap.add_argument("--repeats", type=int, default=1, help="每道题建几次（重复跑看分差），默认 1")
    ap.add_argument("--probes", action="store_true", help="同时建速度探针")
    ap.add_argument("--no-tasks", action="store_true", help="不建岗位题，只建探针")
    ap.add_argument("--include-sample", action="store_true", help="把底座的样题也建上")
    ap.add_argument("--root", help="工作目录的根，默认沿用派活清单里记的根，没有清单时是系统临时目录下的 vbwork（VIDEOBENCH_WORK 可改）")
    ap.add_argument("--redispatch", nargs="+", metavar="键",
                    help="给这几条各开一个新的尝试号（环境事故、作废以后重派用），别的条目不动")
    a = ap.parse_args(argv)

    try:
        batch = workdir.check_slug(a.batch, "批次名")
        ex_dir = workdir.dir_slug(a.examinee)
        prev_doc = common.load_json(common.RUNS / a.examinee / "dispatch.json")[0]
        root = workdir.check_root(a.root or (prev_doc or {}).get("root") or str(workdir.VBWORK))
        template = workdir.load_prompt_template()
    except (ValueError, OSError) as e:
        print(f"参数有问题: {e}", file=sys.stderr)
        return 2
    if not (1 <= a.repeats <= 5):
        print("--repeats 只能是 1 到 5", file=sys.stderr)
        return 2
    if Path(a.examinee).name != a.examinee or a.examinee in (".", ".."):
        print("考生 id 不能带路径分隔符", file=sys.stderr)
        return 2
    card, problems = load_card(a.examinee)
    if card is None or problems:
        for x in problems:
            print("名片: " + x, file=sys.stderr)
        return 2
    caps = card["capabilities"]
    base = root / batch / ex_dir
    out_file = common.RUNS / a.examinee / "dispatch.json"
    old = {}
    history: list = []
    if a.redispatch:
        prev, problem = common.load_json(out_file)
        if problem or not isinstance(prev, dict) or prev.get("batch") != batch:
            print(f"重派要先有这个批次的派活清单: {out_file}（{problem or '批次对不上'}）", file=sys.stderr)
            return 2
        failures = redispatch(prev, a.redispatch, card, a.examinee, root, base, template)
        common.write_json(out_file, prev, sort_keys=False)
        print(f"派活清单 -> {out_file}（history 里有 {len(prev.get('history') or [])} 条旧尝试）")
        for x in failures:
            print("失败: " + x, file=sys.stderr)
        return 1 if failures else 0
    if out_file.is_file():
        prev, problem = common.load_json(out_file)
        if not problem and isinstance(prev, dict) and prev.get("batch") == batch:
            history = prev.get("history") if isinstance(prev.get("history"), list) else []
            old = {e["key"]: e for e in prev.get("entries") or [] if isinstance(e, dict) and "key" in e}
        elif not problem and isinstance(prev, dict):
            print(f"runs/{a.examinee}/dispatch.json 是批次 {prev.get('batch')} 的。一个考生的 runs 目录只放一个批次，"
                  f"要换批次请先把旧的 runs/{a.examinee}/ 改名留档。", file=sys.stderr)
            return 2

    kept = {k: e for k, e in old.items() if e.get("status") == "ready"}  # 以前不支持、名片改了以后的条目要重建
    entries: list[dict] = []
    failures: list[str] = []

    def build(key: str, make) -> Path | None:
        """建一个工作目录。已经建过的不动。make(dest) 返回泄漏问题列表。"""
        dest = base / key
        if key in old and old[key].get("status") == "ready":
            return dest
        if dest.exists() and any(dest.iterdir()):
            failures.append(f"{key}: 目录已经存在但不在派活清单里，没有动它: {dest}")
            return None
        try:
            leaks = make(dest)
        except (OSError, ValueError) as e:
            failures.append(f"{key}: 建目录失败: {e}")
            return None
        if leaks:
            failures.append(f"{key}: 泄漏检查没过，目录已删: " + "；".join(leaks[:5]))
            workdir.remove_tree(dest, root)
            return None
        return dest

    if not a.no_tasks:
        for role, tid in common.select_tasks(a.only, a.include_sample):
            meta = common.load_meta(role, tid)
            lack = missing_needs(meta.get("needs"), caps)
            for rep in range(1, a.repeats + 1):
                key = tid if rep == 1 else f"{tid}_r{rep}"
                extra = {"task": tid, "role": role, "repeat": rep}
                if lack:
                    e = entry("task", key, batch, a.examinee, card, "", None, extra)
                    e.update({"status": "unsupported", "prompt": "", "prompt_sha256": "",
                              "note": "考生不支持: " + "、".join(lack)})
                    entries.append(e)
                    continue
                dest = build(key, lambda d, r=role, t=tid: workdir.build_task_workdir(r, t, d))
                if dest is None:
                    continue
                entries.append(kept.get(key) or entry("task", key, batch, a.examinee, card,
                                                     workdir.render_prompt(template, dest), dest, extra))

    if a.probes:
        suite = common.read_suite()
        reps = int(suite.get("probe_repeats") or 3)
        # --only 里点了探针编号就只建那几个；没点探针编号就建全部
        only_probes = {x.upper() for x in a.only or []} & set(probes.PROBE_IDS)
        plan = [(p, i) for p in suite.get("probes") or [] for i in range(1, reps + 1)
                if not only_probes or p["id"] in only_probes]
        random.Random(f"{batch}|{a.examinee}|probes").shuffle(plan)
        for order, (p, i) in enumerate(plan, 1):
            pid = p["id"]
            key = f"{pid}_{i}"
            extra = {"probe": pid, "i": i, "order": order}
            lack = missing_needs(p.get("needs"), caps)
            if lack:
                e = entry("probe", key, batch, a.examinee, card, "", None, extra)
                e.update({"status": "unsupported", "prompt": "", "prompt_sha256": "",
                          "note": "考生不支持: " + "、".join(lack)})
                entries.append(e)
                continue
            try:
                probe = probes.load_probe(pid)
            except (OSError, ValueError) as e:
                failures.append(f"{key}: 探针读不了（先跑 python -B generators/probes.py）: {e}")
                continue
            if pid in probes.NO_WORKDIR:
                entries.append(kept.get(key) or entry("probe", key, batch, a.examinee, card, probe["prompt"], None, extra))
                continue

            def make(d: Path, src=probe["files_dir"]) -> list[str]:
                workdir.copy_files(src, d)
                workdir.flatten_times(d)
                return leak.check_directory(d, [], None)

            dest = build(key, make)
            if dest is None:
                continue
            entries.append(kept.get(key) or entry("probe", key, batch, a.examinee, card,
                                                 workdir.render_prompt(probe["prompt"], dest), dest, extra))

    # 清单里以前有、这次没选的条目保留
    keys = {e["key"] for e in entries}
    entries = [e for k, e in old.items() if k not in keys] + entries
    doc = {
        "_doc": ("派活清单。每条派给考生一次：title 当会话标题，prompt 原样当任务内容，一个字都不要改，"
                 "不要加别的话；通道、模型、选项用 target。每条都是一个新线程，作答期间不要给它追加消息"
                 "（追加了这次就记环境事故，要重派）。同一条不要派两次；要重派先跑 make_workdir.py --redispatch <键> "
                 "开新的尝试号，再照新的 title 和 prompt 派。"
                 "status 是 unsupported 的不派。探针按 order 的顺序派。派完以后跑 collect.py。"
                 "history 里是被重派顶掉的旧尝试，不要再派。"),
        "batch": batch, "examinee": a.examinee, "root": common.norm_path(str(root)),
        "prepared_at": common.beijing_iso(common.now_beijing()), "target": card.get("target"),
        "prompt_template_sha256": common.sha256_text(template),
        "entries": entries,
        "history": history,
    }
    common.write_json(out_file, doc, sort_keys=False)
    ready = sum(1 for e in entries if e["status"] == "ready")
    print(f"派活清单 -> {out_file}")
    print(f"共 {len(entries)} 条：要派 {ready} 条，不支持 {len(entries) - ready} 条。工作目录在 {base}")
    for x in failures:
        print("失败: " + x, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
