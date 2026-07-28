"""検索クエリの解釈と、ヒット箇所のスニペット生成。

対応する書き方::

    エラーコード E203        すべて含むページ（AND）
    "冷却 ファン"            空白ごと完全一致
    ポンプ -旧型             「旧型」を含むページを除外

FTS5のtrigramトークナイザは3文字未満の語を索引できないので、1〜2文字の語だけは
LIKEで後段フィルタする。利用者からは違いが見えないようにしてある。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .textnorm import normalize_line

# trigramトークナイザが索引できる最小の長さ
MIN_FTS_TERM = 3

_TOKEN = re.compile(r'[-－−]?"[^"]*"|\S+')

# 除外を表す先頭記号。長音符 ー(U+30FC) は日本語の一部なので含めない。
_MINUS = frozenset("-－−")


@dataclass
class ParsedQuery:
    """クエリ文字列を含む語・除外する語に分解したもの。"""

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.include and not self.exclude

    @property
    def fts_include(self) -> list[str]:
        """FTS5のMATCHで引ける語（3文字以上）。"""
        return [t for t in self.include if len(t) >= MIN_FTS_TERM]

    @property
    def like_include(self) -> list[str]:
        """短すぎてMATCHで引けず、LIKEで絞り込む語。"""
        return [t for t in self.include if len(t) < MIN_FTS_TERM]


def parse_query(raw: str) -> ParsedQuery:
    """クエリ文字列を :class:`ParsedQuery` に変換する。"""
    parsed = ParsedQuery()
    # 全角の引用符も同じ意味で受け付ける
    raw = raw.replace("“", '"').replace("”", '"').replace("＂", '"')

    for token in _TOKEN.findall(raw):
        negated = token[:1] in _MINUS
        if negated:
            token = token[1:]
        term = normalize_line(token.strip('"'))
        if not term:
            continue
        (parsed.exclude if negated else parsed.include).append(term)
    return parsed


def fts_escape(term: str) -> str:
    """FTS5のフレーズとして安全な形に包む。"""
    return '"' + term.replace('"', '""') + '"'


def build_match_expr(parsed: ParsedQuery) -> str | None:
    """MATCHに渡す式。MATCHだけでは絞れない場合は ``None``。"""
    include = [fts_escape(t) for t in parsed.fts_include]
    if not include:
        return None
    expr = " AND ".join(include)
    exclude = [fts_escape(t) for t in parsed.exclude if len(t) >= MIN_FTS_TERM]
    if exclude:
        expr += " NOT (" + " OR ".join(exclude) + ")"
    return expr


def find_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """``text`` 中の各語の出現位置を、重なりを潰して昇順で返す。"""
    spans: list[tuple[int, int]] = []
    lowered = text.lower()
    for term in terms:
        needle = term.lower()
        if not needle:
            continue
        start = lowered.find(needle)
        while start != -1:
            spans.append((start, start + len(needle)))
            start = lowered.find(needle, start + len(needle))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def count_hits(text: str, terms: list[str]) -> int:
    """ページ内の総ヒット数。並び順の補助に使う。"""
    lowered = text.lower()
    return sum(lowered.count(t.lower()) for t in terms if t)


def make_snippet(text: str, terms: list[str], radius: int = 55, max_windows: int = 2) -> str:
    """ヒット箇所の前後を切り出し、``<mark>`` を付けたHTMLを返す。

    語が1つも見つからない場合は先頭を短く返す（LIKE経由のヒットなどの保険）。
    """
    spans = find_spans(text, terms)
    if not spans:
        head = text[: radius * 2].strip()
        return html.escape(head) + ("…" if len(text) > len(head) else "")

    # ヒットの前後 radius 文字を窓にして、近いものはつなげる
    windows: list[tuple[int, int]] = []
    for start, end in spans:
        win = (max(0, start - radius), min(len(text), end + radius))
        if windows and win[0] <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], win[1]))
        else:
            windows.append(win)
        if len(windows) >= max_windows and windows[-1][1] >= spans[-1][1]:
            break
    windows = windows[:max_windows]

    pieces: list[str] = []
    for win_start, win_end in windows:
        buf: list[str] = []
        cursor = win_start
        for start, end in spans:
            if end <= win_start or start >= win_end:
                continue
            buf.append(html.escape(text[cursor:start]))
            buf.append("<mark>" + html.escape(text[start:end]) + "</mark>")
            cursor = end
        buf.append(html.escape(text[cursor:win_end]))
        piece = "".join(buf)
        if win_start > 0:
            piece = "…" + piece
        if win_end < len(text):
            piece = piece + "…"
        pieces.append(piece)
    return " ".join(pieces)
