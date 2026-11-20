"""SAMPLE 出题脚本：底座放的两道最小样题，用来证明整条链路通，也给出题的人当写法参考。

全是合成材料，不进正式题单。用法：python -B generators/SAMPLE.py [--out-root 目录]

SAMPLE1 对错题：几张图上各有一个数字，登记成表。带一个陷阱（点名的文件不存在，旁边有名字很像的旧文件）、
        两条要求的声明、看图证据。
SAMPLE2 审美二选一题（开卷）：二十组字幕断行，每组两种写法，选符合标准的那个。带三种傻瓜作答。
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench import common  # noqa: E402

ROLE = "SAMPLE"
SEED_1 = 4101
SEED_2 = 4102
MAX_LINE = 12  # SAMPLE2 的标准：一行不超过 12 个字


# ---------------------------------------------------------------- SAMPLE1
def _draw_number(path: Path, number: int, bg: tuple[int, int, int]) -> None:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (480, 270), bg)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=150)
    text = str(number)
    box = draw.textbbox((0, 0), text, font=font)
    pos = ((480 - (box[2] - box[0])) // 2 - box[0], (270 - (box[3] - box[1])) // 2 - box[1])
    draw.text(pos, text, fill=(255, 255, 255), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=False)


def build_sample1(out_root: Path) -> None:
    tid = "SAMPLE1"
    task = common.task_dir(out_root, ROLE, tid)
    key = common.key_dir(out_root, ROLE, tid)
    rng = random.Random(SEED_1)
    numbers = rng.sample(range(11, 99), 5)
    colors = [(40, 70, 120), (120, 50, 50), (40, 110, 70), (100, 80, 30), (80, 50, 110)]
    frames = []
    for i in range(4):
        name = f"frame_{i + 1:02d}.png"
        _draw_number(task / "input" / "frames" / name, numbers[i], colors[i])
        frames.append({"file": f"input/frames/{name}", "number": numbers[i], "status": "ok"})
    # 陷阱：简报点名 frame_05.png，目录里只有 frame_05_old.png
    _draw_number(task / "input" / "frames" / "frame_05_old.png", numbers[4], colors[4])
    frames.append({"file": "input/frames/frame_05.png", "number": None, "status": "missing"})

    brief = f"""# 把五张图上的数字登记成表

这批图是从一段素材里截出来的，每张图正中有一个两位数。帮我登记一下，后面对素材要用。

## 要做什么

依次打开这五张图，把图上的数字抄下来：

- `input/frames/frame_01.png`
- `input/frames/frame_02.png`
- `input/frames/frame_03.png`
- `input/frames/frame_04.png`
- `input/frames/frame_05.png`

## 交付物

写到 `out/table.json`，格式如下，五张图都要有一行，顺序和上面一致：

```json
{{
  "frames": [
    {{"file": "input/frames/frame_01.png", "number": 42, "status": "ok"}},
    {{"file": "input/frames/frame_02.png", "number": null, "status": "missing"}}
  ]
}}
```

- `number`：图上的数字，整数。
- `status`：`ok` 表示读到了；`missing` 表示这个文件不存在；`unsure` 表示文件在但看不清。后两种情况 `number` 写 `null`。

## 规矩

{common.brief_rules(extra=["只登记我点名的这五个文件。点名的文件不存在就照实标 `missing`，不要拿别的文件顶替。",
                           "`number` 只能是整数或 `null`。"])}
{common.brief_report_section(
    tid,
    claims_example={"frames_read": 4, "missing_count": 1},
    claims_doc="`frames_read` 写你实际读到数字的图有几张，`missing_count` 写不存在的文件有几个。",
    frames_doc="这次每张看过的图都要列，`source` 写 `null`，`t` 写 `null`。")}"""
    common.write_text(task / "brief.md", brief)

    common.write_json(key / "answer.json", {"frames": frames, "lookalike": "input/frames/frame_05_old.png",
                                            "lookalike_number": numbers[4]})
    common.write_json(key / "meta.json", {
        "id": tid, "role": ROLE, "title": "样题一：把图上的数字登记成表",
        "short_title": "数字登记成表",
        "scenario": "五张图各有一个数字，登记成表。",
        "difficulty": "easy", "needs": ["text", "vision"], "dimension": "accuracy",
        "quality_spec": {
            "formula": "两个分项。numbers：四张存在的图，数字抄对并且 status 是 ok 的张数除以 4。"
                       "missing_flag：不存在的那张标成 missing 并且 number 是 null 得 1，否则得 0。",
            "parts": [{"id": "numbers", "label": "数字抄对率", "weight": 0.8},
                      {"id": "missing_flag", "label": "不存在的文件照实标出", "weight": 0.2}],
            "params": {"existing_frames": 4},
        },
        "q_pass": 1.0,
        "grading_method": "逐张对数字；不存在的那张必须标 missing。",
        "traps": [{"type": "lookalike_file", "desc": "点名的图不存在，旁边有个旧文件。"}],
        "required_claims": [{"id": "frames_read", "desc": "实际读到数字的图的张数"},
                            {"id": "missing_count", "desc": "不存在的文件个数"}],
        "constraints": [{"id": "number_type", "desc": "number 只能是整数或 null", "hard": False}],
        "open_book": None, "item_count": 5,
        "brief_excerpt": "五张图的数字登记成表。缺的图标 missing。",
        "answer_excerpt": "前四张是 " + "、".join(str(n) for n in numbers[:4]) + "。第五张标 missing。",
        "allowed_writes": ["out/**"], "required_deliverables": ["out/table.json"],
        "uses_real_material": False, "sources": [], "leak_tokens": [],
        "material_date": None, "generator": f"generators/SAMPLE.py seed={SEED_1}",
        "notes": "合成材料。数字是随机的，文件名和数字无关，所以不放 leak_tokens。",
    })


# ---------------------------------------------------------------- SAMPLE2
# 每组一句台词。good 是符合标准的断行，bad_kind 说明另一种写法坏在哪。
SENTENCES = [
    "他推开门的时候屋里已经没有人了", "这一拳打出去就再也收不回来", "师父说过功夫两个字一横一竖",
    "那天晚上的雨下得比哪一年都大", "你以为你赢了其实你早就输了", "桌上的茶还是热的人却不见了",
    "我在这里等了你整整三年零两个月", "这条街很多年以前不是这个样子的", "他把那封信折好放进了上衣口袋",
    "天亮以前我们必须离开这座城", "没有一个人知道她后来去了哪里", "屋里的灯灭了以后他才敢哭出来",
    "这把刀跟了我二十年从来没钝过", "火车开动的时候她始终没有回头", "老板把账本合上长长叹了一口气",
    "他们两个在码头上站到了后半夜", "第二天的报纸上登出了他的名字", "孩子仰着头问爸爸什么时候回来",
    "那扇窗户从那以后再也没有打开", "屋里所有人都在等他先开口说话",
]
# 每句都要比一行的上限长（整句一行才算不合格），又能断成合格的两行。
assert all(MAX_LINE < len(s) <= 2 * MAX_LINE - 3 for s in SENTENCES)
assert len(SENTENCES) % 4 == 0


def _split_good(text: str, rng: random.Random) -> list[str]:
    """符合标准的断行：两行，每行不超过 MAX_LINE 个字。"""
    lo = max(len(text) - MAX_LINE, 3)
    hi = min(MAX_LINE, len(text) - 3)
    cut = rng.randint(lo, hi)
    return [text[:cut], text[cut:]]


def _split_bad(text: str, kind: str) -> list[str]:
    """不符合标准的断行。too_long：整句一行（超过 12 个字）；too_many：断成三行以上的碎行。"""
    if kind == "too_long":
        return [text]
    step = 4
    return [text[i:i + step] for i in range(0, len(text), step)]


def build_sample2(out_root: Path) -> None:
    tid = "SAMPLE2"
    task = common.task_dir(out_root, ROLE, tid)
    key = common.key_dir(out_root, ROLE, tid)
    rng = random.Random(SEED_2)
    n = len(SENTENCES)
    # 配平：正确答案 A、B 各一半；坏写法「更短」（整句一行）和「改动更大」（切碎）各一半，并和 A、B 交叉。
    plan = [(side, kind) for side in ("A", "B") for kind in ("too_long", "too_many")] * (n // 4)
    rng.shuffle(plan)
    groups, answers = [], []
    for i, text in enumerate(SENTENCES):
        gid = f"g{i + 1:02d}"
        correct_side, bad_kind = plan[i]
        good, bad = _split_good(text, rng), _split_bad(text, bad_kind)
        a, b = (good, bad) if correct_side == "A" else (bad, good)
        groups.append({"id": gid, "A": a, "B": b})
        # 「更短」指行数更少；「改动更大」指和整句一行相比断点更多。
        fewer_lines = "A" if len(a) < len(b) else "B"
        more_cuts = "A" if len(a) > len(b) else "B"
        answers.append({"id": gid, "pick": correct_side, "shorter": fewer_lines, "bigger_change": more_cuts,
                        "bad_kind": bad_kind, "open_book": True, "decided_on": "2026-11-11",
                        "source_note": "样题：合成的断行标准"})
    common.write_json(task / "input" / "groups.json", {"groups": groups})
    common.write_text(task / "refs" / "字幕断行标准.md", f"""# 字幕断行标准（节选）

1. 一行不超过 {MAX_LINE} 个字。
2. 一句话最多断成两行。
3. 两条都满足才算合格。
""")
    brief = f"""# 二十组字幕断行，每组挑合格的那个

同一句台词有两种断行写法，A 和 B。按 `refs/字幕断行标准.md` 挑出合格的那个。

## 输入

`input/groups.json`：二十组，每组有 `id`、`A`、`B`。`A` 和 `B` 都是字符串列表，一个元素是一行字幕。

## 交付物

写到 `out/choices.json`，二十组都要有：

```json
{{
  "choices": [
    {{"id": "g01", "pick": "A", "reason": "一句话说明"}}
  ]
}}
```

- `pick` 只能是 `A` 或 `B`。
- `reason` 一句话，随便写多长都行，不影响结果。

## 规矩

{common.brief_rules(extra=["每组必须二选一，不许两个都选或都不选。"])}
{common.brief_report_section(
    tid,
    claims_example={"group_count": 20, "pick_a_count": 10},
    claims_doc="`group_count` 写你交了多少组，`pick_a_count` 写其中选 A 的有多少组。")}"""
    common.write_text(task / "brief.md", brief)
    common.write_json(key / "answer.json", {"groups": answers})
    balance = {
        "correct_is_A": sum(1 for x in answers if x["pick"] == "A"),
        "correct_is_shorter": sum(1 for x in answers if x["pick"] == x["shorter"]),
        "correct_is_bigger_change": sum(1 for x in answers if x["pick"] == x["bigger_change"]),
    }
    common.write_json(key / "meta.json", {
        "id": tid, "role": ROLE, "title": "样题二：字幕断行二选一（开卷）",
        "short_title": "字幕断行二选一",
        "scenario": "两种字幕断行，挑合格的那个。",
        "difficulty": "medium", "needs": ["text"], "dimension": "taste",
        "quality_spec": {
            "formula": "一个分项。agreement：选对的组数除以总组数，再按二选一校正："
                       "（正确率 − 0.5）÷ 0.5，低于 0 记 0。没交、pick 不是 A 或 B 的组算错。",
            "parts": [{"id": "agreement", "label": "和标准一致的程度（已校正乱选）", "weight": 1.0}],
            "params": {"chance": 0.5, "groups": n},
        },
        "q_pass": 0.9,
        "grading_method": "逐组对答案，按二选一校正。",
        "traps": [],
        "required_claims": [{"id": "group_count", "desc": "交了多少组"},
                            {"id": "pick_a_count", "desc": "选 A 的有多少组"}],
        "constraints": [{"id": "pick_valid", "desc": "每组的 pick 只能是 A 或 B", "hard": False}],
        "open_book": True, "item_count": n,
        "brief_excerpt": "二十组断行，按标准挑合格的。",
        "answer_excerpt": f"合格的那个：A {balance['correct_is_A']} 组，B {n - balance['correct_is_A']} 组。",
        "allowed_writes": ["out/**"], "required_deliverables": ["out/choices.json"],
        "uses_real_material": False, "sources": [], "leak_tokens": [],
        "material_date": "2026-11-11", "generator": f"generators/SAMPLE.py seed={SEED_2}",
        "notes": f"合成材料。配平：正确答案是 A 的 {balance['correct_is_A']} 组；"
                 f"是行数更少那个的 {balance['correct_is_shorter']} 组；"
                 f"是断点更多那个的 {balance['correct_is_bigger_change']} 组（共 {n} 组）。",
    })


def build(out_root: Path) -> None:
    build_sample1(out_root)
    build_sample2(out_root)


if __name__ == "__main__":
    sys.exit(common.generator_main(ROLE, build))
