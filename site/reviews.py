"""VideoBench 评价正文。detail.py 把它放进模型详情页。

正文取自 data/reviews/zh/<考生>.json（英文 data/reviews/en/），原样上页面：one_line、roles[].text、working_style、honesty、
dispatch_advice、confidence。channel 和 spot_checks 不上页面。岗位分和题分不在这里，在详情页下面的数据里。
评语目录多了文件、summary 里也有这个考生，重新 build 就会出现。
reviews.html（旧评价页地址）现在是跳转页：#考生 跳到 m/<考生>.html，没有这页就回成绩页。
"""
import json

from build import ROOT, dk, esc

DIR = ROOT / "data" / "reviews" / "zh"
SECTIONS = ("one_line", "working_style", "honesty", "dispatch", "confidence", "roles")


def load(model_ids, d=DIR):
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json")) if f.stem in model_ids}


def body_strings(rv):
    """页面上出现的评价正文（可见文字检查放行，隐私检查要查）"""
    for j in rv.values():
        yield from (j[k] for k in ("one_line", "working_style", "honesty", "confidence"))
        yield from (r["text"] for r in j["roles"])
        yield from j["dispatch_advice"]["give"] + j["dispatch_advice"]["avoid"]


def operator_strings(rv):
    """只给操作者看的字段，发布文件里一处都不许有"""
    def walk(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, (list, dict)):
            for y in (x.values() if isinstance(x, dict) else x):
                yield from walk(y)

    for j in rv.values():
        yield from (s for s in walk(j.get("spot_checks")) if len(s) >= 6)


def role_block(v, m, r, L):
    rid, mid = r["role"].split()[0], m["id"]
    label = next(ro["label"] for ro in v["roles"] if ro["id"] == rid)
    return (f'<div class="rr"><h3><a class="rl" href="../r/{rid}.html">{esc(label)}</a></h3><p {dk(f"t|{mid}|role|{rid}")}>{esc(r["text"])}</p>'
            '</div>')


def article(v, m, j, L):
    """评价六部分"""
    mid = m["id"]

    def p(key, cls=""):
        c = f' class="{cls}"' if cls else ""
        return f'<p{c} {dk(f"t|{mid}|{key}")}>{esc(j[key])}</p>'

    def lst(kind):
        items = "".join(f'<li {dk(f"t|{mid}|{kind}|{i}")}>{esc(s)}</li>' for i, s in enumerate(j["dispatch_advice"][kind]))
        return f'<div class="{kind}"><h3>{L("rv.dispatch." + kind)}</h3><ul>{items}</ul></div>'

    body = {
        "one_line": p("one_line", "one"),
        "roles": f'<div class="rrs">{"".join(role_block(v, m, r, L) for r in j["roles"])}</div>',
        "working_style": p("working_style"),
        "honesty": p("honesty"),
        "dispatch": f'<div class="rd">{lst("give")}{lst("avoid")}</div>',
        "confidence": p("confidence"),
    }
    return "".join(f'<section class="rs rs-{k}"><h2>{L("rv.sec." + k)}</h2>{body[k]}</section>' for k in SECTIONS)


def redirect(ids, scores_href, detail_dir="m"):
    """旧评价页地址的跳转页，页面上没有字"""
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>VideoBench</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#000000">'
            '<style>html{background:#000;color-scheme:dark}</style></head><body><script>'
            f'(()=>{{const ids={json.dumps(sorted(ids))},h=decodeURIComponent(location.hash.slice(1));'
            f'location.replace(ids.includes(h)?"{detail_dir}/"+h+".html":"{scores_href}")}})();</script></body></html>')
