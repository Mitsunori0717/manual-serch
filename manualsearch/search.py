"""索引に対する検索。

FTS5のMATCHで候補ページを絞り、bm25で並べる。ヒット位置のハイライトはFTS5の
``snippet()`` ではなくPython側で行う。trigramトークナイザだとFTS5のハイライトが
3文字単位に切れてしまい、``エラーコード`` が ``エラーコ`` としか光らないため。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .query import MIN_FTS_TERM, ParsedQuery, build_match_expr, count_hits, make_snippet, parse_query


@dataclass
class Hit:
    """1ページ分の検索結果。"""

    document_id: int
    path: str
    title: str
    machine: str
    page_no: int
    section: str
    snippet: str
    hits: int
    score: float


@dataclass
class DocumentHits:
    """同じPDF内のヒットをまとめたもの。"""

    document_id: int
    path: str
    title: str
    machine: str
    pages: list[Hit] = field(default_factory=list)

    @property
    def total_hits(self) -> int:
        return sum(page.hits for page in self.pages)


@dataclass
class SearchResult:
    query: str
    parsed: ParsedQuery
    hits: list[Hit]
    total: int
    took_ms: float
    slow_path: bool = False

    def by_document(self) -> list[DocumentHits]:
        """ヒットをPDFごとにまとめる。並び順（スコア順）は保つ。"""
        grouped: dict[int, DocumentHits] = {}
        for hit in self.hits:
            group = grouped.get(hit.document_id)
            if group is None:
                group = DocumentHits(
                    document_id=hit.document_id,
                    path=hit.path,
                    title=hit.title,
                    machine=hit.machine,
                )
                grouped[hit.document_id] = group
            group.pages.append(hit)
        return list(grouped.values())


def _like_pattern(term: str) -> str:
    """LIKE用にワイルドカードを打ち消す。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _build_filters(
    parsed: ParsedQuery,
    machine: str | None,
    category: str | None,
) -> tuple[list[str], list[str]]:
    """MATCHで表現できない条件をSQLの条件式に落とす。"""
    clauses: list[str] = []
    params: list[str] = []

    for term in parsed.like_include:
        clauses.append("p.text LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(term))

    for term in parsed.exclude:
        if len(term) < MIN_FTS_TERM:  # 3文字以上の除外語はMATCH側で処理済み
            clauses.append("p.text NOT LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(term))

    if machine:
        clauses.append("d.machine = ?")
        params.append(machine)

    if category:
        clauses.append("d.category = ?")
        params.append(category)

    return clauses, params


def search(
    conn: sqlite3.Connection,
    raw_query: str,
    *,
    limit: int = 50,
    offset: int = 0,
    machine: str | None = None,
    category: str | None = None,
) -> SearchResult:
    """全文検索を実行する。``machine`` を渡すとその機種のマニュアルだけを対象にする。"""
    started = time.perf_counter()
    parsed = parse_query(raw_query)
    if not parsed.include:
        return SearchResult(query=raw_query, parsed=parsed, hits=[], total=0, took_ms=0.0)

    match_expr = build_match_expr(parsed)
    filters, filter_params = _build_filters(parsed, machine, category)
    where = list(filters)
    params: list[object] = []

    if match_expr is not None:
        source = (
            "FROM pages_fts "
            "JOIN pages p ON p.id = pages_fts.rowid "
            "JOIN documents d ON d.id = p.document_id"
        )
        where.insert(0, "pages_fts MATCH ?")
        params.append(match_expr)
        order = "bm25(pages_fts), d.path, p.page_no"
        score_expr = "bm25(pages_fts)"
        slow_path = False
    else:
        # 1〜2文字だけの検索。trigram索引が使えないので全ページ走査になる。
        source = "FROM pages p JOIN documents d ON d.id = p.document_id"
        order = "d.path, p.page_no"
        score_expr = "0.0"
        slow_path = True

    params.extend(filter_params)
    where_sql = " WHERE " + " AND ".join(where) if where else ""

    total = conn.execute(f"SELECT COUNT(*) {source}{where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT p.id, p.document_id, p.page_no, p.section, p.text, d.path, d.title, d.machine, "
        f"{score_expr} AS score "
        f"{source}{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    hits = [
        Hit(
            document_id=row["document_id"],
            path=row["path"],
            title=row["title"],
            machine=row["machine"],
            page_no=row["page_no"],
            section=row["section"],
            snippet=make_snippet(row["text"], parsed.include),
            hits=count_hits(row["text"], parsed.include),
            score=row["score"],
        )
        for row in rows
    ]

    return SearchResult(
        query=raw_query,
        parsed=parsed,
        hits=hits,
        total=total,
        took_ms=(time.perf_counter() - started) * 1000,
        slow_path=slow_path,
    )


def get_document(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def list_documents(
    conn: sqlite3.Connection,
    *,
    machine: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[sqlite3.Row]:
    where, params = ("WHERE d.machine = ?", [machine]) if machine else ("", [])
    # indexed_pages は「本文を取り出せたページ数」。page_count との差が大きければ
    # 文字が画像で入っている＝OCRが必要、と一覧の上で判断できる。
    return conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM pages p WHERE p.document_id = d.id) AS indexed_pages "
        f"FROM documents d {where} ORDER BY d.machine, d.path LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()


@dataclass
class Machine:
    """機種セレクタに出す1件。"""

    name: str
    documents: int
    pages: int


def list_machines(conn: sqlite3.Connection) -> list[Machine]:
    """登録されている機種を、マニュアルの多い順に返す。"""
    rows = conn.execute(
        "SELECT machine, COUNT(*) AS docs, COALESCE(SUM(page_count), 0) AS pages "
        "FROM documents WHERE machine <> '' GROUP BY machine ORDER BY docs DESC, machine"
    ).fetchall()
    return [Machine(name=row["machine"], documents=row["docs"], pages=row["pages"]) for row in rows]


def document_sections(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    """1冊の章立て。マニュアル詳細で目次として出す。"""
    return conn.execute(
        "SELECT level, title, page_no FROM sections WHERE document_id = ? ORDER BY page_no, level",
        (doc_id,),
    ).fetchall()


def document_codes(conn: sqlite3.Connection, doc_id: int, kind: str = "error") -> list[sqlite3.Row]:
    """1冊から拾ったコードの一覧。"""
    return conn.execute(
        "SELECT code, MIN(page_no) AS page_no, SUM(count) AS total FROM codes "
        "WHERE document_id = ? AND kind = ? GROUP BY code ORDER BY code",
        (doc_id, kind),
    ).fetchall()


def find_code(conn: sqlite3.Connection, code: str, machine: str | None = None) -> list[sqlite3.Row]:
    """エラーコードから、載っているマニュアルとページを直接引く。

    全文検索でも当たるが、コード表のように同じ文字列が並ぶページでは
    こちらのほうが確実で速い。
    """
    where, params = ("AND d.machine = ?", [machine]) if machine else ("", [])
    return conn.execute(
        "SELECT c.code, c.page_no, c.count, d.id AS document_id, d.path, d.title, d.machine, "
        "COALESCE(p.section, '') AS section "
        "FROM codes c JOIN documents d ON d.id = c.document_id "
        "LEFT JOIN pages p ON p.document_id = c.document_id AND p.page_no = c.page_no "
        f"WHERE c.code = ? {where} ORDER BY c.count DESC, d.title, c.page_no LIMIT 50",
        [code.strip().upper(), *params],
    ).fetchall()


def list_categories(conn: sqlite3.Connection, machine: str | None = None) -> list[str]:
    """絞り込み用の用途分類。台帳の category 列を書いたぶんだけ出る。"""
    where, params = ("AND machine = ?", [machine]) if machine else ("", [])
    rows = conn.execute(
        f"SELECT DISTINCT category FROM documents WHERE category <> '' {where} ORDER BY category",
        params,
    ).fetchall()
    return [row["category"] for row in rows]
