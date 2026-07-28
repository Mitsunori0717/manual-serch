"""マニュアル1冊を分解して、検索に効く情報を洗い出す。

本文をページに割るだけだと「エラーコードで引いたのに載っているページが出てこない」
「どの章の話か分からない」といったことが起きる。そこで取り込み時に1冊ずつ、

- 目次（PDFのしおり、無ければ本文の見出し行）から章立てを組み立てる
- 各ページがどの章に属するかを決める
- エラーコード・アラーム番号・型番を拾い出す
- 機種名の候補を出す（フォルダ名・ファイル名・表紙）

まで済ませてDBに入れる。ここで拾った情報は library.csv で後から上書きできる。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .extract import ExtractResult
from .textnorm import normalize_line

# --------------------------------------------------------------------- 見出し
# 「第3章 ...」「3.2 ...」「■ 保守」など、行頭が番号や記号で始まる短い行を見出しとみなす
_HEADING_PATTERNS = [
    re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*[章節編部]\s*(?P<title>.*)$"),
    re.compile(r"^(?P<num>[0-9]+(?:[.\-][0-9]+){0,2})[.\s　]+(?P<title>\S.*)$"),
    re.compile(r"^[■●◆□▲※]\s*(?P<title>\S.*)$"),
    re.compile(r"^(?:Chapter|Section)\s+[0-9IVX]+[.\s]+(?P<title>\S.*)$", re.I),
]
_MAX_HEADING_LEN = 40

# --------------------------------------------------------------------- コード
# 「エラーコード E203」「アラーム 1234」のように、前置きが付いた形
_LABELLED_CODE = re.compile(
    r"(?:エラー|ｴﾗｰ|アラーム|警報|異常|故障|ERROR|ALARM|FAULT)"
    r"\s*(?:コード|番号|No\.?|CODE)?\s*[:：]?\s*"
    r"(?P<code>[A-Z]{0,4}[-]?[0-9]{2,5}[A-Z]?)",
    re.I,
)
# 「E203」「SV0417」など、単体で現れる英字+数字の型
_BARE_CODE = re.compile(r"\b(?P<code>[A-Z]{1,3}[-]?[0-9]{3,5})\b")

# 型番らしい並び（英数字とハイフンが混ざり、数字を含むもの）
_MODEL_CODE = re.compile(r"\b(?P<code>[A-Zα-ωΑ-Ω][A-Za-z0-9]*(?:[-][A-Za-z0-9]+){1,3})\b")

_STOP_CODES = {"ISO", "JIS", "PDF", "LED", "NO", "OK", "NG", "AC", "DC"}


@dataclass
class Section:
    """章立ての1項目。"""

    level: int
    title: str
    page_no: int


@dataclass
class CodeHit:
    """本文から拾ったコード。"""

    code: str
    kind: str  # "error" か "model"
    page_no: int
    count: int = 1


@dataclass
class Analysis:
    """1冊分の分析結果。"""

    title: str
    page_count: int = 0
    text_pages: int = 0
    ocr_pages: int = 0
    empty_pages: list[int] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    section_of_page: dict[int, str] = field(default_factory=dict)
    codes: list[CodeHit] = field(default_factory=list)
    machine_candidates: list[str] = field(default_factory=list)
    outline_source: str = "none"  # "pdf" | "heuristic" | "none"

    @property
    def error_codes(self) -> list[str]:
        seen: list[str] = []
        for hit in self.codes:
            if hit.kind == "error" and hit.code not in seen:
                seen.append(hit.code)
        return seen

    @property
    def coverage(self) -> float:
        """本文を取り出せたページの割合。低ければOCRの検討が必要。"""
        return self.text_pages / self.page_count if self.page_count else 0.0


def _looks_like_heading(line: str) -> str | None:
    if len(line) > _MAX_HEADING_LEN:
        return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(line)
        if match:
            title = match.groupdict().get("title", "").strip()
            # 「3.2」だけの行や、句点で終わる普通の文は見出しとみなさない
            if title and not title.endswith("。"):
                return line.strip()
    return None


def detect_headings(pages: list[str]) -> list[Section]:
    """しおりが無いPDF向けに、本文から見出しらしい行を拾う。"""
    sections: list[Section] = []
    for page_no, text in enumerate(pages, start=1):
        for line in text.split("\n"):
            heading = _looks_like_heading(normalize_line(line))
            if heading:
                sections.append(Section(level=1, title=heading, page_no=page_no))
                break  # 1ページにつき先頭の見出しだけ採る
    return sections


def map_sections_to_pages(sections: list[Section], page_count: int) -> dict[int, str]:
    """章の開始ページから、各ページが属する章を決める。"""
    if not sections:
        return {}

    ordered = sorted(sections, key=lambda s: (s.page_no, s.level))
    mapping: dict[int, str] = {}
    current = ""
    index = 0
    for page_no in range(1, page_count + 1):
        while index < len(ordered) and ordered[index].page_no <= page_no:
            current = ordered[index].title
            index += 1
        if current:
            mapping[page_no] = current
    return mapping


def detect_codes(pages: list[str], *, max_codes: int = 300) -> list[CodeHit]:
    """エラーコードと型番らしい文字列を拾う。

    前置き付き（「エラーコード E203」）を優先し、単体で現れるものは
    そのページに前置き付きの記述がある場合だけ拾って誤検出を抑える。
    """
    counter: Counter[tuple[str, str, int]] = Counter()

    for page_no, text in enumerate(pages, start=1):
        labelled = {match.group("code").upper() for match in _LABELLED_CODE.finditer(text)}
        for code in labelled:
            counter[(code, "error", page_no)] += 1

        if labelled:
            # 同じページに並んでいる同種のコード（一覧表など）もまとめて拾う
            for match in _BARE_CODE.finditer(text):
                code = match.group("code").upper()
                if code not in labelled and code not in _STOP_CODES:
                    counter[(code, "error", page_no)] += 1

        for match in _MODEL_CODE.finditer(text):
            code = match.group("code").upper()
            if code not in _STOP_CODES and any(ch.isdigit() for ch in code):
                counter[(code, "model", page_no)] += 1

    hits = [
        CodeHit(code=code, kind=kind, page_no=page_no, count=count)
        for (code, kind, page_no), count in counter.most_common(max_codes)
    ]
    hits.sort(key=lambda hit: (hit.kind, hit.page_no, hit.code))
    return hits


def suggest_machines(result: ExtractResult, rel_path: str, codes: list[CodeHit]) -> list[str]:
    """機種名の候補を、確からしい順に返す。

    フォルダ名 → ファイル名の型番 → 表紙に多く出る型番、の順で見る。
    ここで出すのはあくまで候補で、確定は library.csv か ``set`` コマンドで行う。
    """
    candidates: list[str] = []

    def add(value: str) -> None:
        value = normalize_line(value).strip()
        if value and value not in candidates:
            candidates.append(value)

    if "/" in rel_path:
        add(rel_path.split("/")[0])

    # 「P-100_取扱説明書」のような名前から型番だけを切り出す
    stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    for match in _MODEL_CODE.finditer(re.sub(r"[_\s]+", " ", stem)):
        code = match.group("code")
        if any(ch.isdigit() for ch in code):
            add(code)

    # 表紙（先頭2ページ）に出てくる型番は機種名であることが多い
    cover_codes = [hit.code for hit in codes if hit.kind == "model" and hit.page_no <= 2]
    for code in cover_codes[:3]:
        add(code)

    add(result.title)
    return candidates[:5]


def analyze(result: ExtractResult, rel_path: str) -> Analysis:
    """抽出済みのページ群を分析する。"""
    pages = result.pages
    analysis = Analysis(
        title=result.title,
        page_count=len(pages),
        ocr_pages=result.ocr_pages,
        text_pages=sum(1 for text in pages if text.strip()),
        empty_pages=[i for i, text in enumerate(pages, start=1) if not text.strip()],
    )

    if result.outline:
        analysis.sections = [
            Section(level=level, title=title, page_no=page_no)
            for level, title, page_no in result.outline
        ]
        analysis.outline_source = "pdf"
    else:
        analysis.sections = detect_headings(pages)
        analysis.outline_source = "heuristic" if analysis.sections else "none"

    analysis.section_of_page = map_sections_to_pages(analysis.sections, analysis.page_count)
    analysis.codes = detect_codes(pages)
    analysis.machine_candidates = suggest_machines(result, rel_path, analysis.codes)
    return analysis
