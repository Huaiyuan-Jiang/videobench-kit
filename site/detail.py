"""VideoBench 模型详情页。build.py 调用。

首页链过来的模型（build.detail(m) 有地址：考完且有评语）每个一页，写到 out/site/m/<id>.html。
页面：顶栏同全站；logo 和全名；四张指标卡（数、名次、全体小柱，本模型亮色）；左边评价六部分（reviews.article），
右边规格（summary.json 里这个考生的 card：通道、模型、思考档、fast、看图看视频、上下文，notes 和额度池不放）；
下面岗位得分、逐题得分、速度测试、谎报，都带名次。数据不全的项不出。
模型名右边是「含速度 / 不含速度」切换（同首页），管总分卡、岗位得分、逐题得分；不含速度那份的 key 带 0（q0、role0、run0、zr0）。
data-k 沿用成绩页的 key（q|id、role|岗位|id、run|id|题、zr|id|题、sp|探针|项|id …），名次是 rk|<key>|id，
字体：所有详情页共用 m/fonts.css，页面只 @import 它。
"""
from build import (SITE, detail, dk, esc, f1, fhour, fM, fk, foot, frate, fsec, logo, logo_css, mark_html, model_name, page, topbar,
                   pct, sv_panes, sv_tabs, task_row)
import reviews
from statistics import median

HASH = '<svg class="hs" viewBox="0 0 10 10" aria-hidden="true"><path d="M3.9 1 2.9 9M7.1 1 6.1 9M1.3 3.7h7.6M.9 6.3h7.6"/></svg>'
ICON = {
    "text": '<path d="M3 3.5h10M8 3.5v9.5"/>',
    "image": '<rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.6"/><path d="m2.4 11.6 3.6-3.6 3 3 2-2 2.6 2.6"/>'
             '<circle cx="10.6" cy="6" r="1.1"/>',
    "video": '<rect x="1.4" y="3.8" width="9.2" height="8.4" rx="1.6"/><path d="m10.6 7 4-2.2v6.4l-4-2.2"/>',
}


def icon(kind, on):
    return (f'<svg class="ic{" on" if on else ""}" viewBox="0 0 16 16" aria-hidden="true">{ICON[kind]}</svg>')


def switch(on):
    return (f'<svg class="sw{" on" if on else ""}" viewBox="0 0 28 16" aria-hidden="true">'
            f'<rect x=".75" y=".75" width="26.5" height="14.5" rx="7.25"/><circle cx="{20 if on else 8}" cy="8" r="4.2"/></svg>')


def fctx(n):
    return f"{n / 1e6:g}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


# ---------- 名次 ----------
def rank(vals, mid, desc):
    x = vals[mid]
    return 1 + sum(1 for y in vals.values() if (y > x if desc else y < x)), len(vals)


def rk(key, vals, mid, desc):
    r, n = rank(vals, mid, desc)
    return f'<span class="rk">{HASH}<span {dk(f"rk|{key}|{mid}")}>{r}/{n}</span></span>'


def model_vals(v, key):
    return {i: m[key] for i, m in v["models"].items() if m[key] is not None}


# ---------- 指标卡 ----------
def card(v, m, L, key, label, desc, fmt, subs=()):
    mid = m["id"]
    raw = model_vals(v, key)
    if mid not in raw:
        return ""
    vals = {i: x[0] for i, x in raw.items()} if key == "pass" else raw
    top = max(x[1] for x in raw.values()) if key == "pass" else (100 if key in ("q", "q0") else max(vals.values()))
    bars = "".join(f'<i class="me" data-bar="{key}|{mid}" style="--w:{pct(vals[i], top)}%"></i>' if i == mid
                   else f'<i style="--w:{pct(vals[i], top)}%"></i>'
                   for i in sorted(vals, key=lambda i: -vals[i] if desc else vals[i]))
    sub = "".join(f'<span>{L(lab)}<b {dk(f"{k}|{mid}")}>{f(m[vk])}</b></span>'
                  for lab, vk, k, f in subs if m[vk] is not None)
    return (f'<div class="kc"><div class="kh"><h2>{L(label)}</h2>{rk(key, vals, mid, desc)}</div>'
            f'<div class="mini">{bars}</div><div class="kb"><div class="sub">{sub}</div>'
            f'<div class="big" {dk(f"{key}|{mid}")}>{fmt(raw[mid])}</div></div></div>')


def cards(v, m, L):
    return "".join([
        sv_panes(lambda x: card(v, m, L, "q" + x, "hero.q", True, f1), " kp"),
        card(v, m, L, "tot", "hero.time", False, fhour),
        card(v, m, L, "tok", "hero.tok", False, fM,
             [("legend.cache", "tcr", "crt", fM), ("more.tab.out", "tout", "out", fk)]),
        card(v, m, L, "pass", "more.tab.pass", True, lambda p: f"{p[0]}/{p[1]}"),
    ])


# ---------- 规格 ----------
def tool(src):
    return src["card"].get("tool") or ""


def channel(src, en=False):
    c = src["card"]
    return (c.get("channel_en") or c.get("channel") or "") if en else (c.get("channel") or "")


def spec(src, L):
    c = src["card"]
    rows = [("md.spec.tool", esc(tool(src))), ("md.spec.channel", esc(channel(src, L.en))), ("md.spec.model", esc(c.get("model") or "")),
            ("md.spec.effort", esc(c.get("effort") or "")),
            ("md.spec.fast", switch(c["fast"]) if c.get("fast") is not None else ""),
            ("md.spec.input", icon("text", True) + icon("image", c.get("image_input")) + icon("video", c.get("video_input"))),
            ("md.spec.context", fctx(c["context_tokens"]) if c.get("context_tokens") else "")]
    body = "".join(f'<div class="sr"><dt>{L(k)}</dt><dd>{val}</dd></div>' for k, val in rows if val)
    return f'<aside class="spec"><h2>{L("md.spec.title")}</h2><dl>{body}</dl></aside>'


def spec_strings(src, en=False):
    c = src["card"]
    return [s for s in (tool(src), channel(src, en), c.get("model"), c.get("effort")) if s]


# ---------- 下面的数据 ----------
def meter(name, key, mid, vals, top, text, desc, w=None):
    return (f'<div class="mr"><span class="ml">{name}</span><span class="tt">'
            f'<span class="tf" data-bar="{key}|{mid}" style="--w:{w or pct(vals[mid], top)}%"></span></span>'
            f'<span class="tv" {dk(f"{key}|{mid}")}>{text}</span>{rk(key, vals, mid, desc)}</div>')


def roles_sec(v, m, L):
    mid = m["id"]
    if not m["roles"]:
        return ""

    def rows(x):
        out = []
        for ro in v["roles"]:
            rid = ro["id"]
            if rid in m["roles" + x]:
                vals = {i: o["roles" + x][rid] for i, o in v["models"].items() if rid in o["roles" + x]}
                out.append(meter(f'<a class="rl" href="../r/{rid}.html">{esc(ro["label"])}</a>', f"role{x}|{rid}", mid,
                                 vals, 100, f1(vals[mid]), True))
        return f'<div class="mrs">{"".join(out)}</div>'
    return f'<section class="ds"><h2>{L("roles.title")}</h2>{sv_panes(rows)}</section>'


def tasks_sec(v, m, L):
    if not m["runs"]:
        return ""

    def blocks(x):
        out = []
        for ro in v["roles"]:
            rows = "".join(task_row(v, m, t["id"], x) for t in v["tasks"] if t["role"] == ro["id"] and t["id"] in m["runs"])
            if rows:
                out.append(f'<div class="tb"><h3><a class="rl" href="../r/{ro["id"]}.html">{esc(ro["label"])}</a></h3>{rows}</div>')
        return f'<div class="tgrid">{"".join(out)}</div>'
    return f'<section class="ds"><h2>{L("tasks.title")}</h2>{sv_panes(blocks)}</section>'


def speed_sec(v, m, L):
    from build import MEASURE_ORDER
    mid, rows = m["id"], []
    for mk in MEASURE_ORDER:
        samples = {}
        for p in v["probes"]:
            for k, vs in p["metrics"]:
                if k == mk:
                    for i, x in vs.items():
                        samples.setdefault(i, []).append(x)
        if mid not in samples:
            continue
        correct = {i: (sum(x[0] for x in xs), sum(x[1] for x in xs)) for i, xs in samples.items()} if mk == "correct" else {}
        vals = {i: c / n for i, (c, n) in correct.items()} if correct else {i: median(xs) for i, xs in samples.items()}
        desc = mk.endswith("_per_s") or mk == "correct"
        lo = min((x for x in vals.values() if x > 0), default=0)
        w = None if desc else "100.0" if vals[mid] <= lo else pct(lo, vals[mid])
        text = "{}/{}".format(*correct[mid]) if correct else (frate if desc else fsec)(vals[mid])
        rows.append(meter(L("md.speed." + mk), "sm|" + mk, mid, vals, 1 if correct else max(vals.values()), text, desc, w))
    return f'<section class="ds ds-speed"><h2>{L("speed.title")}</h2><div class="mrs">{"".join(rows)}</div></section>' if rows else ""


def lies_sec(v, m, L):
    if m["lies"] is None:
        return ""
    mid = m["id"]
    chips = "".join(f'<span class="chip" {dk(f"lie|{mid}|{t}")}>{mark_html(v["marks"][t])}</span>' for t in m["lies"])
    return (f'<section class="ds dl2"><h2>{L("more.tab.lie")}</h2><span class="ln" {dk("lie|" + mid)}>{len(m["lies"])}</span>'
            f'<span class="chips">{chips}</span></section>')


# ---------- 拼页 ----------
def body(v, m, src, j, L):
    top = topbar(L, "sub")
    main = (f'<header class="dh">{logo(m)}<h1>{model_name(m)}</h1>{sv_tabs(L)}</header>'
            f'<div class="kcs">{cards(v, m, L)}</div>'
            f'<div class="db"><div class="rvw">{reviews.article(v, m, j, L)}</div>{spec(src, L)}</div>'
            f'{roles_sec(v, m, L)}{tasks_sec(v, m, L)}{speed_sec(v, m, L)}{lies_sec(v, m, L)}')
    return f'{top}<main class="dp" style="--c:{m["color"]}">{foot(main, L)}</main>'


def render(v, doc, rv, L, css):
    """-> {考生 id: 页面 html}，字体换成 @import fonts.css"""
    src = {m["id"]: m for m in doc["models"]}
    out = {}
    for m in v["models"].values():
        if detail(m):
            html_ = page(body(v, m, src[m["id"]], rv[m["id"]], L), css + "\n" + logo_css({m["vk"]}), L)
            out[m["id"]] = (html_.replace("/*{{FONT}}*/", "@import url(fonts.css);", 1)
                            .replace(f"<title>{SITE}</title>", f"<title>{esc(m['name'])}</title>", 1))
    return out


def allowed(doc, ids, en=False):
    """详情页上评价正文以外、可见文字检查要放行的规格值"""
    return [s for m in doc["models"] if m["id"] in ids for s in spec_strings(m, en)]
