"""VideoBench 成绩页。

  python -B site/build.py [--no-card]   读 data/（summary.json、tics.json、reviews/）和 labels.json、roles.json，
                                         写 out/site/（中英文首页、更多指标、详情页 m/、岗位页 r/）和 out/local/；
                                         过隐私检查（privacy.py）才写，查到东西就失败，什么都不写
  python -B build.py --labels X --out Y    用别的 labels 文件出页面（试排版用）
  python -B build.py --init-labels         按 LABELS 补齐 labels.json 里缺的项（已有 text 不动）

页面上的字只能是：模型名、数字和单位、8 个岗位名、VideoBench、labels.json 里的 text，详情页另加评价正文和规格值。
首页只放：总分竖柱图、总用时图、8 个岗位得分图、模型列表（点进详情页）；总分和岗位得分图共用一个「含速度 / 不含速度」切换
（labels sv.speed、sv.nospeed，默认含速度），详情页、岗位页同一个切换管页上所有总分、岗位分、题分；清零的题在题分那一行写原因（labels zero.<原因>）；
「更多指标」页 more.html（和详情页共用 m/fonts.css）放其余：每题 token、散点、合格/谎报/输出/缓存切换图、速度测试，
模型列表带隐藏圆点和重置。速度和用时相关的标签名旁自动带 * （Labels.__call__ 按 STAR 判），有 * 的页面底部放 note.speed 一句。
成绩页上的模型名（考完且有评价的）链到详情页 m/<id>.html（detail.py），详情页顶栏同全站。
build 结束时打印除模型名和数字以外的全部可见文字，出现别的字就失败。

入图规则（数据不全就不进那张图，不加标注）：
  总分        overall_status 是 ok（含速度 overall_score、不含速度 overall_score_nospeed；）
  做成/谎报/总用时   coverage.tasks_done == tasks_total；做成 = 不含速度的题分达到 100 × 合格线（runs.passed），谎报 = 因谎报清零的题；总用时 = 22 道计分题 wall_s 合计（超时的题按上限 3600 秒算，出错的按 wall_s，缺一题就不进）
  岗位        该岗位 coverage == total 且 index 不是 null，且该岗位没有「不支持」的作答
  每题        有分的作答；「不支持」的（没考）不进，超时、出错的照进
  输出 token  考完 且 有 tokens_out 的题数 > 0；每题 = 这些题的合计 ÷ 这些题数
  缓存读取    同上，换成 tokens_cache_read
  （所有 token 图：有一道计分题 tokens_source 以【推算】开头的考生不进，【真实·缺一次调用】【真实+推算】照进）
  消耗 token  考完 且 输入、输出、缓存读取三项都有记录的题数 > 0；每题 = 这些题上输入每题 + 输出每题（同一组题）；
              输入里含缓存读取，所以柱子三段 = 缓存读取 + 非缓存输入 + 输出，三段也取这组题的平均
  口头禅      data/tics.json 里有的考生（留完整思考原文的通道），值是 per10k
  名字        label 后面带 (思考档)，档位取 card.effort，没有就不带
  速度小测    该探针该项中位数不是 null（MEASURE_ORDER 里 summary 有的都放，不只 measures）
"""
import argparse
import base64
import copy
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import hashlib


def first_items(labels):
    return sorted(int(k.split(".")[1]) for k in labels if re.fullmatch(r"first\.\d+", k))


def first_ver(labels):
    return str(int(hashlib.sha256("|".join(labels[k]["text"] for k in sorted(labels) if k.startswith("first.")).encode()).hexdigest()[:8], 16))

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FONTS = Path(os.environ.get("VIDEOBENCH_FONTS") or HERE / "fonts")
SITE = "VideoBench"
PKG = ROOT / "out" / "site"
PUB = {"scores": "index.html", "reviews": "reviews.html", "more": "more.html"}
BASE, CARD, CARD_WH = "https://huaiyuan-jiang.github.io/videobench/", "card.png", (1200, 630)  # 预览卡片

# ---------- 品牌色（深底上用的版本） ----------
BRAND = {"anthropic": "#D97757",
         "openai": "#F2F2F2",
         "minimax": "#F2445F",
         "moonshot": "#2F8CFF",
         "deepseek": "#5B6EFF",
         "bytedance": "#22C3D6",
         "xiaomi": "#FF6900",
         "meituan": "#FFD000",
         "google": "#34A853",
         "zhipu": "#9AA8FF",
         "alibaba": "#A57CFF", "xai": "#A7B85A"}
FALLBACK = ["#9B7BFF", "#2FBF71", "#E05FD0"]

# ---------- labels.json 的项：key -> what ----------
MEASURE_WHAT = {
    "first_event_s": "首个事件时间（秒）：从考生开始到收到它的第一个事件（思考、工具调用或正文都算），3 次取中位。不是首字时间",
    "first_text_s": "首个正文事件时间（秒）：从考生开始到第一条正文事件到达，3 次取中位。各通道都取不到真正的首个 token，这是最接近首字的数",
    "first_stream_chunk_s": "首个流式块时间（秒）：本地会话日志里第一个流式块到达的时间，3 次取中位。只有部分通道有",
    "total_s": "总用时（秒）：这道探针从派出到交回，3 次取中位",
    "startup_s": "启动用时（秒）：从派出到平台把考生拉起来，3 次取中位。属于平台开销，不算模型",
    "model_s": "模型时间（秒）：交回 − 开始 − 命令时间 − 等批准时间，是减出来的残差，里面有读题、思考、写字，3 次取中位",
    "per_roundtrip_s": "每次工具往返（秒）：模型时间 ÷ 工具往返批次数（正常是 5 次），只有工具来回这道探针有，3 次取中位",
    "output_rate_chars_per_s": "端到端产出率（字/秒）：交回的字数 ÷ 模型时间（残差），3 次取中位。只在短答、长输出两道探针上算",
    "output_rate_tokens_per_s": "端到端产出率（token/秒）：输出 token ÷ 模型时间（残差），3 次取中位。只在短答、长输出两道探针上算",
    "stream_chars_per_s": "流式速度（字/秒）：流式分块的字数 ÷ 第一块到最后一块的时长，3 次取中位。只有部分通道报这个数",
    "correct": "答对次数：这道探针跑 3 次，完全答对的次数（显示成 答对/跑的次数）",
}
MEASURE_ORDER = ["first_text_s", "first_event_s", "first_stream_chunk_s", "output_rate_tokens_per_s",
                 "output_rate_chars_per_s", "stream_chars_per_s", "per_roundtrip_s", "total_s",
                 "model_s", "startup_s", "correct"]


def label_spec(doc):
    spec = {
        "models.title": "右侧（手机在顶部）模型列表的标题，后面跟一个模型个数。列表里点模型名进这个模型的详情页；「更多指标」页的列表里名字右边还有圆点，点了在所有图里隐藏/显示它",
        "models.toggle": "手机宽度下顶部的按钮，点开模型列表。后面跟模型个数",
        "nav.scatter": "顶栏导航第 1 项，点了去「更多指标」页的「质量对用时 / 质量对 token」散点图。全站每个页面的顶栏都有这一项，在更多指标页里是页内跳转",
        "nav.more": "顶栏导航第 2 项，点了去「更多指标」页的合格题 / 谎报 / 输出 token / 缓存读取那张切换图。全站每个页面的顶栏都有这一项，在更多指标页里是页内跳转",
        "nav.speed": "顶栏导航第 3 项，点了去「更多指标」页的速度小测（7 种探针）。全站每个页面的顶栏都有这一项，在更多指标页里是页内跳转",
        "lang.switch": "顶栏最右边的语言切换链接，点了去另一种语言的同一页。中文页上写英文版的名字，英文页上写中文版的名字",
        "hero.q": "首屏占满宽度的大竖柱图的标题（模型详情页总分指标卡的标题也是它）。每根柱子是一个模型的总分（0 到 100，22 道计分题题分的简单平均，每题一样重），右边切换含速度和不含速度两种，默认含速度，下面岗位得分图跟着同一个切换。从高到低排，柱下是厂商 logo 和模型名，点柱子或名字进这个模型的详情页。只考完的模型进图",
        "sv.speed": "切换按钮 1：含速度。总分、岗位得分、题分按含速度的算（题分 = 100 × 质量 × 速度分，速度分和这道题做对了的考生里最快的比，拖到 60 分钟为 0）。首页在总分大图右上，管总分图和岗位得分图；详情页在模型名右边、岗位页在排名图上面，管这一页所有的分",
        "sv.nospeed": "切换按钮 2：不含速度。总分、岗位得分、题分都不算速度（题分 = 100 × 质量）。位置和管的范围同 sv.speed",
        "zero.cheat": "题分那一行的小标签：这题整题 0 分，原因是作弊（把活转给别的模型或子代理、问顾问、联网）。出现在详情页逐题得分和岗位页每题的排名表里",
        "zero.timeout": "题分那一行的小标签：这题整题 0 分，原因是超时（满 60 分钟没交）",
        "zero.no_submit": "题分那一行的小标签：这题整题 0 分，原因是没交卷（通道显示跑完，但该交的文件和报告都没有）",
        "zero.error": "题分那一行的小标签：这题整题 0 分，原因是出错（中途报错中断）",
        "zero.lie": "题分那一行的小标签：这题整题 0 分，原因是谎报（交卷里写的声明被核实是假的）",
        "hero.total_tokens": "首页总用时图下方：全部计分题输入加输出 token 合计，缺任一题不入图",
        "tic.title": "首页最下面那张横条图的标题：口头禅频率。每个模型一条，数是每一万字思考原文里口头禅（Hmm、Wait、Actually、OK、再想想这类自言自语、犹豫、反悔、给自己打气的词）合计出现的次数，从多到少排。只有日志里留完整思考原文的模型进图（数据 data/tics.json）",
        "tic.unit": "口头禅频率那张图标题右边的单位：次/万字",
        "hero.time": "总用时：首页质量总分大竖柱图下面那张图的标题，模型详情页用时指标卡的标题也是它，名字旁自动带小星号。图里是每个模型全部 22 道计分题的用时合计（小时，单位 h），超时的题按 60 分钟算，出错的题按实际用时算。从短到长排，只考完的模型进图",
        "hero.tok": "「更多指标」页每题消耗 token 那块图的标题（模型详情页消耗 token 指标卡的标题也是它）。图里是平均每题消耗 token（输入加输出的总量，单位 M=百万），柱子分三段：暗色是缓存读取，模型色是非缓存输入，最右一小段是输出。考完的模型都进图，按有 token 记录的题取平均，从少到多排",
        "legend.cache": "「更多指标」页每题消耗 token 那张图柱子暗色那一段的名字：每题消耗 token 里的缓存读取部分",
        "legend.fresh": "「更多指标」页每题消耗 token 那张图柱子模型色那一段的名字：每题消耗 token 里的输入部分中缓存读取以外的部分",
        "legend.out": "「更多指标」页每题消耗 token 那张图柱子最右一小段的名字：每题消耗 token 里的输出部分",
        "scatter.tab.time": "「更多指标」页散点图左上的切换按钮 1：纵轴总分（不含速度），横轴总用时（全部 22 道计分题的用时合计，小时）。名字旁自动带小星号",
        "scatter.tab.tok": "「更多指标」页散点图左上的切换按钮 2：纵轴总分（不含速度），横轴平均每题消耗 token（输入加输出，单位 M）",
        "scatter.pareto": "散点图右上的开关：显示/隐藏帕累托线（连起来的那些模型，没有别的模型比它又快/又省、分又高）",
        "scale.lin": "散点图右上的横轴刻度切换：线性刻度",
        "scale.log": "散点图右上的横轴刻度切换：对数刻度（默认）",
        "axis.q": "散点图纵轴名：不含速度的总分（0 到 100）。横轴已经是用时或 token，所以纵轴用不含速度的那个",
        "axis.time": "散点图（用时那张）横轴名：总用时，单位小时。名字旁自动带小星号",
        "axis.tok": "散点图（token 那张）横轴名：平均每题消耗 token（输入加输出），单位百万",
        "roles.title": "8 个岗位小图那一块的标题（详情页岗位得分那一块也是它）。每个小图是一个岗位的得分（0 到 100）：这个岗位各计分题题分的简单平均，跟着含速度 / 不含速度的切换。从高到低。这个岗位的题没考全的模型不进那张小图",
        "more.tab.pass": "切换图的按钮 1（详情页指标卡也用它）：做成的题数，显示成 做成/计分题总数（22）。做成 = 不含速度的题分达到这题的及格线（多数题 90 分），只由题分推出来，没有另一套判定",
        "more.tab.lie": "切换图的按钮 2：谎报题数。每个模型一行，数字是因为谎报整题 0 分的有几道，后面列出是哪几道（题名）。谎报=交卷里写的声明被核实是假的",
        "more.tab.out": "切换图的按钮 3：平均每题输出 token（单位 k=千）",
        "more.tab.cache": "切换图的按钮 4：平均每题缓存读取 token（单位 M）",
        "speed.title": "「更多指标」页速度小测那一块的标题。下面先选探针（7 种），再选看哪一项速度",
        "tasks.title": "模型详情页逐题得分那一块的标题。下面每个岗位各几题，亮色条是这个模型这题的题分（0 到 100），跟着含速度 / 不含速度的切换。整题 0 分的题在条上写原因（zero.*）",
        "note.audit": "模型名旁 † 的浮层说明：按考生标注工具记录不完整的通道，悬停、聚焦或点按时显示，每页共用一份，不放页面底部。记号和考生在本项的 mark、examinees",
        "note.sol62": "模型名旁 * 的浮层说明：这个考生首轮几题因通道不支持音频输入报错，重考和余题题面多了一句加注；出现位置和交互同 †。记号和考生在本项的 mark、examinees",
        "note.speed": "页面底部的星号说明：带星号的速度和用时数据仅供参考，实际使用中受时间段、同时使用人数等影响。补全成一句完整的话。小字，前面自动带星号，有星号的页面各出现一次",
    }
    spec.update({
        "rv.sec.one_line": "模型详情页评价第 1 部分的小标题：对这个模型的一句话评价",
        "rv.sec.roles": "评价第 2 部分的小标题：八个岗位各一段文字评价，每段上面是岗位名（分数在页面下面的岗位得分和逐题得分里）",
        "rv.sec.working_style": "评价第 3 部分的小标题：干活的样子（速度、token、工具调用这些习惯）",
        "rv.sec.honesty": "评价第 4 部分的小标题：诚实（有没有谎报、越界）",
        "rv.sec.dispatch": "评价第 5 部分的小标题：派什么活给它。下面分两栏",
        "rv.dispatch.give": "第 5 部分左栏（手机在上面）的小标题：适合派给它的活",
        "rv.dispatch.avoid": "第 5 部分右栏（手机在下面）的小标题：别派给它的活",
        "rv.sec.confidence": "评价第 6 部分的小标题：把握（这份评价有多可靠，哪些结论证据薄）",
        "md.spec.title": "模型详情页评价右边（手机在评价上面）规格表的标题。表里是这个模型怎么考的：通道、模型、思考档、fast、能输入什么、上下文",
        "md.spec.tool": "规格表一行的名字：工具（模型是装在哪个编程工具里干活的），右边是 Claude Code、Codex、Kimi Code、DeepSeek Harness 这类名字",
        "md.spec.channel": "规格表一行的名字：通道（经哪个平台、哪条线路调用这个模型），右边是通道名",
        "md.spec.model": "规格表一行的名字：模型（调用时用的模型 ID），右边是 ID",
        "md.spec.effort": "规格表一行的名字：思考档（reasoning effort），右边是 high、medium 这类档位",
        "md.spec.fast": "规格表一行的名字：fast 模式（加速档），右边是一个开关图标，亮=开，暗=关",
        "md.spec.input": "规格表一行的名字：能输入什么，右边三个图标：文字、图片、视频，亮=能，暗=不能",
        "md.spec.context": "规格表一行的名字：上下文窗口（token），右边是 1M、262k 这类数",
    })
    spec.update({  # 岗位页 r/<岗位>.html（role.py）
        "rp.rank": "岗位页第一张图的标题：这个岗位的得分（0 到 100，各计分题题分的简单平均），跟着含速度 / 不含速度的切换，每个模型一条，从高到低。这个岗位的题没考全的模型不进图",
        "rp.diff": "岗位页每道题题名上方的小标签：难度，后面跟易 / 中 / 难",
        "rp.diff.easy": "岗位页「难度」后面的值：易（只是标注，每题在岗位得分和总分里一样重）",
        "rp.diff.medium": "岗位页「难度」后面的值：中",
        "rp.diff.hard": "岗位页「难度」后面的值：难",
        "rp.ref": "岗位页题名上方的小标签，只出现在只作参考的题上：这题的分不算进岗位得分和总分",
        "rp.col.score": "岗位页每道题右边排名表的列名：题分（0 到 100），每行一条横条加数，跟着含速度 / 不含速度的切换；整题 0 分的在条上写原因",
        "rp.col.time": "排名表的列名：单题用时（这道题从派出到交回，分钟，单位 m），名字旁自动带小星号。超时、出错的这格写超时、出错（见 rp.timeout、rp.error）",
        "rp.col.pass": "排名表的列名：做成（不含速度的题分达到这题的及格线，只由题分推出来），亮色对勾=做成，空圈=没做成",
        "rp.timeout": "排名表「用时」那一格的字，这题作答超时的模型显示它（代替分钟数），不带星号",
        "rp.error": "排名表「用时」那一格的字，这题作答出错（没交上来、中途报错中断）的模型显示它（代替分钟数），不带星号",
    })
    for p in doc["probes"]:
        spec[f"probe.{p['id']}"] = (f"速度小测里的探针切换按钮，探针 {p['id']}，原名「{p['title']}」。"
                                    f"量的是：{'、'.join(p['measures'])}")
    for mkey in MEASURE_ORDER:
        spec[f"metric.{mkey}"] = "速度小测里选看哪一项的按钮。" + MEASURE_WHAT[mkey]
    spec.update({k: v.get("what", "") for k, v in json.loads((HERE / "labels.json").read_text(encoding="utf-8")).items() if k.startswith(("md.speed.", "card.", "first.", "visits."))})
    return spec


# ---------- 格式 ----------
def f1(v):
    return f"{v:.1f}"


def fmin(s):
    return f"{s / 60:.1f}m"


def fhour(s):
    return f"{s / 3600:.1f}h"


def fM(v):
    return f"{v / 1e6:.2f}M"


def fk(v):
    return f"{v / 1e3:.1f}k"


def fsec(v):
    return f"{v:.1f}s"


def frate(v):
    return f"{v:.1f}/s"


def pct(v, top):
    return f"{100 * v / top:.1f}" if top else "0.0"


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- 颜色 ----------
def assign_colors(models):
    fb, out = {}, {}
    for m in sorted(models, key=lambda m: -(m.get("overall_score") or -1)):
        key = (m.get("color_key") or m.get("vendor") or "").lower()
        out[m["id"]] = BRAND.get(key) or fb.setdefault(key, FALLBACK[len(fb) % len(FALLBACK)])
    return out


def ink(hexc):
    """柱内字色：亮柱用黑字"""
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return "#000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else "#fff"


# ---------- 厂商 logo：logos/<color_key>.svg|png，没有就不显示 ----------
def logo_files(keys):
    for k in sorted(keys):
        for ext in ("svg", "png"):
            f = HERE / "logos" / f"{k}.{ext}"
            if f.exists():
                yield k, f
                break


def logo_css(keys):
    mime = {".svg": "image/svg+xml", ".png": "image/png"}
    return "\n".join(f'.lo[data-v="{k}"]{{display:block;background-image:url(data:{mime[f.suffix]};base64,'
                     f'{base64.b64encode(f.read_bytes()).decode()})}}' for k, f in logo_files(keys))


def logo(m):
    return f'<i class="lo" aria-hidden="true" data-v="{esc(m["vk"])}"></i>'


# ---------- 数据 ----------
def scored_runs(doc, mid, with_ref=False):
    ref = {t["id"] for t in doc["tasks"] if t.get("reference_only")}
    return [r for r in doc["runs"] if r["model"] == mid and r.get("counted") and not r.get("superseded")
            and (with_ref or r["task"] not in ref) and (r.get("repeat") or 1) == 1]


def task_marks(doc):
    """task id -> (岗位名, 序号, 题名, 岗位 id)；页面标签用题名"""
    role_label = {r["id"]: r["label"] for r in doc["roles"]}
    seen, out = {}, {}
    for t in doc["tasks"]:
        n = seen.get(t["role"], 0) + 1
        seen[t["role"]] = n
        out[t["id"]] = (role_label[t["role"]], n, t["title_l"], t["role"])
    return out


def tok_avg(runs, *keys):
    """这几项 token 都有记录的那些题上，每项的每题平均；一道都没有就全是 None"""
    g = [x for x in ([(r.get("metrics") or {}).get(k) for k in keys] for r in runs) if None not in x]
    return [sum(c) / len(g) for c in zip(*g)] if g else [None] * len(keys)


def drop_unfinished(doc):
    """没考完（计分题或速度小测没做全）的考生不进页面。速度基准在汇总里已经带上他们算过，这里不动。"""
    gone = {m["id"] for m in doc["models"] if m.get("kind") == "examinee" and (
        m["coverage"]["tasks_done"] < m["coverage"]["tasks_total"] or m["coverage"]["probes_done"] < len(doc["probes"]))}
    doc["models"] = [m for m in doc["models"] if m["id"] not in gone]
    for k in ("runs", "probe_runs"):
        doc[k] = [r for r in doc[k] if r["model"] not in gone]
    return gone


def name_marks(labels):
    """模型名旁的记号按考生配：labels.json 里带 mark 的项，考生 id -> (记号, 项名)"""
    return {i: (x["mark"], k) for k, x in labels.items() if x.get("mark") for i in x["examinees"]}


def tip_id(key):
    return key.split(".", 1)[1] + "-tip"


def build_view(doc, reviewed=(), nmarks={}):
    exams = [m for m in doc["models"] if m.get("kind") == "examinee"]
    colors = assign_colors(exams)
    marks = task_marks(doc)
    tics = json.loads((ROOT / "data" / "tics.json").read_text(encoding="utf-8"))["models"]
    models = {}
    for m in exams:
        i = m["id"]
        cov = m["coverage"]
        complete = cov["tasks_done"] == cov["tasks_total"]
        runs = scored_runs(doc, i)
        walls = [3600 if r["status"] == "timeout" else (r.get("metrics") or {}).get("wall_s") for r in runs]  # 
        # 有一道计分题的 token 是整题推算的（tokens_source 以【推算】开头）就不进任何 token 图
        estimated = any(((r.get("metrics") or {}).get("tokens_source") or "").startswith("【推算】") for r in runs)
        tr = runs if complete and not estimated else []
        tin, tos, tcs = tok_avg(tr, "tokens_in", "tokens_out", "tokens_cache_read")  # 消耗 token 图：三项同一组题
        (tout,), (tcr,) = tok_avg(tr, "tokens_out"), tok_avg(tr, "tokens_cache_read")
        effort = (m.get("card") or {}).get("effort")
        rs = m["role_scores"]
        skip = {r["role"] for r in runs if r["status"] == "unsupported"}  # 没考的岗位：有一题不支持就不进岗位图
        allr = [r for r in scored_runs(doc, i, with_ref=True) if r.get("score") is not None and r["status"] != "unsupported"]
        models[i] = {
            "id": i, "name": f'{m["label"]} ({effort})' if effort else m["label"], "color": colors[i], "rv": i in reviewed,
            "mark": nmarks.get(i),
            "vk": (m.get("color_key") or m.get("vendor") or "").lower(),
            "q": m["overall_score"] if m["overall_status"] == "ok" else None,
            "q0": m["overall_score_nospeed"] if m["overall_status"] == "ok" else None,
            "complete": complete,
            "ttotal": sum(r["metrics"]["tokens_in"] + r["metrics"]["tokens_out"] for r in runs) if tr and len(runs) == cov["tasks_total"] and all((r.get("metrics") or {}).get(k) is not None for r in runs for k in ("tokens_in", "tokens_out")) else None,
            "pass": (sum(r["passed"] or 0 for r in rs), sum(r["total"] or 0 for r in rs)) if complete else None,
            "lies": [r["task"] for r in runs if r["zero"] == "lie"] if complete else None,
            "tot": sum(walls) if complete and walls and all(walls) else None,
            "tok": tin + tos if tin is not None else None, "tsg": (tcs, tos),
            "tcr": tcr,
            "tout": tout,
            "tic": tics[i]["per10k"] if i in tics else None,
            "roles": {r["role"]: r["index"] for r in rs
                      if r.get("index") is not None and r.get("coverage") == r.get("total") and r["role"] not in skip},
            "roles0": {r["role"]: r["index_nospeed"] for r in rs
                       if r.get("index") is not None and r.get("coverage") == r.get("total") and r["role"] not in skip},
            "runs": {r["task"]: r["score"] for r in allr},
            "runs0": {r["task"]: r["score_nospeed"] for r in allr},
            "zero": {r["task"]: r["zero"] for r in allr if r["zero"]},
        }
    probes = []
    for p in doc["probes"]:
        rows = {pr["model"]: pr for pr in doc["probe_runs"] if pr["probe"] == p["id"] and pr["model"] in models}
        metrics = []
        for mk in MEASURE_ORDER:
            vals = {}
            for mid, pr in rows.items():
                if mk == "correct":
                    if p.get("has_answer") and pr.get("correct") is not None and pr.get("n"):
                        vals[mid] = (pr["correct"], pr["n"])
                elif (pr.get("median") or {}).get(mk) is not None:
                    vals[mid] = pr["median"][mk]
            if vals:
                metrics.append((mk, vals))
        probes.append({"id": p["id"], "metrics": metrics, "measures": p["measures"]})
    return {"models": models, "roles": doc["roles"], "tasks": doc["tasks"], "marks": marks, "probes": probes}


# ---------- 片段 ----------
STAR = {"hero.time", "scatter.tab.time", "axis.time", "rp.col.time"}  # 另加 metric.*（正确次数除外）
ZERO_REASONS = ("cheat", "timeout", "no_submit", "error", "lie")  # 同 bench/scoring.ZERO_REASONS
SV = (("s", "sv.speed", ""), ("n", "sv.nospeed", "0"))  # 含速度 / 不含速度：(pane, 标签, data-k 前缀后缀)


class Labels:
    def __init__(self, data, en=False):
        self.data, self.en = data, en
        self.used = set()

    def __call__(self, key):
        if key not in self.data:
            raise SystemExit(f"labels.json 缺 {key}，先跑 --init-labels")
        self.used.add(key)
        star = '<sup class="st">*</sup>' if key in STAR or (key.startswith("md.speed.") and key != "md.speed.correct") or (key.startswith("metric.") and key != "metric.correct") else ""
        return f'<span class="lb">{esc(self.data[key].get("text", ""))}</span>{star}'

    def text(self, key, fill=""):
        """纯文字（预览卡片用），{模型名}、{岗位名} 这类占位换成 fill"""
        self(key)
        return re.sub(r"\{[^}]+\}", fill, self.data[key]["text"])


def detail(m):
    """详情页地址；考完且有评价才有"""
    return f'm/{esc(m["id"])}.html' if m["rv"] and m["complete"] else None


def model_name(m):
    if not m["mark"]:
        return esc(m["name"])
    sym, key = m["mark"]
    head, gap, tail = esc(m["name"]).rpartition(" ")
    return (head + gap + f'<b class="audit-tail">{tail}<sup class="audit-mark" data-audit tabindex="0" role="button" '
            f'aria-describedby="{tip_id(key)}">{esc(sym)}</sup></b>')


def name_html(m):
    n = logo(m) + model_name(m)
    return f'<a class="rl" href="{detail(m)}">{n}</a>' if detail(m) else n


def dk(key):
    return f'data-k="{esc(key)}"'


def sv_tabs(L):
    """含速度 / 不含速度切换（全页共用一个 data-group="sv"，管页上所有带 sv 的 pane）"""
    return '<div class="tabs sv" role="tablist">' + "".join(
        f'<button type="button" class="tab{" on" if p == "s" else ""}" data-group="sv" data-tab="{p}">{L(key)}</button>'
        for p, key, _ in SV) + '</div>'


def sv_panes(fn, cls=""):
    """fn(后缀) -> html；两份分别放进含速度、不含速度两个 pane，默认含速度"""
    return "".join(f'<div class="pane{cls}{" on" if p == "s" else ""}" data-pane-group="sv" data-pane="{p}">{fn(x)}</div>'
                   for p, _, x in SV)


def zero_tag(v, m, tid, x):
    """清零的题在题分条上写原因；没清零的空着"""
    z = m["zero"].get(tid)
    return f'<span class="zr" {dk(f"zr{x}|{m["id"]}|{tid}")}>{v["zl"][z]}</span>' if z else ""


def bar_chart(items, key, top, fmt, cls="", stack=None):
    """items: [(model, value)]，已排好序。vertical at wide, horizontal rows at narrow (cls 'h' = always rows)."""
    out = []
    for m, v in items:
        seg = "".join(f'<i class="{c}" data-bar="{esc(key)}{sfx}|{m["id"]}" style="--w:{pct(fn(m), v)}%"></i>'
                      for c, sfx, fn in stack or ())
        out.append(
            f'<div class="b" data-m="{esc(m["id"])}" style="--c:{m["color"]}">'
            f'<div class="tk"><div class="fl" data-bar="{esc(key)}|{m["id"]}" style="--w:{pct(v, top)}%">{seg}'
            f'<span class="v" {dk(f"{key}|{m["id"]}")}>{fmt(v)}</span></div></div>'
            f'<div class="nm">{name_html(m)}</div></div>')
    return f'<div class="bars {cls}" style="--n:{len(items)}">{"".join(out)}</div>'


def ranked(models, key, desc):
    has = [m for m in models.values() if m[key] is not None]
    return sorted(has, key=lambda m: (-m[key] if desc else m[key], m["name"].lower()))


def qbars(items, key):
    """首屏竖柱：柱内分数，柱下 logo 和斜着的模型名；整根链到详情页"""
    top = math.ceil(max(x for _, x in items) / 10) * 10 + 10
    grid = "".join(f'<i class="vg" style="--y:{g / top:.4f}"></i>' for g in range(20, top + 1, 20))
    cols = []
    for m, x in items:
        tag, href = ("a", f' href="{detail(m)}"') if detail(m) else ("div", "")
        cols.append(f'<{tag} class="vb" data-m="{esc(m["id"])}"{href} style="--c:{m["color"]};--t:{ink(m["color"])}">'
                    f'<span class="vt"><span class="vf" data-bar="{key}|{m["id"]}" style="--w:{pct(x, 100)}%">'
                    f'<span class="vv" {dk(key + "|" + m["id"])}>{f1(x)}</span></span></span>'
                    f'{logo(m)}<span class="vn"><span>{model_name(m)}</span></span></{tag}>')
    return f'<div class="vbars" style="--k:{100 / top:.4f}">{grid}{"".join(cols)}</div>'


def hero(v, L):
    ms = v["models"]
    t = ranked(ms, "tot", False)
    big = (f'<div class="card chart big"><div class="ch"><h2>{L("hero.q")}</h2>{sv_tabs(L)}</div>'
           + sv_panes(lambda x: qbars([(m, m["q" + x]) for m in ranked(ms, "q" + x, True)], "q" + x)) + '</div>')
    time = bar_chart([(m, m["tot"]) for m in t], "tot", max(m["tot"] for m in t), fhour, "h")
    tokens = ranked(ms, "ttotal", False)
    tok = (f'<div class="card chart"><h2>{L("hero.total_tokens")}</h2>'
           + bar_chart([(m, m["ttotal"]) for m in tokens], "ttotal", max(m["ttotal"] for m in tokens), fM, "h") + '</div>') if tokens else ""
    ts = ranked(ms, "tic", True)
    tic = (f'<div class="card chart"><div class="ch"><h2>{L("tic.title")}</h2><span class="un">{L("tic.unit")}</span></div>'
           + bar_chart([(m, m["tic"]) for m in ts], "tic", max(m["tic"] for m in ts), f1, "h") + '</div>') if ts else ""
    return (f'<section class="hero" id="overview">{big}'
            f'<div class="card chart"><h2>{L("hero.time")}</h2>{time}</div>{tok}{tic}</section>')


def tok_panel(v, L):
    k = ranked(v["models"], "tok", False)
    chart = bar_chart([(m, m["tok"]) for m in k], "tok", max(m["tok"] for m in k), fM, "h",
                      stack=[("sg", "cr", lambda m: m["tsg"][0]), ("so", "out", lambda m: m["tsg"][1])])
    return (f'<section class="panel chart" id="tok"><div class="ph"><h2>{L("hero.tok")}</h2></div>{chart}'
            f'<div class="key"><span><i class="kc"></i>{L("legend.cache")}</span>'
            f'<span><i class="kf"></i>{L("legend.fresh")}</span>'
            f'<span><i class="ko"></i>{L("legend.out")}</span></div></section>')


# ---------- 散点 ----------
def nice_lin(hi):
    raw = hi / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    return [i * step for i in range(int(hi / step) + 1)], step


TIME_TICKS = [1800, 3600, 7200, 18000, 36000, 72000]


def log_ticks(lo, hi, cand=None):
    if cand:
        return [t for t in cand if lo <= t <= hi]
    out = []
    for e in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
        for s in (1, 2, 5):
            t = s * 10 ** e
            if lo <= t <= hi:
                out.append(t)
    return out


def tick_time(s):
    return f"{s / 3600:g}h"


def tick_tok(v):
    return f"{v / 1e6:g}M" if v >= 1e6 else f"{v / 1e3:g}k"


def pareto(pts):
    """x 越小越好，y 越大越好。pts: [(x, y, id)] -> 前沿上的点（按 x 升序）"""
    front, best = [], -1
    for x, y, i in sorted(pts):
        if y > best:
            front.append((x, y, i))
            best = y
    return front


def scatter(v, L, xkey, name, tickfmt, axis_key):
    ms = [m for m in v["models"].values() if m["q0"] is not None and m[xkey] is not None]
    xs = [m[xkey] for m in ms]
    ys = [m["q0"] for m in ms]
    y0 = max(0, math.floor((min(ys) - 8) / 10) * 10)
    y1 = min(100, math.ceil((max(ys) + 8) / 10) * 10)
    lin_hi = max(xs) * 1.08
    if name == "time":
        step = 3600 * nice_lin(lin_hi / 3600)[1]
        lin_ticks = [i * step for i in range(int(lin_hi / step) + 1)]
    else:
        lin_ticks, _ = nice_lin(lin_hi)
    lin_hi = max(lin_hi, lin_ticks[-1])
    lg_lo, lg_hi = min(xs) / 1.35, max(xs) * 1.35

    def xl(x):
        return 100 * x / lin_hi

    def xg(x):
        return 100 * (math.log(x) - math.log(lg_lo)) / (math.log(lg_hi) - math.log(lg_lo))

    def yy(y):
        return 100 - 100 * (y - y0) / (y1 - y0)

    # 右侧名字列：按 y 排，最小间距 4%
    order = sorted(ms, key=lambda m: -m["q0"])
    lab, gap = {}, 4.2
    prev = -99
    for m in order:
        p = max(yy(m["q0"]), prev + gap)
        lab[m["id"]] = p
        prev = p
    over = prev - 100
    if over > 0:
        for m in order:
            lab[m["id"]] -= over
        prev = 999
        for m in reversed(order):
            p = min(lab[m["id"]], prev - gap)
            lab[m["id"]] = p
            prev = p

    pts, lines, names, links = [], [], [], []
    for m in order:
        i, x, y = m["id"], m[xkey], m["q0"]
        style = f'--c:{m["color"]};--xl:{xl(x):.2f}%;--xg:{xg(x):.2f}%;--y:{yy(y):.2f}%'
        pts.append(f'<i class="pt" data-m="{esc(i)}" data-pt="{name}|{esc(i)}" data-x="{x:.6g}" data-y="{y:.6g}" '
                   f'style="{style}"></i>')
        lines.append(f'<i class="ld" data-m="{esc(i)}" style="{style}"></i>')
        links.append(f'<line data-m="{esc(i)}" x1="0" y1="{yy(y):.2f}" x2="14" y2="{lab[i]:.2f}" stroke="{m["color"]}"/>')
        names.append(f'<span class="sn" data-m="{esc(i)}" style="--c:{m["color"]};--ly:{lab[i]:.2f}%">'
                     f'<i></i>{name_html(m)}</span>')
    front = pareto([(m[xkey], m["q0"], m["id"]) for m in ms])
    poly_l = " ".join(f"{xl(x):.2f},{yy(y):.2f}" for x, y, _ in front)
    poly_g = " ".join(f"{xg(x):.2f},{yy(y):.2f}" for x, y, _ in front)
    yt = "".join(f'<i class="gl" style="--y:{yy(t):.2f}%"></i><span class="yt" style="--y:{yy(t):.2f}%">{t}</span>'
                 for t in range(y0, y1 + 1, 10))
    xtl = "".join(f'<span class="xt" style="--x:{xl(t):.2f}%">{tickfmt(t)}</span>' for t in lin_ticks)
    xtg = "".join(f'<span class="xt" style="--x:{xg(t):.2f}%">{tickfmt(t)}</span>'
                  for t in log_ticks(lg_lo, lg_hi, TIME_TICKS if name == "time" else None))
    return (f'<div class="pane sc log pareto{" on" if name == "time" else ""}" data-pane-group="sc" data-pane="{name}">'
            f'<div class="sc-y">{L("axis.q")}</div>'
            f'<div class="sc-body"><div class="plot">{yt}'
            f'<div class="xts lin">{xtl}</div><div class="xts lg">{xtg}</div>'
            f'<svg class="pf lin" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="{poly_l}"/></svg>'
            f'<svg class="pf lg" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="{poly_g}"/></svg>'
            f'{"".join(lines)}{"".join(pts)}</div>'
            f'<div class="sc-names"><svg viewBox="0 0 100 100" preserveAspectRatio="none">{"".join(links)}</svg>'
            f'{"".join(names)}</div></div>'
            f'<div class="sc-x">{L(axis_key)}</div></div>')


def scatter_panel(v, L):
    return (f'<section class="panel chart" id="scatter"><div class="ph">'
            f'<div class="tabs" role="tablist">'
            f'<button type="button" class="tab on" data-group="sc" data-tab="time">{L("scatter.tab.time")}</button>'
            f'<button type="button" class="tab" data-group="sc" data-tab="tok">{L("scatter.tab.tok")}</button></div>'
            f'<div class="ctl"><label class="chk"><input type="checkbox" data-pareto checked>{L("scatter.pareto")}</label>'
            f'<span class="seg"><button type="button" class="sb" data-scale="lin">{L("scale.lin")}</button>'
            f'<button type="button" class="sb on" data-scale="log">{L("scale.log")}</button></span></div></div>'
            + scatter(v, L, "tot", "time", tick_time, "axis.time")
            + scatter(v, L, "tok", "tok", tick_tok, "axis.tok") + "</section>")


# ---------- 岗位 ----------
def roles_panel(v, L):
    ms = v["models"]

    def grid(x):
        cards = []
        for ro in v["roles"]:
            rid = ro["id"]
            items = sorted([(m, m["roles" + x][rid]) for m in ms.values() if rid in m["roles" + x]],
                           key=lambda t: (-t[1], t[0]["name"].lower()))
            cards.append(f'<div class="rc"><h3><a class="rl" href="r/{rid}.html">{esc(ro["label"])}</a></h3>'
                         f'{bar_chart(items, f"role{x}|{rid}", 100, f1, "h")}</div>')
        return f'<div class="rgrid">{"".join(cards)}</div>'
    return (f'<section class="panel chart" id="roles"><div class="ph"><h2>{L("roles.title")}</h2></div>'
            f'{sv_panes(grid)}</section>')


# ---------- 合格 / 谎报 / 输出 / 缓存 / 合计 ----------
def mark_html(mark):
    role, n, title, rid = mark
    return f'<span class="mk" data-role="{rid}">{esc(title)}</span>'


def more_panel(v, L):
    ms = v["models"]
    tabs = [("pass", "more.tab.pass"), ("lie", "more.tab.lie"), ("out", "more.tab.out"),
            ("cache", "more.tab.cache")]
    head = "".join(f'<button type="button" class="tab{" on" if i == 0 else ""}" data-group="more" data-tab="{t}">'
                   f'{L(key)}</button>' for i, (t, key) in enumerate(tabs))
    pas = sorted([m for m in ms.values() if m["pass"]], key=lambda m: (-m["pass"][0], m["name"].lower()))
    pass_items = [(m, m["pass"][0]) for m in pas]
    tot_n = max(m["pass"][1] for m in pas)
    pass_chart = bar_chart(pass_items, "pass", tot_n, lambda x: "", "")
    # 合格题的字是 x/22：替换 v 的内容
    for m in pas:
        pass_chart = pass_chart.replace(f'{dk("pass|" + m["id"])}></span>',
                                        f'{dk("pass|" + m["id"])}>{m["pass"][0]}/{m["pass"][1]}</span>', 1)
    lie_rows = []
    for m in sorted([m for m in ms.values() if m["lies"] is not None],
                    key=lambda m: (-len(m["lies"]), m["name"].lower())):
        chips = "".join(f'<span class="chip" {dk(f"lie|{m["id"]}|{t}")}>{mark_html(v["marks"][t])}</span>'
                        for t in m["lies"])
        lie_rows.append(f'<div class="lr" data-m="{esc(m["id"])}" style="--c:{m["color"]}">'
                        f'<span class="nm">{name_html(m)}</span>'
                        f'<span class="ln" {dk("lie|" + m["id"])}>{len(m["lies"])}</span>'
                        f'<span class="chips">{chips}</span></div>')
    out = ranked(ms, "tout", False)
    cr = ranked(ms, "tcr", False)
    panes = [
        ("pass", pass_chart),
        ("lie", f'<div class="lies">{"".join(lie_rows)}</div>'),
        ("out", bar_chart([(m, m["tout"]) for m in out], "out", max(m["tout"] for m in out), fk)),
        ("cache", bar_chart([(m, m["tcr"]) for m in cr], "crt", max(m["tcr"] for m in cr), fM)),
    ]
    body = "".join(f'<div class="pane{" on" if i == 0 else ""}" data-pane-group="more" data-pane="{t}">{c}</div>'
                   for i, (t, c) in enumerate(panes))
    return (f'<section class="panel chart" id="more"><div class="ph"><div class="tabs">{head}</div></div>'
            f'{body}</section>')


# ---------- 速度小测 ----------
def speed_panel(v, L):
    ms = v["models"]
    ptabs, ppanes = [], []
    for pi, p in enumerate(v["probes"]):
        pid = p["id"]
        ptabs.append(f'<button type="button" class="tab{" on" if pi == 0 else ""}" data-group="probe" '
                     f'data-tab="{pid}">{L("probe." + pid)}</button>')
        mtabs, mpanes = [], []
        for mi, (mk, vals) in enumerate(p["metrics"]):
            g = f"pm{pid}"
            mtabs.append(f'<button type="button" class="tab sm{" on" if mi == 0 else ""}" data-group="{g}" '
                         f'data-tab="{mk}">{L("metric." + mk)}</button>')
            key = f"sp|{pid}|{mk}"
            if mk == "correct":
                items = sorted([(ms[i], c / n) for i, (c, n) in vals.items()],
                               key=lambda x: (-x[1], x[0]["name"].lower()))
                chart = bar_chart(items, key, 1, lambda x: "")
                for i, (c, n) in vals.items():
                    chart = chart.replace(f'{dk(key + "|" + i)}></span>', f'{dk(key + "|" + i)}>{c}/{n}</span>', 1)
            else:
                desc = mk.endswith("_per_s")
                items = sorted([(ms[i], x) for i, x in vals.items()],
                               key=lambda t: (-t[1] if desc else t[1], t[0]["name"].lower()))
                chart = bar_chart(items, key, max(vals.values()), frate if desc else fsec)
            mpanes.append(f'<div class="pane{" on" if mi == 0 else ""}" data-pane-group="{g}" data-pane="{mk}">'
                          f'{chart}</div>')
        ppanes.append(f'<div class="pane{" on" if pi == 0 else ""}" data-pane-group="probe" data-pane="{pid}">'
                      f'<div class="tabs sub">{"".join(mtabs)}</div>{"".join(mpanes)}</div>')
    import speed_table  # 总表（speed_table.py），放在探针切换上面
    return (f'<section class="panel chart" id="speed"><div class="ph"><h2>{L("speed.title")}</h2></div>'
            f'{speed_table.table(v, L)}<div class="tabs">{"".join(ptabs)}</div>{"".join(ppanes)}</section>')


# ---------- 每题 ----------
def task_row(v, m, tid, x=""):
    """逐题一行；x 是 "" 含速度、"0" 不含速度"""
    s, mid = m["runs" + x][tid], m["id"]
    return (f'<div class="tr"><span class="ti">{esc(v["marks"][tid][2])}</span><span class="tt">'
            f'<span class="tf" data-bar="run{x}|{mid}|{tid}" style="--w:{pct(s, 100)}%"></span>{zero_tag(v, m, tid, x)}</span>'
            f'<span class="tv" {dk(f"run{x}|{mid}|{tid}")}>{f1(s)}</span></div>')


# ---------- 侧栏 ----------
def sidebar(v, L, toggles):
    """toggles：更多指标页才有隐藏/显示圆点和重置"""
    ms = sorted(v["models"].values(), key=lambda m: (m["q"] is None, -(m["q"] or 0), m["name"].lower()))
    items = "".join(f'<li style="--c:{m["color"]}">'
                    + (f'<a class="mn" href="{detail(m)}">' if detail(m) else '<span class="mn">')
                    + f'{logo(m)}<span>{model_name(m)}</span>' + ("</a>" if detail(m) else "</span>")
                    + (f'<button type="button" class="mt" data-toggle="{esc(m["id"])}"><i></i></button>' if toggles else "")
                    + '</li>' for m in ms)
    reset = ('<button type="button" class="reset" data-reset><svg viewBox="0 0 16 16" width="14" height="14">'
             '<path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v3h3" fill="none" stroke="currentColor" stroke-width="1.4"/>'
             '</svg></button>') if toggles else ""
    return (f'<aside class="side"><button type="button" class="side-toggle" data-side>{L("models.toggle")}'
            f'<b>{len(ms)}</b></button><div class="side-in"><div class="sh"><h2>{L("models.title")}'
            f'<b>{len(ms)}</b></h2>{reset}</div><ul>{items}</ul></div></aside>')


def topbar(L, where):
    """全站同一套顶栏：站名回成绩页，三项导航去更多指标页的三个位置（where：home / more / sub=m/、r/ 下的页）"""
    up = "../" if where == "sub" else ""
    to = "" if where == "more" else up + PUB["more"]
    nav = "".join(f'<a href="{to}#{s}">{L("nav." + s)}</a>' for s in ("scatter", "more", "speed"))
    mark = "#overview" if where == "home" else up + PUB["scores"]
    return (f'<header class="top"><a class="mark" href="{mark}" data-home>{SITE}</a><nav>{nav}</nav>'
            f'<a class="lang" href="{{{{ALT}}}}" data-lang>{L("lang.switch")}</a></header>')


# ---------- 可见文字检查 ----------
class TextGrab(HTMLParser):
    SKIP = {"style", "script"}

    def __init__(self):
        super().__init__()
        self.stack, self.out, self.attrs = [], [], []

    def handle_starttag(self, tag, attrs):
        for a, val in attrs:
            if a in ("title", "aria-label", "alt", "placeholder") and val:
                self.attrs.append(val)
        if tag not in ("input", "br", "img", "meta", "link", "path", "line", "polyline"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data):
        s = data.strip()
        if s and not (self.SKIP & set(self.stack)):
            self.out.append(s)


NUM = re.compile(r"^\d+(\.\d+)?(s|m|h|k|M|%|/s)?$|^\d+/\d+$")


def check_visible(page, models, roles, labels, body=()):
    g = TextGrab()
    g.feed(page)
    names = {m["name"] for m in models.values()} | set(body)
    names |= {part for m in models.values() if m["mark"] for part in m["name"].rsplit(" ", 1)}
    allowed = set(roles) | {SITE, "*"} | {x[k] for x in labels.values() for k in ("text", "mark") if x.get(k)}
    extra, bad = [], []
    for s in g.out + g.attrs:
        if s in names or NUM.match(s):
            continue
        extra.append(s)
        if s not in allowed:
            bad.append(s)
    if "content:\"" in page.replace(" ", "") and re.search(r'content:\s*"[^"]+"', page):
        bad.append("CSS content 里有字")
    return extra, bad, g.attrs


# ---------- 字体 ----------
def subset_font(path, text, flavor="woff2"):
    from fontTools import subset
    opts = subset.Options()
    opts.flavor = flavor
    opts.layout_features = ["tnum", "kern", "liga", "calt"]
    opts.notdef_outline = True
    font = subset.load_font(str(path), opts)
    sub = subset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(font)
    buf = io.BytesIO()
    subset.save_font(font, buf, opts)
    return base64.b64encode(buf.getvalue()).decode()


def font_faces(text):
    latin = "".join(sorted({c for c in text if ord(c) < 0x2E80})) + "0123456789.%/kMsmh"
    cjk = "".join(sorted({c for c in text if ord(c) >= 0x2E80}))
    css = []
    for w, f in ((400, "Inter-Regular.ttf"), (500, "Inter-Medium.ttf"), (600, "Inter-SemiBold.ttf")):
        if (FONTS / f).exists():
            css.append(f'@font-face{{font-family:"VBL";font-weight:{w};font-display:block;'
                       f'src:url(data:font/woff2;base64,{subset_font(FONTS / f, latin)}) format("woff2")}}')
    sc = FONTS / "NotoSansSC-VF.ttf"
    if cjk and sc.exists():
        css.append(f'@font-face{{font-family:"VBC";font-weight:100 900;font-display:block;'
                   f'src:url(data:font/woff2;base64,{subset_font(sc, cjk)}) format("woff2")}}')
    return "\n".join(css)


# ---------- 拼页 ----------
def foot(body, L):
    """页面上有速度和用时的 * 就在底部放一句说明（一页一次）"""
    return body + f'<p class="note"><sup class="st">*</sup>{L("note.speed")}</p>' if 'class="st"' in body else body


def page(body, css="", L=None):
    for key, x in (L.data.items() if L else ()):  # 每种记号一份浮层，页面上有这种记号才放
        if x.get("mark") and f'aria-describedby="{tip_id(key)}"' in body:
            body += f'<div id="{tip_id(key)}" class="mark-tip" role="tooltip" hidden>{L(key)}</div>'
    if L:  # 累计访客默认收着，拿到数才显示；第一次进站的提示，template 里的脚本决定弹不弹
        body += (f'<footer id="vc" class="vc" hidden>{L("visits.total")}<b></b></footer>'
                 f'<dialog id="first" data-v="{first_ver(L.data)}" aria-labelledby="first-t" aria-describedby="first-b"><h2 id="first-t">{L("first.title")}</h2>'
                 f'<div class="fb" id="first-b"><p class="lead">{L("first.body")}</p>'
                 + "".join(f'<p class="it">{f"<b>{L(t)}</b>" if t in L.data else ""}{L(f"first.{n}")}</p>' for n in first_items(L.data) for t in [f"first.{n}.term"])
                 + f'<p class="foot">{L("first.foot")}</p></div><form method="dialog"><button autofocus>{L("first.ok")}</button></form></dialog>')
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    if css:
        tpl = tpl.replace('<style id="hide"></style>', f'<style id="hide"></style>\n<style>\n{css}</style>')
    return tpl.replace("{{TITLE}}", SITE).replace("{{BODY}}", body)


def render(v, L):
    """-> (成绩页, 更多指标页)"""
    css = logo_css({m["vk"] for m in v["models"].values()})
    home = topbar(L, "home") + '<div class="wrap"><main>' + foot(hero(v, L) + roles_panel(v, L), L) + "</main>"
    more = (topbar(L, "more") + '<div class="wrap"><main>'
            + foot(scatter_panel(v, L) + tok_panel(v, L) + more_panel(v, L) + speed_panel(v, L), L) + "</main>")
    return (page(home + sidebar(v, L, False) + "</div>", css, L),
            page(more + sidebar(v, L, True) + "</div>", css, L))


def card_meta(name, L, v, roles, pre=""):
    """发布路径 name 那一页的预览卡片标签（X 的爬虫不跑 JS，只读 head；）；pre：英文版 en/"""
    d, _, f = name.rpartition("/")
    kind, fill = ("model", v["models"][f[:-5]]["name"]) if d == "m" else ("role", roles[f[:-5]]) if d == "r" else ("home", "")
    desc = L.text(f"card.desc.{kind}", fill)
    tags = (("name", "description", desc), ("property", "og:type", "website"), ("property", "og:site_name", SITE),
            ("property", "og:title", L.text(f"card.title.{kind}", fill)), ("property", "og:description", desc),
            ("property", "og:url", BASE + pre + ("" if name == PUB["scores"] else name)), ("property", "og:image", BASE + pre + CARD),
            ("property", "og:image:width", CARD_WH[0]), ("property", "og:image:height", CARD_WH[1]),
            ("name", "twitter:card", "summary_large_image"), ("name", "twitter:title", L.text(f"card.title.{kind}", fill)),
            ("name", "twitter:description", desc), ("name", "twitter:image", BASE + pre + CARD))
    return "".join(f'<meta {a}="{b}" content="{esc(c)}">' for a, b, c in tags)


def card_png(index, out, L, want):
    """预览卡片图：打开刚写好的首页，只留总分大图（含速度，默认那份）加大字小字，截 1200×630。数和首页是同一份 DOM"""
    from playwright.sync_api import sync_playwright
    ARGS, CHROME = ["--mute-audio"], os.environ.get("VIDEOBENCH_CHROME")
    big, small = L.text("card.img.big"), L.text("card.img.small")
    css = (font_faces(big + small + SITE).replace('"VBL"', '"VKL"').replace('"VBC"', '"VKC"')
           + 'html,body{margin:0;overflow:hidden}#og{width:%dpx;height:%dpx;padding:44px 52px 0;display:flex;flex-direction:column}'
           '#og header{display:flex;justify-content:space-between;align-items:baseline}#og header b{font:600 22px "VKL";color:var(--accent)}'
           '#og h1{margin:0;font:600 62px/1.1 "VKL","VKC";letter-spacing:-.01em}#og p{margin:14px 0 6px;font:400 27px "VKL","VKC";color:var(--mute)}'
           '#og .vbars{--h:250px;padding-left:40px}' % CARD_WH)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        pg = b.new_page(viewport=dict(zip(("width", "height"), CARD_WH)), device_scale_factor=1)
        pg.goto(index.resolve().as_uri())
        pg.evaluate("""([big, small, site]) => {
          const c = document.createElement('div'), h = document.createElement('header'), p = document.createElement('p');
          h.append(Object.assign(document.createElement('h1'), {textContent: big}), Object.assign(document.createElement('b'), {textContent: site}));
          p.textContent = small; c.id = 'og';
          c.append(h, p, document.querySelector('#overview .pane.on .vbars'));
          document.body.replaceChildren(c);
        }""", [big, small, SITE])
        pg.add_style_tag(content=css)
        nums, out_of = pg.evaluate("""async () => {await document.fonts.ready; return [
          [...document.querySelectorAll('#og .vv')].map(e => e.textContent),
          [...document.querySelectorAll('#og header, #og p, #og .vb, #og .vn span')].filter(e => {const r = e.getBoundingClientRect();
            return r.left < 0 || r.top < 0 || r.right > innerWidth || r.bottom > innerHeight}).map(e => e.textContent)]}""")
        if nums != want or out_of:
            raise SystemExit(f"卡片图不对：图上的分 {nums}，应为 {want}；出界 {out_of}")
        pg.screenshot(path=str(out))
        b.close()


def init_labels(doc, path):
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    new = {}
    for key, what in label_spec(doc).items():
        new[key] = {**old.get(key, {}), "what": what, "text": (old.get(key) or {}).get("text", "")}
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"labels.json：{len(new)} 项，{sum(1 for x in new.values() if x['text'])} 项已填")


def visible_ok(name, html_, v, roles, labels, body=(), show=True):
    extra, bad, attrs = check_visible(html_, v["models"], roles, labels, body)
    counts = {}
    for s in extra:
        counts[s] = counts.get(s, 0) + 1
    print(f"{name} 上除模型名、数字和评价正文以外的可见文字：" + ("" if show else f"{len(counts)} 种（同上）"))
    for s, n in counts.items() if show else ():
        print(f"  {s}" + (f"  ×{n}" if n > 1 else ""))
    if attrs:
        bad.append(f"属性里有字：{attrs[:5]}")
    if re.search(r"(?:src|href)=\"https?:", html_) or "url(http" in html_:
        bad.append("有外链")
    if bad:
        print("不允许的字：", *bad, sep="\n  ")
        sys.exit(1)
    g = TextGrab()
    g.feed(html_)
    return html_.replace("/*{{FONT}}*/", font_faces(" ".join(g.out))) if "/*{{FONT}}*/" in html_ else html_


def privacy_ok(pages, forbid):
    import privacy
    words, skipped = privacy.lists()
    hits = [h for name, text in pages.items() for h in privacy.scan_text(name, text, words, forbid)]
    print(f"隐私检查：额外名单 {sum(map(len, words.values()))} 个；查了 {', '.join(pages)}，命中 {len(hits)} 处",
          *(f"  {name} [{kind}] …{ctx}…" for name, kind, ctx in hits), sep="\n")
    if hits:
        print("隐私检查没过，什么都没写")
        sys.exit(1)


def lang_pages(doc, labels, rj, rv, pre=""):
    """一种语言的全部页面 -> (v, L, {发布路径: html}, 详情页, 岗位页, fonts.css, 卡片图上的字)；pre 是英文版的 en/"""
    import detail
    import reviews
    import role
    role.rename(doc, rj)  # 岗位名换成 roles.json 的 name，要在 build_view 之前
    L = Labels(labels, en=bool(pre))
    v = build_view(doc, set(rv), name_marks(labels))
    L.used |= {k for k, x in labels.items() if x.get("mark")}  # 记号说明：考生不在页面上也算用到
    v["zl"] = {z: L(f"zero.{z}") for z in ZERO_REASONS}  # 清零原因的短标签（没出现的原因也算用到）
    scores, more = render(v, L)
    more = more.replace("/*{{FONT}}*/", "@import url(m/fonts.css);", 1)  # 更多指标页和详情页、岗位页共用字体文件
    # 详情页 m/<id>.html：所有页共用 m/fonts.css；reviews.html 改成跳到详情页的跳转页
    dpages = detail.render(v, doc, rv, L, (HERE / "review.css").read_text(encoding="utf-8"))
    rpages = role.render(v, doc, rj, L, (HERE / "role.css").read_text(encoding="utf-8"))
    empty = sorted(k for k, x in labels.items() if not x.get("text"))
    if empty:
        print("labels.json 里 text 还空着（页面照出，等填）：", ", ".join(empty))
    roles = [r["label"] for r in doc["roles"]]
    pages = {PUB["scores"]: visible_ok("成绩页", scores, v, roles, labels),
             PUB["reviews"]: visible_ok("跳转页", reviews.redirect(dpages, PUB["scores"]), v, roles, labels),
             PUB["more"]: visible_ok("更多指标页", more, v, roles, labels, [t["title"] for j in rj.values() for t in j["tasks"].values()])}
    dbody = list(reviews.body_strings(rv)) + detail.allowed(doc, dpages, L.en)
    for n, (i, h) in enumerate(dpages.items()):
        pages[f"m/{i}.html"] = visible_ok(f"详情页 {i}", h, v, roles, labels, dbody + list(role.strings(rj)), show=n == 0)
    for n, (i, h) in enumerate(rpages.items()):
        pages[f"r/{i}.html"] = visible_ok(f"岗位页 {i}", h, v, roles, labels, list(role.strings(rj)), show=n == 0)
    rids = {r["id"]: r["label"] for r in doc["roles"]}
    pages = {pre + k: h.replace('<meta charset="utf-8">', '<meta charset="utf-8">' + card_meta(k, L, v, rids, pre), 1)
             .replace("{{ALT}}", "../" * k.count("/") + ("../" if pre else "en/") + k) for k, h in pages.items()}
    card_words = L.text("card.img.big") + " " + L.text("card.img.small")  # 卡片图上的字，也过隐私检查
    unused = set(labels) - L.used
    if unused:
        print("labels.json 里有页面没用到的项：", ", ".join(sorted(unused)))
        if not os.environ.get("VIDEOBENCH_ALLOW_UNUSED_LABELS"):  # 样题页面没有探针和评语，允许
            sys.exit(1)
    g = TextGrab()
    g.feed("".join(dpages.values()) + "".join(rpages.values()) + more)
    fonts = font_faces(" ".join(g.out))
    return v, L, pages, dpages, rpages, fonts, card_words


DATA = re.compile(r'(data-k|data-bar|data-ok|data-t)="([^"]*)"([^>]*)>([^<]*)')
CJK = re.compile(r"[⺀-鿿　-〿＀-￯]")


def en_labels(zh):
    """英文 labels = 中文那份只换 text（mark、what 不动）；缺哪项就列出来失败"""
    en = json.loads((HERE / "labels_en.json").read_text(encoding="utf-8"))
    miss = sorted(k for k in zh if not (en.get(k) or {}).get("text"))
    if miss:
        raise SystemExit("labels_en.json 缺英文：" + ", ".join(miss))
    return {k: {**x, "text": en[k]["text"]} for k, x in zh.items()}


def en_gaps(zh, en):
    """中文有字而英文空着，或条数不同"""
    zh, en = list(zh), list(en)
    return len(zh) != len(en) or any(a.strip() and not b.strip() for a, b in zip(zh, en))


def no_cjk(pages):
    """英文页原始文件去掉 style、script、注释、base64 后（title、meta、隐藏的浮层和提示窗都算），不许有中文字和全角标点，lang 是 en"""
    bad = []
    for name, h in pages.items():
        t = re.sub(r"<style.*?</style>|<script.*?</script>|<!--.*?-->|base64,[A-Za-z0-9+/=]+", "", h, flags=re.S)
        bad += [f"{name}: …{t[max(0, m.start() - 20):m.end() + 20]}…" for m in CJK.finditer(t)][:3]
        bad += [] if name.endswith(".png") or '<html lang="en">' in h else [f"{name}: lang 不是 en"]
    print(f"英文页无中文检查：查了 {len(pages)} 项，命中 {len(bad)} 处", *bad[:30], sep="\n  ")
    if bad:
        sys.exit(1)


def same_data(zh, en):
    """英文页上的数、条宽、合格勾、速度总表的格子和中文页逐项一样（评语正文 t|… 除外）"""
    n, bad = 0, sorted(set(en) - {"en/" + k for k in zh})
    for k, h in zh.items():
        a = sorted(x for x in DATA.findall(h) if not x[1].startswith("t|"))
        n += len(a)
        bad += [] if a == sorted(x for x in DATA.findall(en.get("en/" + k, "")) if not x[1].startswith("t|")) else [k]
    print(f"英文页和中文页数据对比：{len(zh)} 页 {n} 处，不一致 {len(bad)} 页", *bad[:10], sep="\n  ")
    if bad:
        sys.exit(1)


def main():
    import detail
    import reviews
    import role
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "data" / "summary.json"))
    ap.add_argument("--labels", default=str(HERE / "labels.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "local" / "index.html"))
    ap.add_argument("--rv-out", default=str(ROOT / "out" / "local" / "reviews.html"))
    ap.add_argument("--pkg", default=str(PKG))
    ap.add_argument("--init-labels", action="store_true")
    ap.add_argument("--no-card", action="store_true", help="skip card.png (needs playwright)")
    a = ap.parse_args()
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    drop_unfinished(doc)
    lpath = Path(a.labels)
    if a.init_labels:
        init_labels(doc, lpath)
        return
    labels = json.loads(lpath.read_text(encoding="utf-8"))
    rv = reviews.load({m["id"] for m in doc["models"] if m.get("kind") == "examinee"})
    doc_en, rj, rj_en = copy.deepcopy(doc), role.load(), role.load(HERE / "roles_en.json")
    v, L, pages, dpages, rpages, fonts, card_words = lang_pages(doc, labels, rj, rv)
    # 英文版 en/：文字换 labels_en、roles_en、评语_en，别的同一套；缺译直接失败，不拿中文顶
    en_l = en_labels(labels)
    rv_en = reviews.load(set(rv), ROOT / "data" / "reviews" / "en")
    if set(rv_en) != set(rv):
        raise SystemExit(f"评价/评语_en 缺：{sorted(set(rv) - set(rv_en))}")
    gaps = [f"评语_en/{i}" for i in rv if en_gaps(reviews.body_strings({i: rv[i]}), reviews.body_strings({i: rv_en[i]}))]
    gaps += ["roles_en.json"] if en_gaps(role.strings(rj), role.strings(rj_en)) else []
    if gaps:
        raise SystemExit("英文缺段或条数和中文不同：" + ", ".join(gaps))
    names = {j["name"]: k for k, j in rj_en.items()}
    for i, j in rv_en.items():  # roles[].role 只用来对岗位：编号开头的照用，译成岗位名的按 roles_en 的 name 换回编号
        for r in j["roles"]:
            r["role"] = r["role"].split()[0] if r["role"].split()[0] in rj_en else names.get(r["role"], "")
        if sorted(r["role"] for r in j["roles"]) != sorted(z["role"].split()[0] for z in rv[i]["roles"]):
            raise SystemExit(f"评价/评语_en/{i}.json 的岗位和中文对不上：{[r['role'] for r in j['roles']]}")
    v_en, L_en, epages, _, _, efonts, ecard = lang_pages(doc_en, en_l, rj_en, rv_en, "en/")
    epages = {k: h.replace('<html lang="zh-CN">', '<html lang="en">', 1).replace(f'data-v="{first_ver(en_l)}"', f'data-v="{first_ver(labels)}"')
              for k, h in epages.items()}  # 提示窗版本跟中文走，切语言不再弹
    no_cjk({**epages, "en/" + CARD: ecard})
    same_data(pages, epages)
    logos = {f"logos/{f.name}": f.read_bytes().decode("utf-8", "ignore")  # 内嵌成 base64 前的原文
             for _, f in logo_files({m["vk"] for m in v["models"].values()})}
    privacy_ok({**pages, **epages, **logos, CARD: card_words, "en/" + CARD: ecard},
               list(reviews.operator_strings(rv)) + list(reviews.operator_strings(rv_en)))
    out, rv_out, pkg = Path(a.out), Path(a.rv_out), Path(a.pkg)
    out.parent.mkdir(parents=True, exist_ok=True)

    def local(name, h):  # out/local 里的 EN 指到 out/site 里的英文页
        return h.replace(f'class="lang" href="{"../" * name.count("/")}en/{name}"',
                         f'class="lang" href="{Path(os.path.relpath(pkg / "en" / name, (out.parent / name).parent)).as_posix()}"')
    out.write_text(local(PUB["scores"], pages[PUB["scores"]]), encoding="utf-8")
    rv_out.write_text(reviews.redirect(dpages, out.name), encoding="utf-8")
    (out.parent / PUB["more"]).write_text(local(PUB["more"], pages[PUB["more"]]).replace(f'href="{PUB["scores"]}"', f'href="{out.name}"'),
                                          encoding="utf-8")
    shutil.rmtree(pkg, ignore_errors=True)
    shutil.rmtree(out.parent / "m", ignore_errors=True)
    shutil.rmtree(out.parent / "r", ignore_errors=True)
    for d in (pkg / "m", out.parent / "m"):
        d.mkdir(parents=True)
        (d / "fonts.css").write_text(fonts, encoding="utf-8")
    for d in (pkg / "r", out.parent / "r"):
        d.mkdir(parents=True)
    for name, html_ in pages.items():
        (pkg / name).write_text(html_, encoding="utf-8")
        if name.startswith(("m/", "r/")):
            (out.parent / name).write_text(local(name, html_).replace(f'href="../{PUB["scores"]}"', f'href="../{out.name}"'),
                                           encoding="utf-8")
    a.no_card or card_png(pkg / PUB["scores"], pkg / CARD, L, [f1(m["q"]) for m in ranked(v["models"], "q", True)])
    for d in ("m", "r"):
        (pkg / "en" / d).mkdir(parents=True)
    (pkg / "en" / "m" / "fonts.css").write_text(efonts, encoding="utf-8")
    for name, html_ in epages.items():
        (pkg / name).write_text(html_, encoding="utf-8")
    a.no_card or card_png(pkg / "en" / PUB["scores"], pkg / "en" / CARD, L_en, [f1(m["q"]) for m in ranked(v_en["models"], "q", True)])
    assert sorted(f.name for f in pkg.iterdir()) == sorted([*PUB.values(), "m", "r", "en"] + ([] if a.no_card else [CARD]))
    print(f"写好 {out.name}（{len(pages[PUB['scores']]) // 1024} KB）、{rv_out.name}（跳转页）、"
          f"m/ 详情页 {len(dpages)} 个（每页约 {max(map(len, dpages.values()), default=0) // 1024} KB，共用 fonts.css "
          f"{len(fonts) // 1024} KB）、{pkg}；考生 {len(v['models'])}，评价 {len(rv)} 个，labels {len(labels)} 项，"
          f"已填 {sum(1 for x in labels.values() if x.get('text'))}；r/ 岗位页 {len(rpages)} 个")


if __name__ == "__main__":
    main()
