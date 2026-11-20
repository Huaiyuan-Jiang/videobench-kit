"""VideoBench 岗位页。build.py 调用。

8 个岗位每个一页，写到 out/site/r/<岗位>.html，字体共用 m/fonts.css。
文字来自 roles.json（name / what / desc / tasks.<题>.title、desc），必有，缺了直接报错。
页面：顶栏同全站；8 个岗位互链；岗位名、what、desc；「含速度 / 不含速度」切换（同首页，管这页所有的分）；
岗位得分（同成绩页口径：该岗位题考全才进）；每道题一块：题号、难度、题名、介绍，按题分排的横条，带用时和做成，
整题 0 分的在条上写原因（labels zero.<原因>）。
  题分  有分就进（同成绩页逐题）
  用时  ok 和 late 给分钟数（同成绩页中位用时口径），超时写「超时」、出错写「出错」（labels.json rp.timeout、rp.error，不带星号）
  没考的（状态「不支持」的作答）一律不列：题表没有它那一行，岗位排名没有它
data-k：role|岗位|id、run|id|题、zr|id|题 沿用成绩页；用时 rt|id|题；做成放 data-ok="ok|id|题"，值 1/0。
不含速度那份的 key 前缀带 0（role0、run0、zr0、rt0、ok0）。
"""
import json

from build import (HERE, SITE, detail, esc, f1, fmin, foot, logo, logo_css, model_name, page, pct, scored_runs, sv_panes,
                   sv_tabs, topbar, zero_tag)

PATH = HERE / "roles.json"
TIMES = {"timeout": "rp.timeout", "error": "rp.error"}
TICK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 8.4 2.6 2.6L12 5.4"/></svg>'


def load(path=PATH):
    return json.loads(path.read_text(encoding="utf-8"))


def rename(doc, rj):
    """roles.json 的 name 写进 doc 的岗位名（所有页面的岗位名都从这里取）"""
    for r in doc["roles"]:
        r["label"] = rj[r["id"]]["name"]
    for t in doc["tasks"]:
        t["title_l"] = rj[t["role"]]["tasks"][t["id"]]["title"]


def strings(rj):
    """页面上出现的 roles.json 文字（可见文字检查放行，隐私检查要查）"""
    for j in rj.values():
        yield from (j[k] for k in ("name", "what", "desc"))
        for t in j["tasks"].values():
            yield from (t["title"], t["desc"])


def mlink(m):
    n = model_name(m)
    return f'<a class="rl" href="../{detail(m)}">{n}</a>' if detail(m) else f"<span>{n}</span>"


def run_time(r, L):
    """用时格的内容：ok、late 给分钟数，超时、出错写这两个字，其余空"""
    w = (r.get("metrics") or {}).get("wall_s")
    return fmin(w) if r["status"] in ("ok", "late") and w else L(TIMES[r["status"]]) if r["status"] in TIMES else ""


# ---------- 图 ----------
def rank_chart(v, rid, L):
    if not any(rid in m["roles"] for m in v["models"].values()):
        return ""

    def rows(x):
        items = sorted([(m, m["roles" + x][rid]) for m in v["models"].values() if rid in m["roles" + x]],
                       key=lambda t: (-t[1], t[0]["name"].lower()))
        return '<div class="rk2">' + "".join(
            f'<div class="rr2" style="--c:{m["color"]}">{logo(m)}<span class="nm2">{mlink(m)}</span>'
            f'<span class="tr2"><i data-bar="role{x}|{rid}|{m["id"]}" style="--w:{pct(s, 100)}%"></i></span>'
            f'<span class="vl2" data-k="role{x}|{rid}|{m["id"]}">{f1(s)}</span></div>' for m, s in items) + '</div>'
    return f'<section class="rsec"><h2>{L("rp.rank")}</h2>{sv_panes(rows)}</section>'


def task_block(v, doc, t, n, tj, L):
    tid, ms = t["id"], v["models"]
    rows = []
    for mid, m in ms.items():
        r = next((r for r in scored_runs(doc, mid, with_ref=True) if r["task"] == tid), None)
        if r and tid in m["runs"]:
            rows.append((m, run_time(r, L), bool(r["passed"])))
    if not rows:
        return ""

    def table(x):
        return "".join(
            f'<div class="tw" style="--c:{m["color"]}">{logo(m)}<span class="nm2">{mlink(m)}</span>'
            f'<span class="tr2"><i data-bar="run{x}|{m["id"]}|{tid}" style="--w:{pct(m["runs" + x][tid], 100)}%"></i>'
            f'{zero_tag(v, m, tid, x)}</span>'
            f'<span class="vl2" data-k="run{x}|{m["id"]}|{tid}">{f1(m["runs" + x][tid])}</span>'
            + (f'<span class="tm" data-k="rt{x}|{m["id"]}|{tid}">{w}</span>' if w else '<span class="tm"></span>')
            + f'<span class="ok{" on" if ok else ""}" data-ok="ok{x}|{m["id"]}|{tid}" data-v="{int(ok)}">{TICK if ok else ""}</span>'
            f'</div>' for m, w, ok in sorted(rows, key=lambda t: (-t[0]["runs" + x][tid], t[0]["name"].lower())))
    body = sv_panes(table)
    head = (f'<div class="th"><span class="th0"></span><span>{L("rp.col.score")}</span>'
            f'<span>{L("rp.col.time")}</span><span>{L("rp.col.pass")}</span></div>')
    ref = f'<span class="tag">{L("rp.ref")}</span>' if t.get("reference_only") else ""
    return (f'<section class="rsec tk3" id="{esc(tid)}"><div class="tl">'
            f'<div class="tmeta"><span class="tn">{n}</span><span class="df">{L("rp.diff")}'
            f'<b>{L("rp.diff." + t["difficulty"])}</b></span>{ref}</div>'
            f'<h2>{esc(tj["title"])}</h2><p>{esc(tj["desc"])}</p></div>'
            f'<div class="tbl">{head}{body}</div></section>')


# ---------- 拼页 ----------
def body(v, doc, ro, rj, L):
    rid = ro["id"]
    j = rj[rid]
    cur = ' aria-current="page"'
    tabs = "".join(f'<a href="{esc(r["id"])}.html"{cur if r["id"] == rid else ""}>{esc(r["label"])}</a>'
                   for r in doc["roles"])
    tasks = [t for t in doc["tasks"] if t["role"] == rid]
    blocks = "".join(task_block(v, doc, t, n, j["tasks"][t["id"]], L) for n, t in enumerate(tasks, 1))
    return (topbar(L, "sub") +
            f'<main class="rp"><nav class="rtabs">{tabs}</nav>'
            f'<header class="rh"><h1>{esc(ro["label"])}</h1>'
            f'<p class="what">{esc(j["what"])}</p><p class="desc">{esc(j["desc"])}</p>{sv_tabs(L)}</header>'
            f'{foot(rank_chart(v, rid, L) + blocks, L)}</main>')


def render(v, doc, rj, L, css):
    """-> {岗位 id: 页面 html}，字体换成 @import ../m/fonts.css"""
    out = {}
    keys = {m["vk"] for m in v["models"].values()}
    for ro in doc["roles"]:
        html_ = page(body(v, doc, ro, rj, L), css + "\n" + logo_css(keys), L)
        out[ro["id"]] = (html_.replace("/*{{FONT}}*/", "@import url(../m/fonts.css);", 1)
                         .replace(f"<title>{SITE}</title>", f"<title>{esc(ro['label'])}</title>", 1))
    return out
