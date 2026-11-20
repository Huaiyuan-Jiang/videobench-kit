"""VideoBench「更多指标」页速度小测总表。build.py 的 speed_panel 调用，放在探针切换上面。

行 = 模型（顺序同侧栏：总分从高到低），列 = 7 种探针各自的 measures（summary.json probes[].measures）+ 有标准答案的探针加正确次数。
数和下面柱状图是同一份 v["probes"]，同样的格式（fsec / frate / 答对/次数），没数的格子留空。
每列按好坏上底色：秒数越小越好，/秒 和正确次数越大越好（同 speed_panel 的排序方向），最好的那格加亮。
格子用 data-t="st|探针|项|考生"（不用 data-k，免得重复）；行带 data-m，侧栏隐藏圆点照样管用。
点列名按这一列排（好的在前），再点回到默认顺序。
"""
from build import esc, frate, fsec, name_html

CSS = """
.stw{overflow-x:auto;margin:0 0 30px;border:1px solid var(--line);border-radius:6px;-webkit-overflow-scrolling:touch}
.stt{border-collapse:separate;border-spacing:0;width:100%;font-size:12.5px}
.stt th,.stt td{padding:7px 8px;border-bottom:1px solid var(--line);font-weight:400}
.stt tbody tr:last-child>*{border-bottom:0}
.stt .c0{position:sticky;left:0;z-index:1;background:var(--bg);text-align:left;width:184px;min-width:184px;
  box-shadow:inset 3px 0 0 var(--c,transparent),1px 0 0 var(--grid)}
.stt tbody .c0{padding-right:14px}
.stt .c0>span{display:block;position:relative;padding-left:28px;line-height:1.25;color:var(--fg)}
.stt .c0 .lo{position:absolute;left:0;top:50%;transform:translateY(-50%)}
.stt .pg{padding:10px 9px 6px;font-size:13px;color:var(--fg);text-align:left;border-bottom-color:var(--grid)}
.stt .mh{vertical-align:bottom;width:4.9em;min-width:4.9em;font-size:11.5px;line-height:1.3;color:var(--mute);
  text-align:right;overflow-wrap:break-word;hyphens:auto;cursor:pointer}
.stt .mh:hover,.stt .mh.on{color:var(--fg)}
.stt .mh.on{box-shadow:inset 0 -2px 0 var(--accent)}
.stt .g0{border-left:1px solid var(--grid)}
.stt td{text-align:right;white-space:nowrap;color:var(--mute);background:rgb(197 172 152/calc(var(--g,0)*.2))}
.stt td.hi{color:var(--fg);font-weight:600}
.stt tbody tr:hover>*{background-color:#14110F}
.stt tbody tr:hover td{background-image:linear-gradient(rgb(197 172 152/calc(var(--g,0)*.2)),rgb(197 172 152/calc(var(--g,0)*.2)))}
@media (max-width:720px){.stt{font-size:12px}.stt .c0{width:132px;min-width:132px;padding-left:10px}
  .stt .c0>span{padding-left:22px}.stt .c0 .lo{width:16px;height:16px;background-size:12px}}
"""

JS = """
document.querySelectorAll('.stt').forEach(t=>{const b=t.tBodies[0],rows=[...b.rows];
t.addEventListener('click',e=>{const h=e.target.closest('.mh');if(!h)return;const on=!h.classList.contains('on');
t.querySelectorAll('.mh').forEach(x=>x.classList.toggle('on',x===h&&on));const c=+h.dataset.c,d=+h.dataset.d;
const v=r=>{const s=r.cells[c].dataset.v;return s===undefined?null:+s};
(on?[...rows].sort((p,q)=>{const a=v(p),z=v(q);return a===null?(z===null?0:1):z===null?-1:d*(z-a)}):rows).forEach(r=>b.append(r))})});
"""


def columns(v):
    """[(探针, 项, {考生: 值})]：每个探针 measures 里有数的项按 MEASURE_ORDER 排，最后是正确次数"""
    out = []
    for p in v["probes"]:
        keep = set(p["measures"]) | {"correct"}
        out += [(p["id"], mk, vals) for mk, vals in p["metrics"] if mk in keep]  # metrics 已按 MEASURE_ORDER 排
    return out


def table(v, L):
    ms = sorted(v["models"].values(), key=lambda m: (m["q"] is None, -(m["q"] or 0), m["name"].lower()))
    cols = columns(v)
    groups = []
    for pid, _, _ in cols:
        if groups and groups[-1][0] == pid:
            groups[-1][1] += 1
        else:
            groups.append([pid, 1])
    first = {i for i, (pid, _, _) in enumerate(cols) if i == 0 or cols[i - 1][0] != pid}
    g0 = lambda i: " g0" if i in first else ""
    head1 = "".join(f'<th class="pg g0" colspan="{n}">{L("probe." + pid)}</th>' for pid, n in groups)
    head2, cells = "", {}
    for ci, (pid, mk, vals) in enumerate(cols):
        better = 1 if mk == "correct" or mk.endswith("_per_s") else -1  # 1 = 越大越好
        num = {i: (x[0] / x[1] if mk == "correct" else x) for i, x in vals.items()}
        ranked = sorted(set(num.values()), key=lambda x: -better * x)
        head2 += f'<th class="mh{g0(ci)}" data-c="{ci + 1}" data-d="{better}">{L("metric." + mk)}</th>'
        for i, x in vals.items():
            r = ranked.index(num[i])
            text = f"{x[0]}/{x[1]}" if mk == "correct" else (frate if better > 0 else fsec)(x)
            g = 1 - r / (len(ranked) - 1) if len(ranked) > 1 else 1
            cells[ci, i] = (" hi" if r == 0 and mk != "correct" else "",
                            f' data-t="st|{pid}|{mk}|{esc(i)}" data-v="{num[i]:.6g}" style="--g:{g:.2f}"', text)
    rows = ""
    for m in ms:
        tds = ""
        for ci in range(len(cols)):
            hi, attrs, text = cells.get((ci, m["id"]), ("", "", ""))
            cls = (g0(ci) + hi).strip()
            tds += "<td" + (f' class="{cls}"' if cls else "") + f"{attrs}>{text}</td>"
        rows += f'<tr data-m="{esc(m["id"])}" style="--c:{m["color"]}"><th class="c0"><span>{name_html(m)}</span></th>{tds}</tr>'
    return (f'<style>{CSS}</style><div class="stw"><table class="stt"><thead><tr><th class="c0" rowspan="2"></th>{head1}</tr>'
            f'<tr>{head2}</tr></thead><tbody>{rows}</tbody></table></div><script>{JS}</script>')
