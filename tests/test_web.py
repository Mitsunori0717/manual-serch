"""検索画面とAPIの動作確認。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manualsearch import db
from manualsearch.config import AiConfig, Config
from manualsearch.indexer import index_directory
from manualsearch.web import create_app


@pytest.fixture
def client(tmp_path: Path, manual_root: Path):
    db_path = tmp_path / "index.db"
    conn = db.connect(db_path)
    index_directory(conn, manual_root, ocr="never", workers=1)
    conn.close()

    with TestClient(create_app(Config(root=manual_root, db_path=db_path))) as test_client:
        yield test_client


def test_home_shows_the_index_size(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "マニュアル" in response.text


def test_search_page_lists_hits(client):
    response = client.get("/search", params={"q": "エラーコード"})
    assert response.status_code == 200
    assert "<mark>エラーコード</mark>" in response.text
    assert "P-100 ポンプ取扱説明書" in response.text


def test_search_page_without_query_falls_back_to_home(client):
    assert client.get("/search", params={"q": ""}).status_code == 200


def test_no_hits_shows_a_hint(client):
    response = client.get("/search", params={"q": "存在しない語句"})
    assert "見つかりませんでした" in response.text


def test_machine_filter_is_applied(client):
    response = client.get("/search", params={"q": "エラーコード", "machine": "コンプレッサ"})
    assert "見つかりませんでした" in response.text


def test_api_returns_json_hits(client):
    payload = client.get("/api/search", params={"q": "エラーコード"}).json()
    assert payload["total"] == 2
    assert payload["hits"][0]["url"].startswith("/file/")
    assert "#page=" in payload["hits"][0]["url"]


def test_pdf_is_served_inline_so_page_anchors_work(client):
    doc_id = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"][0]["document_id"]
    response = client.get(f"/file/{doc_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")


def test_unknown_document_is_404(client):
    assert client.get("/file/99999").status_code == 404


def test_missing_pdf_on_disk_is_404(client, manual_root: Path):
    doc_id = client.get("/api/search", params={"q": "international"}).json()["hits"][0]["document_id"]
    (manual_root / "コンプレッサ" / "C-9_manual.pdf").unlink()
    assert client.get(f"/file/{doc_id}").status_code == 404


def test_document_outside_the_root_is_refused(client, tmp_path: Path, manual_root: Path):
    """索引に細工されたパスが入っていてもルート外は配信しない。"""
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")

    conn = db.connect(tmp_path / "index.db")
    conn.execute(
        "INSERT INTO documents(path, title, size, mtime) VALUES (?, '悪意', 0, 0)",
        ("../secret.pdf",),
    )
    conn.commit()
    doc_id = conn.execute("SELECT id FROM documents WHERE path = '../secret.pdf'").fetchone()["id"]
    conn.close()

    assert client.get(f"/file/{doc_id}").status_code == 404


def test_library_page_lists_every_manual(client):
    response = client.get("/library")
    assert response.status_code == 200
    assert "C-9_manual.pdf" in response.text


def test_healthz_reports_the_index(client):
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["documents"] == 3


# --------------------------------------------------------------- AI相談画面
@pytest.fixture
def ai_client(tmp_path: Path, manual_root: Path):
    """ChatGPT連携を有効にした状態のアプリ（APIは偽物に差し替える）。"""
    from manualsearch.assistant import Assistant
    from manualsearch.config import AiConfig
    from test_assistant import FakeClient

    db_path = tmp_path / "index.db"
    conn = db.connect(db_path)
    index_directory(conn, manual_root, ocr="never", workers=1)
    conn.close()

    config = Config(root=manual_root, db_path=db_path, ai=AiConfig(api_key="test-key"))
    app = create_app(config)
    app.state.assistant = Assistant(
        config.ai,
        client=FakeClient(['{"queries": ["エラーコード"]}', "冷却ファンを確認してください[1]"]),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_ask_tab_is_always_visible_and_marked_when_unset(client):
    """キーが無くてもタブは出す。出さないと設定画面にたどり着けないため。"""
    body = client.get("/").text
    assert "AIに相談" in body
    assert "未設定" in body


def test_ask_tab_appears_when_the_api_key_is_set(ai_client):
    assert "AIに相談" in ai_client.get("/").text


def test_ask_page_shows_the_answer_with_linked_citations(ai_client):
    response = ai_client.get("/ask", params={"q": "E203が出たらどうすればいい？"})

    assert response.status_code == 200
    assert "冷却ファンを確認してください" in response.text
    assert 'class="cite"' in response.text


def test_ask_page_without_a_question_shows_the_guide(ai_client):
    assert "マニュアルの内容について相談する" in ai_client.get("/ask").text


def test_ask_page_points_at_the_settings_screen_when_disabled(client):
    response = client.get("/ask")
    assert "有効になっていません" in response.text
    assert 'href="/settings"' in response.text


def test_api_ask_returns_sources(ai_client):
    payload = ai_client.get("/api/ask", params={"q": "E203について"}).json()
    assert payload["sources"]
    assert payload["sources"][0]["url"].startswith("/file/")


def test_api_ask_is_unavailable_without_a_key(client):
    assert client.get("/api/ask", params={"q": "E203"}).status_code == 503


# --------------------------------------------------------------- 機種の記憶
def test_selected_machine_is_remembered_in_a_cookie(client):
    response = client.get("/search", params={"q": "エラーコード", "machine": "ポンプ"})
    assert response.cookies.get("manual_machine")

    # 次のリクエストで機種を指定しなくても、覚えた機種で絞り込まれる
    follow_up = client.get("/search", params={"q": "international"})
    assert "見つかりませんでした" in follow_up.text


def test_api_machines_lists_the_selector_options(client):
    names = {item["name"] for item in client.get("/api/machines").json()}
    assert names == {"ポンプ", "コンプレッサ"}
