"""マニュアルの内容についてChatGPTに相談する（RAG）。

流れは3段階。

1. 質問文から検索キーワードを作る（APIに1回問い合わせる。失敗したら簡易抽出で代用）
2. そのキーワードで既存の全文検索を回し、根拠になりそうなページを集める
3. 集めたページ本文だけを渡して答えさせる

モデルに渡すのは検索でヒットしたページの本文だけで、マニュアル全体は渡さない。
根拠が無ければ「記載が見つかりません」と答えるよう指示しているので、答えは必ず
出典ページとセットで確認できる。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from .config import AiConfig
from .query import make_snippet, parse_query
from .search import search
from .textnorm import normalize_line

# 1ページ分の本文をモデルに渡すときの上限。長いページで文脈を食い潰さないため。
MAX_SOURCE_CHARS = 1500

# 会話履歴として持ち回る往復数
MAX_HISTORY_TURNS = 6

_PLANNER_SYSTEM = """\
あなたは日本語の技術マニュアル検索を補助するアシスタントです。
利用者の質問に答えるために全文検索エンジンへ投げるべき検索語を考えてください。

規則:
- 出力はJSONのみ。形式は {"queries": ["検索語1", "検索語2", "検索語3"]}
- 各検索語は1〜3語の名詞で構成する。助詞や「どうすれば」などの疑問表現は入れない。
- 型番・エラーコード・部品名があれば必ず含める。
- 表記が揺れそうな場合は、言い換えを別の検索語として並べる（例: "冷却ファン" と "送風機"）。
- 検索語は最大4個。
"""

_ANSWER_SYSTEM = """\
あなたは社内の技術マニュアルに詳しいアシスタントです。
以下に渡す「マニュアル抜粋」だけを根拠に、日本語で簡潔に答えてください。

規則:
- 抜粋に書かれていないことは推測しない。根拠が無ければ「マニュアル内に該当する記載が
  見つかりませんでした」と述べ、代わりに確認するとよい箇所を提案する。
- 事実を述べた文の末尾には必ず [1] のように出典番号を付ける。複数なら [1][3]。
- 手順を答えるときは番号付きの箇条書きにする。
- 安全上の注意が抜粋にある場合は必ず併記する。
- 抜粋に無い型番やエラーコードを創作しない。
"""


class AssistantError(RuntimeError):
    """APIキー未設定や通信失敗など、相談機能が使えない状態。"""


@dataclass
class Source:
    """回答の根拠として渡したページ。"""

    number: int
    document_id: int
    title: str
    path: str
    machine: str
    page_no: int
    text: str
    snippet: str

    @property
    def url(self) -> str:
        return f"/file/{self.document_id}#page={self.page_no}"

    @property
    def label(self) -> str:
        return f"{self.title} P.{self.page_no}"


@dataclass
class Answer:
    question: str
    answer: str
    keywords: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    model: str = ""
    planned_by_llm: bool = True


# 助詞・語尾など、検索語として役に立たない部分
_STOP = re.compile(
    r"(について|における|ですか|でしょうか|したい|してください|するには|したら|"
    r"どうすれば|どうやって|どのように|教えて|方法|とは|です|ます|ください|"
    r"[はがのにをへとでもやかね、。？?！!「」『』（）()・…]+)"
)
_MIN_KEYWORD = 2


def fallback_keywords(question: str) -> list[str]:
    """APIを使わずに質問文から検索語を切り出す（保険用）。

    助詞や疑問表現を削って残った塊を語として扱う。精度は高くないが、
    APIが落ちていても検索だけは動く状態を保てる。
    """
    text = normalize_line(question)
    chunks = [chunk.strip() for chunk in _STOP.split(text)]
    seen: list[str] = []
    for chunk in chunks:
        if not chunk or _STOP.fullmatch(chunk):
            continue
        for word in chunk.split():
            word = word.strip()
            if len(word) >= _MIN_KEYWORD and word not in seen:
                seen.append(word)
    return seen[:4]


def _build_client(config: AiConfig):
    if not config.api_key:
        raise AssistantError(
            "OPENAI_API_KEY が設定されていません。"
            "環境変数に APIキーを入れてから起動してください。"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - 依存が無い環境
        raise AssistantError("openai パッケージが入っていません（pip install openai）") from exc

    kwargs = {"api_key": config.api_key, "timeout": config.timeout}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OpenAI(**kwargs)


class Assistant:
    """検索結果を根拠にChatGPTへ質問するクライアント。

    ``client`` を渡せば差し替えられる（テストや、OpenAI互換の別エンドポイント用）。
    """

    def __init__(self, config: AiConfig, client=None) -> None:
        self.config = config
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _build_client(self.config)
        return self._client

    # ------------------------------------------------------------------ 検索語
    def plan_keywords(self, question: str) -> tuple[list[str], bool]:
        """質問文から検索語を作る。``(検索語, LLMを使えたか)`` を返す。"""
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _PLANNER_SYSTEM},
                    {"role": "user", "content": question},
                ],
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            queries = [str(q).strip() for q in payload.get("queries", []) if str(q).strip()]
        except AssistantError:
            raise
        except Exception:
            # 検索語作りで失敗しても、簡易抽出で検索まではたどり着かせる
            return fallback_keywords(question), False

        return (queries[:4], True) if queries else (fallback_keywords(question), False)

    # ------------------------------------------------------------------ 根拠集め
    def collect_sources(
        self,
        conn: sqlite3.Connection,
        keywords: list[str],
        *,
        max_sources: int = 8,
        per_query: int = 5,
        machine: str | None = None,
    ) -> list[Source]:
        """検索語ごとに全文検索し、重複を除いて根拠ページを集める。"""
        collected: dict[tuple[int, int], Source] = {}
        for keyword in keywords:
            if not parse_query(keyword).include:
                continue
            result = search(conn, keyword, limit=per_query, machine=machine)
            for hit in result.hits:
                key = (hit.document_id, hit.page_no)
                if key in collected:
                    continue
                row = conn.execute(
                    "SELECT text FROM pages WHERE document_id = ? AND page_no = ?",
                    (hit.document_id, hit.page_no),
                ).fetchone()
                text = (row["text"] if row else "")[:MAX_SOURCE_CHARS]
                collected[key] = Source(
                    number=len(collected) + 1,
                    document_id=hit.document_id,
                    title=hit.title,
                    path=hit.path,
                    machine=hit.machine,
                    page_no=hit.page_no,
                    text=text,
                    snippet=hit.snippet,
                )
                if len(collected) >= max_sources:
                    return list(collected.values())
        return list(collected.values())

    # ------------------------------------------------------------------ 回答
    def ask(
        self,
        conn: sqlite3.Connection,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        max_sources: int = 8,
        machine: str | None = None,
    ) -> Answer:
        """質問に対して、根拠ページ付きの回答を返す。"""
        question = question.strip()
        if not question:
            raise AssistantError("質問が空です。")

        keywords, planned_by_llm = self.plan_keywords(question)
        sources = self.collect_sources(conn, keywords, max_sources=max_sources, machine=machine)

        if not sources:
            return Answer(
                question=question,
                answer=(
                    "マニュアル内に該当する記載が見つかりませんでした。"
                    f"（検索した語: {' / '.join(keywords) if keywords else 'なし'}）\n"
                    "別の言い回しや型番で試すか、そのマニュアルがスキャンPDFの場合は "
                    "`--ocr force` で索引を作り直してください。"
                ),
                keywords=keywords,
                sources=[],
                model=self.config.model,
                planned_by_llm=planned_by_llm,
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": _ANSWER_SYSTEM}]
        for past_question, past_answer in (history or [])[-MAX_HISTORY_TURNS:]:
            messages.append({"role": "user", "content": past_question})
            messages.append({"role": "assistant", "content": past_answer})
        messages.append({"role": "user", "content": _format_prompt(question, sources, machine)})

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=0.2,
                messages=messages,
            )
            text = (response.choices[0].message.content or "").strip()
        except AssistantError:
            raise
        except Exception as exc:
            raise AssistantError(f"ChatGPT APIの呼び出しに失敗しました: {exc}") from exc

        return Answer(
            question=question,
            answer=text,
            keywords=keywords,
            sources=sources,
            model=self.config.model,
            planned_by_llm=planned_by_llm,
        )


def _format_prompt(question: str, sources: list[Source], machine: str | None = None) -> str:
    blocks = []
    for source in sources:
        head = f"[{source.number}] {source.title}"
        if source.machine:
            head += f" / 機種 {source.machine}"
        head += f" / {source.page_no}ページ"
        blocks.append(f"{head}\n{source.text}")

    prompt = "マニュアル抜粋:\n\n" + "\n\n---\n\n".join(blocks)
    if machine:
        prompt += f"\n\n利用者が使っている機械: {machine}"
    return prompt + f"\n\n質問: {question}"


_CITATION = re.compile(r"\[(\d+)\]")


def link_citations(answer: Answer) -> str:
    """回答文中の ``[1]`` を該当ページへのリンクにしたHTMLを返す。"""
    import html

    by_number = {source.number: source for source in answer.sources}

    def replace(match: re.Match[str]) -> str:
        source = by_number.get(int(match.group(1)))
        if source is None:
            return match.group(0)
        return (
            f'<a class="cite" href="{source.url}" target="_blank" '
            f'title="{html.escape(source.label)}">[{source.number}]</a>'
        )

    escaped = html.escape(answer.answer)
    return _CITATION.sub(replace, escaped).replace("\n", "<br>")


def highlight_source(source: Source, question: str) -> str:
    """根拠ページのどこが引っかかったのかを示すスニペット。"""
    terms = parse_query(" ".join(fallback_keywords(question))).include
    return make_snippet(source.text, terms) if terms else source.snippet
