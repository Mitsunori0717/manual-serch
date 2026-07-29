"""ChatGPT連携。APIは呼ばず、差し替えたクライアントで検証する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manualsearch import db
from manualsearch.assistant import Assistant, AssistantError, fallback_keywords, link_citations
from manualsearch.config import AiConfig
from manualsearch.indexer import index_directory


class FakeCompletions:
    def __init__(self, replies: list[str], fail: bool = False):
        self.replies = list(replies)
        self.fail = fail
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("network down")
        content = self.replies.pop(0) if self.replies else ""

        class Message:
            def __init__(self, text):
                self.content = text

        class Choice:
            def __init__(self, text):
                self.message = Message(text)

        class Response:
            def __init__(self, text):
                self.choices = [Choice(text)]

        return Response(content)


class FakeClient:
    def __init__(self, replies: list[str], fail: bool = False):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(replies, fail)


@pytest.fixture
def conn(tmp_path: Path, manual_root: Path):
    connection = db.connect(tmp_path / "index.db")
    index_directory(connection, manual_root, ocr="never", workers=1)
    yield connection
    connection.close()


def make_assistant(replies, fail=False):
    client = FakeClient(replies, fail)
    return Assistant(AiConfig(api_key="test-key", model="test-model"), client=client), client


# --------------------------------------------------------------------- 検索語
def test_fallback_keywords_strip_particles():
    words = fallback_keywords("エラーコード E203 が出たときの対処方法は？")
    assert "エラーコード" in words
    assert "E203" in words


def test_fallback_keywords_drop_question_words():
    assert "どうすれば" not in fallback_keywords("冷却ファンが止まったらどうすればいい")


def test_planner_uses_the_model_output():
    assistant, _ = make_assistant([json.dumps({"queries": ["エラーコード", "冷却ファン"]})])
    keywords, by_llm = assistant.plan_keywords("E203が出た")

    assert keywords == ["エラーコード", "冷却ファン"]
    assert by_llm is True


def test_planner_falls_back_when_the_api_fails():
    assistant, _ = make_assistant([], fail=True)
    keywords, by_llm = assistant.plan_keywords("エラーコード E203 の対処")

    assert by_llm is False
    assert "エラーコード" in keywords


# --------------------------------------------------------------------- 回答
def test_answer_is_grounded_in_retrieved_pages(conn):
    assistant, client = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "冷却ファンを確認してください[1]"]
    )
    answer = assistant.ask(conn, "E203が出たらどうすればいい？")

    assert answer.answer == "冷却ファンを確認してください[1]"
    assert answer.sources
    assert all(source.page_no > 0 for source in answer.sources)


def test_only_retrieved_page_text_is_sent_to_the_api(conn):
    assistant, client = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "回答[1]"]
    )
    assistant.ask(conn, "E203について")

    prompt = client.chat.completions.calls[-1]["messages"][-1]["content"]
    assert "マニュアル抜粋" in prompt
    assert "エラーコード" in prompt
    # 検索に引っかからない別マニュアルの本文は渡っていないこと
    assert "Maintenance schedule" not in prompt


def test_machine_scopes_the_sources(conn):
    assistant, client = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "回答[1]"]
    )
    answer = assistant.ask(conn, "エラーについて", machine="ポンプ")

    assert {source.machine for source in answer.sources} == {"ポンプ"}
    assert "利用者が使っている機械: ポンプ" in client.chat.completions.calls[-1]["messages"][-1]["content"]


def test_no_hits_returns_a_plain_answer_without_calling_the_model(conn):
    assistant, client = make_assistant([json.dumps({"queries": ["まったく存在しない語句"]})])
    answer = assistant.ask(conn, "宇宙船の操縦方法は？")

    assert answer.sources == []
    assert "見つかりませんでした" in answer.answer
    assert len(client.chat.completions.calls) == 1  # 回答用の呼び出しはしていない


def test_history_is_passed_as_previous_turns(conn):
    assistant, client = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "回答[1]"]
    )
    assistant.ask(conn, "続きの質問", history=[("前の質問", "前の回答")])

    roles = [m["role"] for m in client.chat.completions.calls[-1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_api_failure_on_the_answer_call_is_reported(conn):
    assistant, _ = make_assistant([], fail=True)
    with pytest.raises(AssistantError, match="呼び出しに失敗"):
        assistant.ask(conn, "エラーコードについて")


def test_empty_question_is_rejected(conn):
    assistant, _ = make_assistant([])
    with pytest.raises(AssistantError):
        assistant.ask(conn, "   ")


def test_missing_api_key_is_reported_clearly(conn):
    assistant = Assistant(AiConfig(api_key=""))
    with pytest.raises(AssistantError, match="APIキーが設定されていません"):
        assistant.ask(conn, "エラーコードについて")


def test_a_local_server_needs_no_api_key(conn):
    """Ollama などはキーを見ないので、接続先だけで有効として扱う。"""
    config = AiConfig(api_key="", base_url="http://localhost:11434/v1")
    assert config.enabled is True
    assert config.is_local is True


def test_a_remote_base_url_is_not_treated_as_local():
    assert AiConfig(api_key="sk-x", base_url="https://api.example.com/v1").is_local is False


def test_planner_retries_without_json_mode(conn):
    """JSONモードに対応していないローカルサーバーでも検索語を作れること。"""

    class PickyCompletions(FakeCompletions):
        def create(self, **kwargs):
            if "response_format" in kwargs:
                raise RuntimeError("this server does not support response_format")
            return super().create(**kwargs)

    assistant, client = make_assistant([])
    client.chat.completions = PickyCompletions(
        ['ここが検索語です:\n```json\n{"queries": ["冷却ファン"]}\n```']
    )

    keywords, by_llm = assistant.plan_keywords("ファンが止まった")
    assert keywords == ["冷却ファン"]
    assert by_llm is True


def test_json_wrapped_in_prose_is_still_parsed():
    from manualsearch.assistant import _parse_queries

    assert _parse_queries('答えます {"queries": ["ポンプ"]} 以上です') == ["ポンプ"]
    assert _parse_queries("JSONではありません") == []


# --------------------------------------------------------------------- 出典
def test_citations_become_links(conn):
    assistant, _ = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "冷却ファンを確認[1]"]
    )
    answer = assistant.ask(conn, "E203について")
    html = link_citations(answer)

    assert 'href="/file/' in html
    assert "#page=" in html


def test_citation_to_an_unknown_number_is_left_as_text(conn):
    assistant, _ = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "根拠のない主張[99]"]
    )
    answer = assistant.ask(conn, "E203について")

    assert "[99]" in link_citations(answer)
    assert "href" not in link_citations(answer)


def test_answer_html_is_escaped(conn):
    assistant, _ = make_assistant(
        [json.dumps({"queries": ["エラーコード"]}), "<script>alert(1)</script>"]
    )
    answer = assistant.ask(conn, "E203について")

    assert "<script>" not in link_citations(answer)


# --------------------------------------------------------------- 接続テスト
def test_health_check_reports_a_working_server(conn):
    assistant, _ = make_assistant(["OK", "XK7Q2"])
    check = assistant.check(conn)

    assert check.reachable is True
    assert check.context_ok is True
    assert check.ok is True
    assert check.context_chars > 10000


def test_health_check_detects_a_silently_truncating_server(conn):
    """コンテキストが足りないサーバーはエラーを返さず、黙って先頭を捨てる。

    そのまま使うとマニュアルを読まずに答えてしまうので、必ず気づけること。
    """
    assistant, _ = make_assistant(["OK", "確認コードは見当たりませんでした"])
    check = assistant.check(conn)

    assert check.reachable is True
    assert check.context_ok is False
    assert check.ok is False


def test_health_check_reports_an_unreachable_server(conn):
    assistant, _ = make_assistant([], fail=True)
    check = assistant.check(conn)

    assert check.reachable is False
    assert check.ok is False
    assert "接続できません" in check.detail


def test_health_check_reports_a_missing_key(conn):
    check = Assistant(AiConfig(api_key="")).check(conn)
    assert check.reachable is False
    assert "APIキー" in check.detail


def test_health_check_uses_real_manual_text(conn):
    """本番と同じ負荷で測れるよう、確認用の長文は索引の本文から作る。"""
    assistant, client = make_assistant(["OK", "XK7Q2"])
    assistant.check(conn, probe_chars=3000)

    prompt = client.chat.completions.calls[-1]["messages"][-1]["content"]
    assert "エラーコード" in prompt  # サンプルPDFの本文が入っている
    assert prompt.startswith("確認コードは")


def test_health_check_works_without_an_index():
    assistant, _ = make_assistant(["OK", "XK7Q2"])
    check = assistant.check(None, probe_chars=2000)
    assert check.context_ok is True
