"""日本語PDFのテキストを検索しやすい形に正規化する。

PDFから抜いた生テキストをそのまま索引に入れると、以下の理由で検索が当たらない。

- 全角/半角の揺れ（``ＡＢＣ`` と ``ABC``、``１２３`` と ``123``）
- 文字送りの都合で1文字ずつ空白が入る（``マ ニ ュ ア ル``）
- 単語の途中で改行される（``エラーコ`` + 改行 + ``ード``）

ここではこの3つをまとめて潰す。索引にも表示にも同じ正規化済みテキストを使うので、
検索でヒットした文字列は必ずスニペットの中にも同じ形で存在する。
"""

from __future__ import annotations

import re
import unicodedata

# NFKC後に残る日本語の文字種。長音符・々・ー なども単語の一部として扱う。
_CJK_CHARS = (
    r"々〆〇"  # 々 〆 〇
    r"ぁ-ゟ"  # ひらがな
    r"゠-ヿ"  # カタカナ（ー を含む）
    r"㐀-䶿"  # CJK拡張A
    r"一-鿿"  # CJK統合漢字
    r"豈-﫿"  # CJK互換漢字
)
_CJK = re.compile(f"[{_CJK_CHARS}]")

# 幅ゼロ文字・ソフトハイフンなど、見えないのに一致を邪魔する文字
_INVISIBLE = re.compile(r"[­​-‏  ﻿]")

# 日本語文字にはさまれた空白（``マ ニ ュ ア ル`` 対策）
_CJK_GAP = re.compile(f"(?<=[{_CJK_CHARS}])[ \t]+(?=[{_CJK_CHARS}])")

_SPACES = re.compile(r"[ \t　]+")
_LATIN_TAIL = re.compile(r"[A-Za-z]$")
_LATIN_HEAD = re.compile(r"^[A-Za-z]")


def is_cjk(ch: str) -> bool:
    """文字が日本語（漢字・かな）かどうか。"""
    return bool(ch) and bool(_CJK.match(ch))


def normalize_line(line: str) -> str:
    """1行分を正規化する。改行は含まない前提。"""
    line = unicodedata.normalize("NFKC", line)
    line = _INVISIBLE.sub("", line)
    line = _SPACES.sub(" ", line)
    line = _CJK_GAP.sub("", line)
    return line.strip()


def join_lines(lines: list[str], separator: str = " ") -> str:
    """行を、単語が切れないようにつなぐ。

    - 日本語で終わって日本語で始まる → 区切りなしで連結
    - ``inter-`` + ``national`` → ハイフンを落として連結
    - それ以外 → ``separator`` でつなぐ

    日本語同士を必ず連結するのが肝心で、これをやらないと ``エラーコ`` / ``ード`` の
    ように改行で割れた語が検索から漏れる。PDFによっては1行が1ブロックとして
    返ってくるので、段落内・段落間のどちらにも同じ規則を当てる。
    """
    parts: list[str] = []
    for raw in lines:
        line = normalize_line(raw)
        if not line:
            continue
        if not parts:
            parts.append(line)
            continue

        prev = parts[-1]
        if is_cjk(prev[-1]) and is_cjk(line[0]):
            parts[-1] = prev + line
        elif prev.endswith("-") and _LATIN_TAIL.search(prev[:-1] or " ") and _LATIN_HEAD.match(line):
            parts[-1] = prev[:-1] + line
        else:
            parts[-1] = prev + separator + line
    return parts[0] if parts else ""


# 段落をまたいで連結してよいと判断する、直前の段落の最低の長さ。
# これより短い段落は見出しや表のセルとみなし、次の段落とはつなげない。
_MIN_JOIN_LEN = 15

# 文が終わっている印。ここで切れているなら語の途中ではない。
_SENTENCE_END = "。．！？!?」』）)】"


def _joins_with_next(prev: str, nxt: str) -> bool:
    """段落 ``prev`` の末尾で語が切れていそうかどうか。"""
    if not prev or not nxt:
        return False
    if not (is_cjk(prev[-1]) and is_cjk(nxt[0])):
        return False
    if prev[-1] in _SENTENCE_END:
        return False
    # 短い段落は見出しの可能性が高いので、本文と混ぜない
    return len(prev) >= _MIN_JOIN_LEN


def normalize_blocks(blocks: list[str]) -> str:
    """段落（ブロック）のリストを1ページ分のテキストにまとめる。

    段落内の改行は :func:`join_lines` でつなぐ。段落同士は基本的に改行で区切るが、
    1行が1ブロックとして返ってくるPDFがあるので、文の途中で切れている段落だけは
    次とつなげて、検索語が改行で割れないようにする。
    """
    parts: list[str] = []
    for block in blocks:
        text = join_lines(block.splitlines())
        if not text:
            continue
        if parts and _joins_with_next(parts[-1], text):
            parts[-1] += text
        elif parts and parts[-1].endswith("-") and _LATIN_TAIL.search(parts[-1][:-1] or " ") \
                and _LATIN_HEAD.match(text):
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)
    return "\n".join(parts)


def normalize_text(text: str) -> str:
    """任意のテキスト（検索クエリを含む）を索引と同じ土俵に乗せる。"""
    lines = [normalize_line(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)
