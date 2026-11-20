"""考生工作目录：建目录、渲染派活话术、收卷时取文件时间、把作答复制回 runs/。

工作目录在 bench 外面（默认是系统临时目录下的 vbwork/<批次>/<考生>/<题号>/，VIDEOBENCH_WORK 可改），路径里不带 bench 字样。
这里的函数只碰工作目录根和 bench 自己的 runs/，别处不写。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import common, leak

VBWORK = Path(os.environ.get("VIDEOBENCH_WORK") or Path(tempfile.gettempdir()) / "vbwork")
PROMPT_FILE = common.ROOT / "dispatch_prompt.txt"
PLACEHOLDER = "{workdir}"
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
# 路径里不许出现的字样（不分大小写）：考生看到路径就能猜出这是测评，或者能找到答案
BANNED_IN_PATH = ("bench", "grading", "answer", "selfcheck", "eval", "考")
# 题目文件统一改成这个修改时间，免得从文件时间看出出题的先后
FLAT_MTIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()


def check_slug(name: Any, what: str) -> str:
    """批次名、考生目录名只许字母、数字、下划线、连字符，最长 40，并且不带 bench 之类的字样。不合格抛 ValueError。"""
    if not isinstance(name, str) or not SLUG.match(name):
        raise ValueError(f"{what} 只能用字母、数字、下划线、连字符，以字母或数字开头，最长 40 个字符: {name!r}")
    low = name.casefold()
    for bad in BANNED_IN_PATH:
        if bad in low:
            raise ValueError(f"{what} 里不能出现「{bad}」: {name!r}")
    return name


def dir_slug(examinee_id: str) -> str:
    """考生 id（可以带 @ . 之类）变成能当目录名的写法。变不出合格名字时抛 ValueError。"""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(examinee_id)).strip("-_")[:40].strip("-_")
    return check_slug(s, "考生目录名")


def check_root(root: Path | str) -> Path:
    """工作目录的根必须是绝对路径、不在 bench 里面、路径里不带禁用字样。"""
    p = Path(root)
    s = common.norm_path(str(p))
    if not common.is_abs_path(s):
        raise ValueError(f"工作目录的根要写绝对路径: {root}")
    if common.is_inside(s, str(common.ROOT)) or common.is_inside(str(common.ROOT), s):
        raise ValueError(f"工作目录的根不能在 bench 里面，也不能是 bench 的上级目录: {root}")
    low = s.casefold()
    for bad in BANNED_IN_PATH:
        if bad in low:
            raise ValueError(f"工作目录的根里不能出现「{bad}」: {root}")
    if " " in s:
        raise ValueError(f"工作目录的根里不要带空格（污染检查从命令里认路径时会被空格切断）: {root}")
    # 根目录或它的上级是联接时，字面上在 bench 外面，实际可能指回 bench 里面
    real = common.norm_path(os.path.realpath(p))
    if common.is_inside(real, str(common.ROOT)) or common.is_inside(os.path.realpath(common.ROOT), real):
        raise ValueError(f"工作目录的根经过联接指到了 bench 里面（真实位置 {real}）: {root}")
    return p


def load_prompt_template(path: Path | None = None) -> str:
    """读统一的派活话术。里面必须正好有一处 {workdir}。"""
    text = Path(path or PROMPT_FILE).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip("\n")
    if text.count(PLACEHOLDER) != 1:
        raise ValueError(f"派活话术里要正好有一处 {PLACEHOLDER}，现在有 {text.count(PLACEHOLDER)} 处")
    return text


def render_prompt(template: str, workdir: Path | str | None) -> str:
    """把工作目录填进话术。路径统一写正斜杠。没有工作目录的探针直接返回话术原文。"""
    if workdir is None:
        return template
    return template.replace(PLACEHOLDER, common.norm_path(str(workdir)))


def prompt_addendum(card: dict, key: str, attempt: int) -> str | None:
    """名片 approved_prompt_addenda 里批准给这一次尝试的题面加注。
    每条 {text, since, why, attempts: {键: 尝试号}}；这道题这次尝试没被列上就返回 None。"""
    return next((a["text"] for a in (card or {}).get("approved_prompt_addenda") or []
                 if isinstance(a, dict) and a.get("text") and (a.get("attempts") or {}).get(key) == int(attempt or 1)),
                None)


def tag_for(batch: str, examinee: str, key: str, attempt: int = 1) -> str:
    """每次派活一个 8 位的记号，放在线程标题里，收卷时靠它找线程。

    同一道题重派时尝试号加一，记号跟着变，新旧线程不会混。第 1 次的记号和没有尝试号时一样。"""
    seed = f"{batch}|{examinee}|{key}" + (f"|a{int(attempt)}" if int(attempt) > 1 else "")
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


def copy_files(src: Path, dst: Path, skip: tuple | set | list = ()) -> list[str]:
    """把 src 下的普通文件复制到 dst（保持相对路径）。链接和目录联接不跟、不复制。返回复制了的相对路径。

    skip 里的相对路径不复制。dst 已经存在并且不是空目录时抛 FileExistsError，不覆盖。"""
    src, dst = Path(src), Path(dst)
    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError(f"目标目录已经有东西了，不覆盖: {dst}")
    files, _links = common.walk_files(src)
    drop = {common.path_key(s) for s in skip}
    files = [f for f in files if common.path_key(f) not in drop]
    dst.mkdir(parents=True, exist_ok=True)
    for rel in files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, target)
    return files


def fingerprint(root: Path, files: list[str] | None = None) -> dict[str, str]:
    """目录里每个普通文件的 sha256：{相对路径: 哈希}。读不了的文件记成 "unreadable"。"""
    root = Path(root)
    if files is None:
        files = common.walk_files(root)[0]
    out = {}
    for rel in files:
        try:
            out[rel] = common.sha256_file(root / rel)
        except OSError:
            out[rel] = "unreadable"
    return out


def link_targets(root: Path) -> dict[str, str]:
    """目录里的符号链接和目录联接：{联接的绝对路径: 它真正指向的位置}。给污染检查用。"""
    root = Path(root)
    out = {}
    for rel in common.walk_files(root)[1]:
        p = root / rel
        out[common.norm_path(str(p))] = common.norm_path(os.path.realpath(p))
    return out


def late_files(work: Path, manifest: dict, end_epoch: float | None, slack_s: float = 2.0) -> list[str]:
    """交回之后才写出来或改过的文件（修改时间比交回时刻晚 slack_s 秒以上，并且内容和发下去的不一样）。

    这些文件收卷时不算进作答。交回时刻不知道时返回空表。"""
    if end_epoch is None:
        return []
    work = Path(work)
    out = []
    for rel in common.walk_files(work)[0]:
        try:
            mtime = (work / rel).stat().st_mtime
        except OSError:
            continue
        if mtime <= end_epoch + slack_s:
            continue
        try:
            same = rel in manifest and common.sha256_file(work / rel) == manifest[rel]
        except OSError:
            same = False
        if not same:
            out.append(rel)
    return out


def flatten_times(root: Path, ts: float = FLAT_MTIME) -> None:
    """把目录里所有文件和子目录的修改时间改成同一个值。"""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            try:
                os.utime(os.path.join(dirpath, name), (ts, ts))
            except OSError:
                pass
    try:
        os.utime(root, (ts, ts))
    except OSError:
        pass


def build_task_workdir(role: str, tid: str, dest: Path, root: Path = common.ROOT) -> list[str]:
    """把一道题的考生材料复制成工作目录，抹平文件时间，再做一遍泄漏检查。返回泄漏检查发现的问题。"""
    meta = common.load_meta(role, tid, root)
    copy_files(root / "tasks" / role / tid, dest)
    flatten_times(dest)
    return leak.check_directory(dest, list(meta.get("leak_tokens") or []), root / "grading" / role / tid)


def _stat_times(path: Path) -> tuple[float | None, float | None]:
    """(创建时间, 修改时间)，秒。取不到的是 None。Windows 上创建时间取 st_birthtime，没有就用 st_ctime。"""
    try:
        st = path.stat()
    except OSError:
        return None, None
    born = getattr(st, "st_birthtime", None)
    if born is None and os.name == "nt":
        born = st.st_ctime
    return born, st.st_mtime


def deliverable_times(work: Path, meta: dict, manifest: dict, skip: tuple | set | list = ()) -> dict | None:
    """必交文件最早是什么时候写出来的、最后一次是什么时候改的。要在把目录复制走之前取。

    新建的文件用创建时间；题目里本来就有、被考生改过的文件用修改时间（没改过的不算）。
    skip 里的文件（交回之后才改的）不算。一个必交文件都没有时返回 None。"""
    first, last = None, None
    drop = {common.path_key(s) for s in skip}
    for rel in meta.get("required_deliverables") or []:
        if not isinstance(rel, str) or common.path_key(rel) in drop:
            continue
        born, mtime = _stat_times(Path(work) / rel)
        if mtime is None:
            continue
        if rel in manifest:
            if abs(mtime - FLAT_MTIME) < 1.0:
                continue  # 没动过
            created = mtime
        else:
            created = born if born is not None else mtime
        first = created if first is None else min(first, created)
        last = mtime if last is None else max(last, mtime)
    if first is None:
        return None

    def iso(t: float) -> str:
        return common.beijing_iso(datetime.fromtimestamp(t, timezone.utc))

    return {"first_created": iso(first), "last_modified": iso(last)}


def _is_link(path: Path | str) -> bool:
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda _p: False)(path)


def remove_tree(path: Path, must_be_under: Path) -> None:
    """删一个目录。只删 must_be_under 下面的，别处拒绝。

    字面路径和解析掉联接以后的真实路径都要在 must_be_under 下面：路径中间经过一个指到外面的目录联接时，
    字面上在范围里，真正删的却是外面的东西，这种一律拒绝。要删的本身是联接时，只摘掉联接，不碰它指向的内容。"""
    p, base = common.norm_path(str(path)), common.norm_path(str(must_be_under))
    if not common.is_inside(p, base) or common.path_key(p) == common.path_key(base):
        raise ValueError(f"拒绝删除 {path}：不在 {must_be_under} 下面")
    if _is_link(path):
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)
        return
    if not Path(path).exists():
        return
    real_p = common.norm_path(os.path.realpath(path))
    real_base = common.norm_path(os.path.realpath(must_be_under))
    if not common.is_inside(real_p, real_base) or common.path_key(real_p) == common.path_key(real_base):
        raise ValueError(f"拒绝删除 {path}：它经过目录联接，真实位置 {real_p} 不在 {must_be_under} 下面")

    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onerror=onerror)
