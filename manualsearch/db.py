"""索引データベース（SQLite + FTS5）。

検索エンジンのサーバーは立てず、DBファイル1つで完結させる。日本語は分かち書きが
必要になるのが普通だが、``tokenize='trigram'`` なら3文字以上の任意の部分文字列で
引けるので、形態素解析器を入れずに済む。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analyze import Analysis

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    path          TEXT    NOT NULL UNIQUE,   -- ルートからの相対パス
    title         TEXT    NOT NULL,
    machine       TEXT    NOT NULL DEFAULT '',  -- 機種名（library.csv かフォルダ名）
    category      TEXT    NOT NULL DEFAULT '',
    tags          TEXT    NOT NULL DEFAULT '',
    note          TEXT    NOT NULL DEFAULT '',
    size          INTEGER NOT NULL,
    mtime         REAL    NOT NULL,
    page_count    INTEGER NOT NULL DEFAULT 0,
    ocr_pages     INTEGER NOT NULL DEFAULT 0,
    indexed_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS documents_machine_idx ON documents(machine);

CREATE TABLE IF NOT EXISTS pages (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    page_no      INTEGER NOT NULL,
    section      TEXT    NOT NULL DEFAULT '',  -- そのページが属する章
    text         TEXT    NOT NULL,
    UNIQUE(document_id, page_no)
);

CREATE INDEX IF NOT EXISTS pages_document_idx ON pages(document_id);

-- 章立て（PDFのしおり、無ければ本文から拾った見出し）
CREATE TABLE IF NOT EXISTS sections (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    level        INTEGER NOT NULL DEFAULT 1,
    title        TEXT    NOT NULL,
    page_no      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS sections_document_idx ON sections(document_id);

-- 本文から拾ったエラーコード・型番。コード直打ちの検索を確実に当てるため。
CREATE TABLE IF NOT EXISTS codes (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    code         TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    page_no      INTEGER NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS codes_code_idx ON codes(code);
CREATE INDEX IF NOT EXISTS codes_document_idx ON codes(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    text,
    content='pages',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO pages_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """索引DBを開く。無ければ作る。"""
    db_path = Path(db_path)
    if read_only:
        if not db_path.exists():
            raise FileNotFoundError(
                f"索引が見つかりません: {db_path}\n先に `python -m manualsearch index` を実行してください。"
            )
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def upsert_document(
    conn: sqlite3.Connection,
    *,
    path: str,
    title: str,
    size: int,
    mtime: float,
    pages: list[str],
    ocr_pages: int,
    machine: str = "",
    category: str = "",
    tags: str = "",
    note: str = "",
    analysis: "Analysis | None" = None,
) -> int:
    """1つのPDFの索引を丸ごと入れ替える。

    ページを消してから入れ直すので、ページ数が減った場合も取りこぼさない。
    ``analysis`` を渡すと章立て・コードも一緒に入れ替える。
    """
    meta = (title, machine, category, tags, note, size, mtime, len(pages), ocr_pages)
    section_of_page = analysis.section_of_page if analysis else {}

    with conn:  # 1つのPDFを1トランザクションに収め、途中で落ちても半端に残さない
        row = conn.execute("SELECT id FROM documents WHERE path = ?", (path,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO documents"
                "(path, title, machine, category, tags, note, size, mtime, page_count, ocr_pages) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (path, *meta),
            )
            doc_id = int(cur.lastrowid)
        else:
            doc_id = int(row["id"])
            conn.execute("DELETE FROM pages WHERE document_id = ?", (doc_id,))
            conn.execute(
                "UPDATE documents SET title = ?, machine = ?, category = ?, tags = ?, note = ?, "
                "size = ?, mtime = ?, page_count = ?, ocr_pages = ?, indexed_at = datetime('now') "
                "WHERE id = ?",
                (*meta, doc_id),
            )

        conn.executemany(
            "INSERT INTO pages(document_id, page_no, section, text) VALUES (?, ?, ?, ?)",
            [
                (doc_id, i, section_of_page.get(i, ""), text)
                for i, text in enumerate(pages, start=1)
                if text.strip()
            ],
        )

        conn.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM codes WHERE document_id = ?", (doc_id,))
        if analysis is not None:
            conn.executemany(
                "INSERT INTO sections(document_id, level, title, page_no) VALUES (?, ?, ?, ?)",
                [(doc_id, s.level, s.title, s.page_no) for s in analysis.sections],
            )
            conn.executemany(
                "INSERT INTO codes(document_id, code, kind, page_no, count) VALUES (?, ?, ?, ?, ?)",
                [(doc_id, c.code, c.kind, c.page_no, c.count) for c in analysis.codes],
            )
    return doc_id


def update_metadata(
    conn: sqlite3.Connection,
    doc_id: int,
    *,
    title: str,
    machine: str,
    category: str,
    tags: str,
    note: str,
) -> bool:
    """本文を触らずに台帳由来の情報だけ更新する。変化があれば ``True``。

    library.csv を書き換えただけならPDFの中身は読み直す必要がないので、
    差分更新で読み飛ばしたマニュアルにもこれで一覧の情報を反映する。
    """
    row = conn.execute(
        "SELECT title, machine, category, tags, note FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return False
    if (row["title"], row["machine"], row["category"], row["tags"], row["note"]) == (
        title,
        machine,
        category,
        tags,
        note,
    ):
        return False

    with conn:
        conn.execute(
            "UPDATE documents SET title = ?, machine = ?, category = ?, tags = ?, note = ? "
            "WHERE id = ?",
            (title, machine, category, tags, note, doc_id),
        )
    return True


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM pages WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM codes WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def known_documents(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """相対パス → documents の行。差分更新の判定に使う。"""
    rows = conn.execute("SELECT id, path, title, size, mtime FROM documents").fetchall()
    return {row["path"]: row for row in rows}


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    doc_count, page_count, ocr_pages = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(page_count), 0), COALESCE(SUM(ocr_pages), 0) FROM documents"
    ).fetchone()
    indexed_pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    chars = conn.execute("SELECT COALESCE(SUM(LENGTH(text)), 0) FROM pages").fetchone()[0]
    machines = conn.execute("SELECT COUNT(DISTINCT machine) FROM documents WHERE machine <> ''").fetchone()[0]
    sections = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    codes = conn.execute("SELECT COUNT(DISTINCT code) FROM codes").fetchone()[0]
    return {
        "documents": doc_count,
        "machines": machines,
        "pages": page_count,
        "indexed_pages": indexed_pages,
        "ocr_pages": ocr_pages,
        "characters": chars,
        "sections": sections,
        "codes": codes,
    }


def optimize(conn: sqlite3.Connection) -> None:
    """索引を最適化する。大量投入のあとに1回呼ぶと検索が速くなる。"""
    with conn:
        conn.execute("INSERT INTO pages_fts(pages_fts) VALUES ('optimize')")
    conn.execute("VACUUM")
