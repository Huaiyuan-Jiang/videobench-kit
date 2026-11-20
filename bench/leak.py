"""泄漏检查：考生能看到的目录里有没有答案、有没有能反查到原工程或这个 bench 的东西。

对 tasks/<岗位>/<题号>/ 和建好的考生目录都能用。只读。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import common

# 考生材料里不该出现的固定词。bench 根目录的几种写法在 fixed_tokens() 里加。
FIXED_TOKENS = ("VideoBench", "DelegateBench", "ModelBench", "grading/", "grading\\", "grader.py",
                "make_reference", "make_wrong", "selfcheck", "leak_tokens")
# 文件名里的版本、期数记号
VERSION_MARK = re.compile(
    r"(?i)(?:^|[_\-. (（])v\d+(?:[_\-. )）]|$)|定稿|终版|最终版|修改版|改后|final|第[一二三四五六七八九十\d]+期")
MEDIA_FORMAT_TAGS = frozenset({"title", "comment", "description", "artist", "album", "album_artist", "date",
                               "creation_time", "copyright", "synopsis", "show", "episode_id", "genre",
                               "composer", "publisher", "encoded_by", "location"})
MEDIA_STREAM_TAGS = frozenset({"title", "comment", "creation_time", "description"})
MAX_SCAN_BYTES = 256 * 1024 * 1024


def fixed_tokens() -> list[str]:
    root = str(common.ROOT)
    variants = {root, root.replace("\\", "/"), root.replace("/", "\\"), root.replace("\\", "\\\\")}
    return list(FIXED_TOKENS) + sorted(variants)


def _needles(token: str) -> list[bytes]:
    """一个词在文件里可能的字节写法：UTF-8、UTF-16LE、JSON 转义后的写法，各带小写版本。"""
    forms = {token, token.lower(), json.dumps(token, ensure_ascii=True)[1:-1],
             json.dumps(token.lower(), ensure_ascii=True)[1:-1]}
    out = set()
    for f in forms:
        out.add(f.encode("utf-8"))
        out.add(f.encode("utf-16-le"))
    return [n for n in out if len(n) >= 2]


def scan_tokens(directory: Path, tokens: list[str]) -> list[dict]:
    """在目录的文件名和文件内容里搜这些词。返回命中列表 [{token, path, where}]。"""
    tokens = [t for t in dict.fromkeys(tokens) if isinstance(t, str) and len(t.strip()) >= 2]
    hits = []
    # 缓存目录、系统文件也是考生看得见的文件：__pycache__ 里放一份答案照样是泄题，所以这里一个都不跳
    files, links = common.walk_files(directory, include_ignored=True)
    needles = {t: _needles(t) for t in tokens}
    for rel in files + links:
        low = rel.casefold()
        for t in tokens:
            if t.casefold().replace("\\", "/") in low:
                hits.append({"token": t, "path": rel, "where": "文件名"})
    for rel in files:
        p = Path(directory) / rel
        try:
            if p.stat().st_size > MAX_SCAN_BYTES:
                hits.append({"token": "", "path": rel, "where": "文件太大，没有扫描内容"})
                continue
            data = p.read_bytes()
        except OSError as e:
            hits.append({"token": "", "path": rel, "where": f"读不了: {e}"})
            continue
        lowered = data.lower()
        for t in tokens:
            if any(n in data or n in lowered for n in needles[t]):
                hits.append({"token": t, "path": rel, "where": "文件内容"})
    return hits


def identical_files(task_dir: Path, key_dir: Path, min_size: int = 32) -> list[dict]:
    """考生材料里有没有和答案目录里一模一样的文件（太小的文件不比）。"""
    key_hashes = {}
    for rel in common.list_files(key_dir):
        p = Path(key_dir) / rel
        if rel != "manifest.json" and p.stat().st_size >= min_size:
            key_hashes[common.sha256_file(p)] = rel
    out = []
    for rel in common.list_files(task_dir):
        p = Path(task_dir) / rel
        if p.stat().st_size >= min_size:
            h = common.sha256_file(p)
            if h in key_hashes:
                out.append({"task_file": rel, "key_file": key_hashes[h]})
    return out


def version_marks(directory: Path) -> list[str]:
    files, links = common.walk_files(directory, include_ignored=True)
    return sorted(rel for rel in files + links if VERSION_MARK.search(rel))


def image_metadata(path: Path) -> list[str]:
    """图片里的元数据。干净的图返回 []。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            found = []
            if len(im.getexif()) > 0:
                found.append("EXIF")
            for k in im.info:
                if str(k).lower() in ("exif", "comment", "software", "description", "xml:com.adobe.xmp", "xmp",
                                      "author", "title", "copyright", "creation time", "date:create",
                                      "date:modify", "photoshop"):
                    found.append(str(k))
            text = getattr(im, "text", None)
            if isinstance(text, dict) and text:
                found += [f"PNG 文本块 {k}" for k in sorted(text)]
            return sorted(set(found))
    except Exception as e:  # noqa: BLE001
        return [f"打不开: {e}"]


def media_metadata(path: Path) -> list[str]:
    """音视频容器里的标题、注释、创建时间等标签。干净的返回 []。ffprobe 不在时返回提示。"""
    ffprobe = common.ffmpeg_bin("ffprobe")
    if not ffprobe:
        return ["找不到 ffprobe，没有检查容器元数据"]
    r = common.run_cmd([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], timeout=60)
    if r["code"] != 0:
        return [f"ffprobe 读不了: {r['stderr'].strip()[:200]}"]
    try:
        info = json.loads(r["stdout"])
    except ValueError:
        return ["ffprobe 输出解析不了"]
    found = []
    for k in ((info.get("format") or {}).get("tags") or {}):
        if k.lower() in MEDIA_FORMAT_TAGS:
            found.append(f"容器标签 {k}")
    for s in info.get("streams") or []:
        for k in (s.get("tags") or {}):
            if k.lower() in MEDIA_STREAM_TAGS:
                found.append(f"流 {s.get('index')} 标签 {k}")
    return sorted(set(found))


def check_directory(directory: Path, tokens: list[str], key_dir: Path | None = None,
                    check_media: bool = True) -> list[str]:
    """对一个考生可见的目录做全套泄漏检查，返回问题列表（空表示干净）。"""
    directory = Path(directory)
    problems = []
    for h in scan_tokens(directory, fixed_tokens() + list(tokens)):
        if h["token"]:
            problems.append(f"{h['where']}里出现「{h['token']}」: {h['path']}")
        else:
            problems.append(f"{h['path']}: {h['where']}")
    if key_dir is not None:
        for d in identical_files(directory, key_dir):
            problems.append(f"考生材料 {d['task_file']} 和答案目录里的 {d['key_file']} 一模一样")
    for rel in version_marks(directory):
        problems.append(f"文件名带版本或期数记号: {rel}")
    files, links = common.walk_files(directory, include_ignored=True)
    for rel in links:
        problems.append(f"目录里有链接: {rel}")
    for rel in files:
        if common._ignored(rel):
            problems.append(f"考生材料里不该有缓存或系统文件（清单和改动检查都看不见它）: {rel}")
        if rel in ("meta.json", "manifest.json") or rel.endswith(("/meta.json", "/manifest.json")):
            problems.append(f"考生材料里不该有 {rel}")
        low = rel.lower()
        if not check_media:
            continue
        if low.endswith(common.IMAGE_SUFFIX):
            for x in image_metadata(directory / rel):
                problems.append(f"{rel}: 图片带元数据（{x}）")
        elif low.endswith(common.VIDEO_SUFFIX + common.AUDIO_SUFFIX):
            for x in media_metadata(directory / rel):
                problems.append(f"{rel}: {x}")
    return problems


def check_task(role: str, tid: str, root: Path = common.ROOT) -> list[str]:
    """查一道题的考生材料 tasks/<岗位>/<题号>/。"""
    key = root / "grading" / role / tid
    meta = common.read_json(key / "meta.json")
    return check_directory(root / "tasks" / role / tid, list(meta.get("leak_tokens") or []), key)
