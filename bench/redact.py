"""遮蔽：凭据不落盘，给榜单页的数据里不带本机绝对路径和用户名。

两个用处，分开用：
  secrets(text)  把命令、工具输入、报错、回复里像凭据的值换成 ***。收卷文件落盘之前，凡是从线程里抄来的文字都过一遍。
  public(text)   在 secrets 的基础上，把工作目录、用户主目录、题库目录换成占位的写法。只给会进 summary.json 的文字用。

只认明确的形状（名字像凭据的赋值、Bearer、带账号密码的网址、几家常见的密钥前缀、私钥块），
不做「长字符串一律盖掉」：那样会把路径和哈希也盖掉，命令核对和污染证据就没法用了。
纯函数，不碰磁盘。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MASK = "***"
ROOT = Path(__file__).resolve().parent.parent  # 题库目录。这个文件不引用 bench 里别的模块，common 才能反过来用它


def _norm(path: str) -> str:
    """正斜杠、盘符大写、/d/x 和 /mnt/d/x 转成 D:/x。只为列出一个目录的几种写法用。"""
    s = str(path).strip().strip('"').strip("'").replace("\\", "/")
    m = re.match(r"^/(?:(?:mnt|cygdrive)/)?([a-zA-Z])(?:/|$)", s)
    if m:
        s = f"{m.group(1).upper()}:/{s[m.end():]}"
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + ":/" + s[2:].lstrip("/")
    return re.sub(r"/{2,}", "/", s)
# 名字像凭据的词。后面不能再接字母，免得 tokens、author、passwordless 这些被误认。
_NAME = (r"(?:api[_-]?key|apikey|access[_-]?key|secret|token|passwd|password|pwd|credential|authorization|auth"
         r"|cookie|session[_-]?id|private[_-]?key)(?![A-Za-z])")
_STRONG = re.compile(r"(?i)pass|pwd|secret")
_VALUE = r"(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s\"'&|;,)]{4,})"
# 名字明说是密码、密钥的：值再短、是纯数字也盖（--password abc、password=123、"password": 123456）。
# 别的名字（token、auth、cookie 这些）还是要 4 个字符以上才盖，免得 max_token=1 这类被误盖。
_PASS = r"(?:passwd|password|pwd|secret)(?![A-Za-z])"


def _pass_json(m: re.Match) -> str:
    """JSON 里密码一类的值：带引号的保留引号，数字和布尔值盖成带引号的 ***，这样还是合法的 JSON。
    引号前面带反斜杠的（JSON 写在另一层引号里）照原样带上。"""
    q = m.group(3) or m.group(2)
    return m.group(1) + q + MASK + q


def _assign(m: re.Match) -> str:
    """名字像凭据的赋值。值只是一个不长的数（max_token=4096 这种）就不动，除非名字是密码一类。"""
    name, value = m.group(1), m.group(2)
    if re.fullmatch(r"[\"']?\d{1,10}[\"']?", value) and not _STRONG.search(name):
        return m.group(0)
    return name + MASK


_RULES: list[tuple[re.Pattern, Any]] = [
    # 私钥块
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     "-----BEGIN PRIVATE KEY----- " + MASK),
    # Bearer xxx；Basic 只在 Authorization 后面才认，免得吃掉普通英文词
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 " + MASK),
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?basic)\s+[A-Za-z0-9+/=]{8,}"), r"\1 " + MASK),
    # 带账号密码的网址 scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://)[^\s/:@\"']+:[^\s/@\"']+@"), r"\1" + MASK + "@"),
    # curl -u 账号:密码
    (re.compile(r"(?i)(\bcurl(?:\.exe)?\b[^\n|;&]*?\s(?:-u|--user)[\s=]+)(\"[^\"\n]+\"|'[^'\n]+'|\S+)"), r"\1" + MASK),
    # 密码一类的名字，值不限长短：JSON 里的字符串、数字、布尔值；赋值；命令行开关
    (re.compile(r"(?i)((\\*[\"'])[\w.-]*" + _PASS + r"[\w.-]*\\*[\"']\s*:\s*)"
                r"(?:(\\*[\"'])[^\"'\n]+\3|-?\d[\w.+-]*|true|false)"), _pass_json),
    (re.compile(r"(?i)((?<![\w/\.-])[\w.:$-]*" + _PASS + r"[\w.-]*\s*[=:]\s*)"
                r"(?:\"[^\"\n]+\"|'[^'\n]+'|[^\s\"'&|;,)]+)"), r"\1" + MASK),
    (re.compile(r"(?i)(--?[\w-]*" + _PASS + r"[\w-]*\s+)(?:\"[^\"\n]+\"|'[^'\n]+'|[^\s\"'&|;<>-][^\s\"'&|;]*)"),
     r"\1" + MASK),
    # JSON 或字典里的 "apiKey": "值"。引号前面可以带反斜杠：JSON 写在命令行的另一层引号里时就是那个样子
    (re.compile(r"(?i)(\\*[\"'][\w.-]*" + _NAME + r"[\w.-]*\\*[\"']\s*:\s*)(\\*[\"'])[^\"'\n]{4,}\2"),
     r"\1\2" + MASK + r"\2"),
    # 赋值：NAME=值、$env:NAME = "值"、NAME: 值
    (re.compile(r"(?i)((?<![\w/\.-])[\w.:$-]*" + _NAME + r"[\w.-]*\s*[=:]\s*)" + _VALUE), _assign),
    # 命令行长开关：--token 值、--api-key 值
    (re.compile(r"(?i)(--?[\w-]*" + _NAME + r"[\w-]*\s+)(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s\"'&|;-][^\s\"'&|;]{3,})"), _assign),
    # 几家常见的密钥形状
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), MASK),
]


def secrets(text: Any) -> Any:
    """把一段文字里像凭据的值换成 ***。不是字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    for pat, rep in _RULES:
        text = pat.sub(rep, text)
    return text


def deep(obj: Any, fn=secrets, depth: int = 0) -> Any:
    """对字符串、列表、字典里的每个字符串值做一遍 fn。键不动。"""
    if isinstance(obj, str):
        return fn(obj)
    if depth > 8:
        return obj
    if isinstance(obj, list):
        return [deep(x, fn, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: deep(v, fn, depth + 1) for k, v in obj.items()}
    return obj


def _variants(path: str) -> list[str]:
    """一个目录在文字里可能出现的几种写法：正斜杠、反斜杠、转义过的反斜杠、Git Bash 的 /d/…、WSL 的 /mnt/d/…。"""
    p = _norm(path).rstrip("/")
    if not p:
        return []
    out = [p, p.replace("/", "\\"), p.replace("/", "\\\\")]
    if len(p) >= 2 and p[1] == ":":
        out += [f"/{p[0].lower()}{p[2:]}", f"/mnt/{p[0].lower()}{p[2:]}"]
    return out


class Public:
    """把文字变成可以放进 summary.json 的写法：凭据盖掉，本机目录换成占位。"""

    def __init__(self, workdir: str | None = None, home: str | None = None, extra: dict[str, str] | None = None):
        table: list[tuple[str, str]] = []
        if workdir:
            table.append((str(workdir), "<工作目录>"))
        for path, label in (extra or {}).items():
            table.append((path, label))
        table.append((str(ROOT), "<题库>"))
        h = _norm(home if home is not None else str(Path.home()))
        table.append((h, "~"))
        # 长的先换，免得工作目录先被它的上级目录换掉
        pairs = [(v, label) for path, label in table for v in _variants(path)]
        pairs.sort(key=lambda x: -len(x[0]))
        self._pairs = [(re.compile(re.escape(v), re.I), label) for v, label in pairs if len(v) >= 3]
        # 用户名：不管是不是本机这个用户，Users 后面那一段、/home 后面那一段都盖掉
        self._user = re.compile(r"(?i)([\\/]Users[\\/]+|(?<![\w.:<>~-])/home/)[^\\/\s\"'`<>|;&,()]+")
        # 剩下的绝对路径只留最后两段
        self._abs = re.compile(r"(?<![\w<>~])(?:[A-Za-z]:[\\/]|\\\\[\w.$-]+\\|/mnt/[a-z]/|/[a-z]/(?=[^\s/]{2}))"
                               r"[^\s\"'`|;&<>*?,()\[\]{}]*")

    def text(self, s: Any) -> Any:
        if not isinstance(s, str) or not s:
            return s
        s = secrets(s)
        for pat, label in self._pairs:
            s = pat.sub(lambda _m, label=label: label, s)

        def tail(m: re.Match) -> str:
            parts = [x for x in re.split(r"[\\/]+", m.group(0)) if x]
            for i in range(1, len(parts)):
                if parts[i - 1].casefold() in ("users", "home"):
                    parts[i] = "<用户>"
            return "<本机其他目录>/" + "/".join(parts[-2:] if len(parts) > 2 else parts[1:] or parts[-1:])

        s = self._abs.sub(tail, s)
        return self._user.sub(lambda m: m.group(1) + "<用户>", s)

    def deep(self, obj: Any) -> Any:
        return deep(obj, self.text)
