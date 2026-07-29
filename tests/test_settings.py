"""設定画面（APIキーの登録と .env の読み書き）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manualsearch import db
from manualsearch.config import AiConfig, Config, load_env_file, mask_secret, save_env_file
from manualsearch.indexer import index_directory
from manualsearch.web import create_app


# --------------------------------------------------------------------- .env
def test_missing_env_file_is_not_an_error(tmp_path: Path):
    assert load_env_file(tmp_path / ".env") == {}


def test_values_are_read_and_exported(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-test-123\n", encoding="utf-8")

    assert load_env_file(path)["OPENAI_API_KEY"] == "sk-test-123"
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-test-123"


def test_comments_quotes_and_export_are_handled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MANUAL_AI_MODEL", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "# コメント行\n\nexport MANUAL_AI_MODEL=\"gpt-4o-mini\"\n空行の次\n",
        encoding="utf-8",
    )
    assert load_env_file(path) == {"MANUAL_AI_MODEL": "gpt-4o-mini"}


def test_an_existing_environment_variable_wins(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-environment")
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
    load_env_file(path)

    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-environment"


def test_excel_style_bom_is_handled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-bom\n", encoding="utf-8-sig")
    assert load_env_file(path)["OPENAI_API_KEY"] == "sk-bom"


def test_saving_creates_the_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env"
    save_env_file({"OPENAI_API_KEY": "sk-new"}, path)
    assert load_env_file(path)["OPENAI_API_KEY"] == "sk-new"


def test_saving_keeps_unrelated_lines(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("# 大事なメモ\nMANUAL_OCR_LANG=jpn+eng\n", encoding="utf-8")

    save_env_file({"OPENAI_API_KEY": "sk-new"}, path)
    body = path.read_text(encoding="utf-8")

    assert "# 大事なメモ" in body
    assert "MANUAL_OCR_LANG=jpn+eng" in body
    assert "OPENAI_API_KEY=sk-new" in body


def test_saving_replaces_an_existing_value(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-old\n", encoding="utf-8")

    save_env_file({"OPENAI_API_KEY": "sk-new"}, path)
    assert path.read_text(encoding="utf-8").count("OPENAI_API_KEY") == 1
    assert load_env_file(path)["OPENAI_API_KEY"] == "sk-new"


def test_saving_an_empty_value_removes_the_entry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-old\n", encoding="utf-8")

    save_env_file({"OPENAI_API_KEY": ""}, path)

    import os

    assert "OPENAI_API_KEY" not in path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in os.environ


def test_the_key_is_masked_for_display():
    assert mask_secret("sk-abcdefghijklmnop") == "sk-abc…mnop"
    assert "sk-" not in mask_secret("sk-short")
    assert mask_secret("") == ""


# ------------------------------------------------------------------- 設定画面
@pytest.fixture
def client(tmp_path: Path, manual_root: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    db_path = tmp_path / "index.db"
    conn = db.connect(db_path)
    index_directory(conn, manual_root, ocr="never", workers=1)
    conn.close()

    app = create_app(Config(root=manual_root, db_path=db_path, ai=AiConfig()))
    # テストがリポジトリ直下の .env を書き換えないようにする
    app.state.env_path = str(tmp_path / ".env")
    with TestClient(app) as test_client:
        yield test_client


def test_settings_tab_is_always_available(client):
    assert 'href="/settings"' in client.get("/").text


def test_settings_page_reports_ai_as_disabled(client):
    body = client.get("/settings").text
    assert "無効" in body
    assert "OpenAI を使う" in body
    assert "ローカルのAIを使う" in body


def test_saving_a_key_enables_the_assistant_without_a_restart(client, tmp_path: Path):
    response = client.post("/settings", data={"provider": "openai", "api_key": "sk-test-123456789012"})
    assert response.status_code == 200  # リダイレクトを追った結果

    body = client.get("/settings").text
    assert "sk-test-123456789012" not in body  # 生のキーは画面に出さない

    # AIに相談タブが使える状態になっている
    assert "有効になっていません" not in client.get("/ask").text
    # .env にも書かれている
    assert "sk-test-123456789012" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_an_empty_key_keeps_the_current_one(client):
    client.post("/settings", data={"provider": "openai", "api_key": "sk-test-123456789012"})
    client.post("/settings", data={"provider": "openai", "openai_model": "gpt-4o"})

    assert "gpt-4o" in client.get("/settings").text


def test_clearing_the_settings_disables_the_assistant(client):
    client.post("/settings", data={"provider": "openai", "api_key": "sk-test-123456789012"})
    client.post("/settings", data={"clear": "1"})

    assert "無効" in client.get("/settings").text
    assert client.get("/api/ask", params={"q": "エラー"}).status_code == 503


def test_settings_page_shows_where_things_live(client, manual_root: Path):
    body = client.get("/settings").text
    assert str(manual_root) in body
    assert "OCR" in body


# ------------------------------------------------------- 一覧からの機種設定
def test_machine_can_be_set_from_the_library_screen(client, manual_root: Path):
    doc_id = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"][0][
        "document_id"
    ]
    response = client.post(
        "/library", data={f"machine_{doc_id}": "ROBODRILL", f"category_{doc_id}": "保守"}
    )
    assert response.status_code == 200

    # 検索の絞り込みに使えるようになっている
    assert client.get("/api/search", params={"q": "エラーコード", "machine": "ROBODRILL"}).json()[
        "total"
    ] >= 1
    # 選択肢にも出る
    assert "ROBODRILL" in {item["name"] for item in client.get("/api/machines").json()}


def test_editing_from_the_screen_is_written_to_the_library_file(client, manual_root: Path):
    from manualsearch import library

    doc_id = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"][0][
        "document_id"
    ]
    client.post("/library", data={f"machine_{doc_id}": "ROBODRILL"})

    entries = library.load(manual_root / "library.csv")
    assert any(entry.machine == "ROBODRILL" for entry in entries.values())


def test_unchanged_rows_are_not_counted_as_saved(client):
    doc_id = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"][0][
        "document_id"
    ]
    client.post("/library", data={f"machine_{doc_id}": "ROBODRILL"})
    response = client.post("/library", data={f"machine_{doc_id}": "ROBODRILL"})

    assert "saved=0" in str(response.url)


def test_clearing_a_machine_from_the_screen_works(client):
    hit = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"][0]
    assert hit["machine"] == "ポンプ"

    client.post("/library", data={f"machine_{hit['document_id']}": ""})

    after = client.get("/api/search", params={"q": "エラーコード"}).json()["hits"]
    assert next(h["machine"] for h in after if h["document_id"] == hit["document_id"]) == ""


def test_unknown_document_ids_are_ignored(client):
    assert client.post("/library", data={"machine_99999": "ROBODRILL"}).status_code == 200


# ------------------------------------------------------- ローカルAIサーバー
def test_local_server_can_be_enabled_without_an_api_key(client, tmp_path: Path):
    client.post("/settings", data={"provider": "local", "local_model": "qwen3:32b"})

    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MANUAL_AI_PROVIDER=local" in env
    # 接続先が空欄なら Ollama の既定を入れる
    assert "http://localhost:11434/v1" in env
    assert "qwen3:32b" in client.get("/settings").text


def test_a_custom_local_address_is_kept(client):
    client.post(
        "/settings",
        data={
            "provider": "local",
            "local_base_url": "http://192.168.1.50:11434/v1",
            "local_model": "qwen3:32b",
        },
    )
    assert "http://192.168.1.50:11434/v1" in client.get("/settings").text


def test_both_providers_survive_switching(client, tmp_path: Path):
    """片方を設定してももう片方が消えないこと。"""
    client.post("/settings", data={"provider": "openai", "api_key": "sk-test-123456789012"})
    client.post(
        "/settings",
        data={"provider": "local", "local_base_url": "http://127.0.0.1:1/v1", "local_model": "qwen3:32b"},
    )

    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-test-123456789012" in env  # OpenAIのキーは残っている
    assert "MANUAL_AI_PROVIDER=local" in env
    assert client.get("/api/ai/status").json()["local"] is True

    # OpenAIへ戻しても、ローカルの設定は残る
    client.post("/settings", data={"provider": "openai"})
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "http://127.0.0.1:1/v1" in env
    assert "qwen3:32b" in env
    assert client.get("/api/ai/status").json()["local"] is False


def test_clearing_removes_both_providers(client, tmp_path: Path):
    client.post("/settings", data={"provider": "openai", "api_key": "sk-test-123456789012"})
    client.post(
        "/settings",
        data={"provider": "local", "local_base_url": "http://127.0.0.1:1/v1", "local_model": "qwen3:32b"},
    )
    client.post("/settings", data={"clear": "1"})

    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-test-123456789012" not in env
    assert "http://127.0.0.1:1/v1" not in env
    assert "無効" in client.get("/settings").text


# ----------------------------------------------------- AIの接続状態バッジ
def test_status_says_unset_when_no_ai_is_configured(client):
    payload = client.get("/api/ai/status").json()
    assert payload["enabled"] is False
    assert payload["reachable"] is False


def test_the_badge_is_on_every_page(client):
    for path in ["/", "/library", "/settings"]:
        assert 'id="ai-badge"' in client.get(path).text


def test_status_reports_the_local_server_and_model(client, tmp_path: Path):
    """繋がっていれば、どこの何を使っているかが分かること。"""
    from manualsearch.assistant import Assistant
    from manualsearch.config import AiConfig

    class ModelListingClient:
        def __init__(self, names):
            self.models = type("M", (), {"list": lambda _self: type("R", (), {
                "data": [type("I", (), {"id": n})() for n in names]
            })()})()

    app = client.app
    app.state.ai_config = AiConfig(
        provider="local", local_base_url="http://localhost:11434/v1", local_model="qwen3:32b"
    )
    app.state.assistant = Assistant(
        app.state.ai_config, client=ModelListingClient(["qwen3:32b", "qwen3:30b-a3b"])
    )
    app.state.ai_status_cache = None

    payload = client.get("/api/ai/status").json()
    assert payload["enabled"] is True
    assert payload["local"] is True
    assert payload["where"] == "ローカル"
    assert payload["model"] == "qwen3:32b"
    assert payload["reachable"] is True
    assert payload["model_found"] is True


def test_status_flags_a_model_name_that_is_not_on_the_server(client):
    from manualsearch.assistant import Assistant
    from manualsearch.config import AiConfig

    class ModelListingClient:
        def __init__(self, names):
            self.models = type("M", (), {"list": lambda _self: type("R", (), {
                "data": [type("I", (), {"id": n})() for n in names]
            })()})()

    app = client.app
    app.state.ai_config = AiConfig(
        provider="local", local_base_url="http://localhost:11434/v1", local_model="qwen3:32b"
    )
    app.state.assistant = Assistant(app.state.ai_config, client=ModelListingClient(["llama3"]))
    app.state.ai_status_cache = None

    payload = client.get("/api/ai/status").json()
    assert payload["reachable"] is True
    assert payload["model_found"] is False
    assert payload["models"] == ["llama3"]


def test_status_reports_a_server_that_is_not_running(client):
    from manualsearch.assistant import Assistant
    from manualsearch.config import AiConfig

    class DeadClient:
        class _Models:
            def list(self):
                raise RuntimeError("connection refused")

        models = _Models()

    app = client.app
    app.state.ai_config = AiConfig(
        provider="local", local_base_url="http://localhost:11434/v1", local_model="qwen3:32b"
    )
    app.state.assistant = Assistant(app.state.ai_config, client=DeadClient())
    app.state.ai_status_cache = None

    payload = client.get("/api/ai/status").json()
    assert payload["enabled"] is True
    assert payload["reachable"] is False


def test_status_is_reused_for_a_short_while(client):
    """ページを開くたびに問い合わせないこと。"""
    from manualsearch.assistant import Assistant
    from manualsearch.config import AiConfig

    calls = []

    class CountingClient:
        class _Models:
            def __init__(self, calls):
                self.calls = calls

            def list(self):
                self.calls.append(1)
                raise RuntimeError("dead")

        def __init__(self, calls):
            self.models = self._Models(calls)

    app = client.app
    app.state.ai_config = AiConfig(
        provider="local", local_base_url="http://localhost:11434/v1", local_model="qwen3:32b"
    )
    app.state.assistant = Assistant(app.state.ai_config, client=CountingClient(calls))
    app.state.ai_status_cache = None

    client.get("/api/ai/status")
    client.get("/api/ai/status")
    assert len(calls) == 1

    client.get("/api/ai/status", params={"refresh": 1})
    assert len(calls) == 2


def test_saving_settings_clears_the_cached_status(client):
    client.get("/api/ai/status")
    client.post("/settings", data={"provider": "local", "local_model": "qwen3:32b"})
    payload = client.get("/api/ai/status").json()
    assert payload["local"] is True
    assert payload["model"] == "qwen3:32b"
