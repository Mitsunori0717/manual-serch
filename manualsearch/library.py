"""マニュアル一覧（ライブラリ台帳）。

PDF置き場の直下に ``library.csv`` を置くと、ファイル名だけでは分からない情報を
マニュアルごとに足せる。台帳が無くても検索はそのまま動くので、必要になってから
書き始めればよい。

列（ヘッダ行は必須、順不同、余分な列は無視）::

    path,machine,title,category,tags,note
    ロボドリル/α-D21MiB5_操作説明書.pdf,ロボドリル,α-D21MiB5 操作説明書,操作,アラーム 段取り,2024年改訂

- ``path``: PDF置き場からの相対パス。これだけが必須。
- ``machine``: 機種名。検索画面の「使っている機械」の選択肢になる。
  空欄なら第1階層のフォルダ名を機種として扱うので、機種ごとにフォルダを分けてあれば
  台帳を書かなくても機種で絞り込める。
- ``title``: 一覧と検索結果に出す名前。空ならPDFのタイトルかファイル名を使う。
- ``category`` / ``tags`` / ``note``: 用途の分類とメモ。絞り込みと目視確認に使う。

``model`` という列名でも ``machine`` と同じものとして読む（呼び方の揺れ対策）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

COLUMNS = ["path", "machine", "title", "category", "tags", "note"]

# 機種列の別名。社内でどちらの呼び方をしていても読めるようにする。
_MACHINE_ALIASES = ("machine", "model", "機種")


@dataclass
class LibraryEntry:
    """台帳1行分。"""

    path: str
    machine: str = ""
    title: str = ""
    category: str = ""
    tags: str = ""
    note: str = ""


class LibraryError(ValueError):
    """台帳の書式がおかしい。"""


def _cell(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def load(path: Path) -> dict[str, LibraryEntry]:
    """台帳を読み込む。ファイルが無ければ空の辞書を返す。"""
    path = Path(path)
    if not path.is_file():
        return {}

    # Excelで保存するとBOMが付くので utf-8-sig で開く
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "path" not in reader.fieldnames:
            raise LibraryError(
                f"{path} に path 列がありません。1行目に {','.join(COLUMNS)} が必要です。"
            )

        entries: dict[str, LibraryEntry] = {}
        for row in reader:
            rel = _cell(row, "path").replace("\\", "/")
            if not rel or rel.startswith("#"):
                continue
            entries[rel] = LibraryEntry(
                path=rel,
                machine=_cell(row, *_MACHINE_ALIASES),
                title=_cell(row, "title"),
                category=_cell(row, "category"),
                tags=_cell(row, "tags"),
                note=_cell(row, "note"),
            )
    return entries


def save(path: Path, entries: list[LibraryEntry]) -> None:
    """台帳を書き出す。Excelで開けるようにBOM付きUTF-8にする。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "path": entry.path,
                    "machine": entry.machine,
                    "title": entry.title,
                    "category": entry.category,
                    "tags": entry.tags,
                    "note": entry.note,
                }
            )


def machine_from_path(rel_path: str) -> str:
    """第1階層のフォルダ名を機種名として使う（台帳が無いときの既定）。"""
    return rel_path.split("/")[0] if "/" in rel_path else ""


def scaffold(
    root: Path,
    pdf_paths: list[Path],
    existing: dict[str, LibraryEntry] | None = None,
) -> list[LibraryEntry]:
    """見つかったPDFから台帳の下書きを作る。

    既にある行は書き換えず、新しく増えたPDFだけを追記する。``machine`` は
    フォルダ名で埋めておくので、機種ごとにフォルダを分けてあればそのまま使える。
    """
    root = Path(root)
    existing = existing or {}
    entries: list[LibraryEntry] = []
    seen: set[str] = set()

    for pdf in pdf_paths:
        rel = pdf.relative_to(root).as_posix()
        seen.add(rel)
        if rel in existing:
            entries.append(existing[rel])
            continue
        entries.append(LibraryEntry(path=rel, machine=machine_from_path(rel)))

    # 実体が消えたPDFの行も、書き戻すときに消えないよう残す
    for rel, entry in existing.items():
        if rel not in seen:
            entries.append(entry)

    entries.sort(key=lambda entry: entry.path)
    return entries
