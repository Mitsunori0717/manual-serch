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
import time
from dataclasses import dataclass, field

from .config import AiConfig
from .query import make_snippet, parse_query
from .search import search
from .textnorm import normalize_line

# 1ページ分の本文をモデルに渡すときの上限。長いページで文脈を食い潰さないため。
MAX_SOURCE_CHARS = 1500

# 会話履歴として持ち回る往復数
MAX_HISTORY_TURNS = 6

# 「繋がっているか」を見るだけの問い合わせに使う待ち時間。
# 回答用の待ち時間（既定60秒）をそのまま使うと、サーバーが落ちているときに
# 画面がその間ずっと固まってしまう。
PROBE_TIMEOUT = 3.0

# 検索語を作らせる問い合わせの出力上限。JSONを1行返すだけなので短くてよく、
# 上限を切っておくと、話し始めたモデルが延々と続けて待たされることがなくなる。
PLANNER_MAX_TOKENS = 400

# ローカルの推論モデル（Qwen3 など）は、既定で「考えている文章」を長々と出してから
# 答え始める。渡した抜粋から引用して答える作業に長考は要らないうえ、待ち時間が
# 数倍になるので切っておく。これは Qwen3 のチャットテンプレートが解釈する合言葉で、
# 対応していないモデルにとっては意味のない一語として読み飛ばされる。
NO_THINK = "/no_think"

_PLANNER_SYSTEM = """\
あなたは日本語の技術マニュアル検索を補助するアシスタントです。
利用者の質問に答えるために全文検索エンジンへ投げるべき検索語を考えてください。

この検索エンジンは文字列がそのまま載っているページしか返しません。意味の近さでは
当たらないので、**マニュアルにその字面で書かれていそうな語**を選ぶことが重要です。

規則:
- 出力はJSONのみ。形式は {"queries": ["検索語1", "検索語2", "検索語3"]}
- 各検索語は1〜3語の名詞で構成する。助詞や「どうすれば」などの疑問表現は入れない。
- 型番・エラーコード・部品名があれば必ず含める。
- 話し言葉は、マニュアルで使われる書き言葉に置き換える
  （例: "動かない" → "起動しない" / "異常" 、"変な音" → "異音"）。
- 表記が揺れそうな場合は、言い換えを別の検索語として並べる（例: "冷却ファン" と "送風機"）。
- 1つの検索語は長くしすぎない。3〜6文字程度の語のほうが当たりやすい。
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
    where: str = ""  # "ローカル" か "OpenAI"。どれが答えたか画面に出すため。
    planned_by_llm: bool = True
    # 段階ごとの所要時間（ミリ秒）。遅いときにどこが遅いのかを画面で見せるため。
    plan_ms: float = 0.0
    search_ms: float = 0.0
    answer_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.plan_ms + self.search_ms + self.answer_ms


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


@dataclass
class HealthCheck:
    """接続テストの結果。"""

    reachable: bool = False
    detail: str = ""
    latency_ms: float = 0.0
    context_ok: bool | None = None
    context_ms: float = 0.0
    context_chars: int = 0
    models: list[str] = field(default_factory=list)
    model_found: bool | None = None

    @property
    def ok(self) -> bool:
        return self.reachable and self.context_ok is not False


# 接続テストで「先頭が切り捨てられていないか」を見るための合言葉
_PROBE_TOKEN = "XK7Q2"
_PROBE_HEAD = f"確認コードは {_PROBE_TOKEN} です。この行を必ず覚えておいてください。\n\n"
_PROBE_TAIL = (
    "\n\n上の文章のいちばん先頭に書かれている確認コードだけを、"
    "他には何も付けずに答えてください。"
)


def widen_keywords(keywords: list[str], question: str) -> list[str]:
    """最初の検索語が全部外れたときに試す、別の切り口の検索語。

    全文検索は「マニュアルにその文字列があるか」しか見ないので、言い回しが
    合っていないと1件も返らない。そこで、複合語を単語に割ったものと、質問文から
    直接切り出した語を足して、もう一度だけ広げて探す。
    """
    seen = {k for k in keywords}
    widened: list[str] = []

    def add(word: str) -> None:
        word = word.strip()
        if len(word) >= 2 and word not in seen:
            seen.add(word)
            widened.append(word)

    # 「冷却ファン 異音」のような複合をばらす
    for keyword in keywords:
        for part in keyword.split():
            add(part)

    for word in fallback_keywords(question):
        add(word)
    return widened


def _build_client(config: AiConfig):
    if not config.can_probe:
        raise AssistantError(
            "ローカルAIの接続先を設定してください。"
            if config.is_local
            else "OpenAIのAPIキーが設定されていません。設定画面で登録してください。"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - 依存が無い環境
        raise AssistantError("openai パッケージが入っていません（pip install openai）") from exc

    kwargs = {"api_key": config.client_api_key, "timeout": config.timeout}
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

    def _system(self, prompt: str) -> str:
        """指示文。ローカルの推論モデル向けに「長考しない」指定を足す。"""
        if self.config.is_local and not self.config.local_think:
            return f"{prompt}\n{NO_THINK}"
        return prompt

    def _require_ready(self) -> None:
        """モデルまで揃っているか。揃っていなければ何が足りないかを言う。"""
        missing = self.config.missing
        if missing:
            raise AssistantError(f"{self.config.where}の{missing}が設定されていません。")

    # ------------------------------------------------------------------ 接続確認
    def list_models(self, timeout: float = PROBE_TIMEOUT) -> list[str]:
        """接続先に入っているモデル名を返す。取れなければ空。

        画面の表示に使うので、待たされないよう短めに打ち切る。
        """
        try:
            client = self.client
        except AssistantError:
            return []

        # 状態表示のための確認なので、再試行はしない（失敗は失敗として早く返す）
        with_options = getattr(client, "with_options", None)
        if with_options is not None:
            try:
                client = with_options(timeout=timeout, max_retries=0)
            except TypeError:  # pragma: no cover - SDKの版差
                pass

        try:
            try:
                data = client.models.list(timeout=timeout).data
            except TypeError:
                # 差し替えたクライアント（テスト用）は timeout を受け取らない
                data = client.models.list().data
            return sorted(item.id for item in data)
        except Exception:
            return []

    def check(self, conn: sqlite3.Connection | None = None, *, probe_chars: int = 12000) -> HealthCheck:
        """接続先が使える状態かを確かめる。

        2段階で見る。

        1. 短い質問を投げて、繋がるか・モデル名が正しいかを確かめる
        2. 実際の相談と同じくらい長い文章（既定で約1.2万文字）を投げ、その先頭に
           置いた合言葉を答えさせる

        2番目が肝心。ローカルのサーバーはコンテキスト長の既定値が小さいことが多く、
        溢れたぶんは**黙って先頭から捨てられる**。エラーにならないので、気づかないまま
        「マニュアルの内容を無視した回答」が返り続ける。合言葉を先頭に置いておけば、
        それが答えられない＝切り捨てられている、と判定できる。
        """
        result = HealthCheck()
        try:
            self._require_ready()
        except AssistantError as exc:
            result.detail = str(exc)
            return result

        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=0,
                messages=[{"role": "user", "content": "「OK」とだけ答えてください。"}],
            )
            reply = (response.choices[0].message.content or "").strip()
        except AssistantError as exc:
            result.detail = str(exc)
            return result
        except Exception as exc:
            result.detail = f"接続できませんでした: {exc}"
            return result

        result.reachable = True
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.detail = f"応答: {reply[:40]}" if reply else "応答が空でした"

        # サーバーに入っているモデル名を出す。名前の打ち間違いがいちばん多いため。
        result.models = self.list_models()
        if result.models:
            result.model_found = self.config.model in result.models

        filler = _probe_filler(conn, probe_chars)
        prompt = _PROBE_HEAD + filler + _PROBE_TAIL
        result.context_chars = len(prompt)

        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = (response.choices[0].message.content or "").upper()
        except Exception as exc:
            result.context_ok = False
            result.detail = f"長い文章を渡すと失敗しました: {exc}"
            return result

        result.context_ms = (time.perf_counter() - started) * 1000
        result.context_ok = _PROBE_TOKEN in answer
        return result

    # ------------------------------------------------------------------ 検索語
    def plan_keywords(self, question: str) -> tuple[list[str], bool]:
        """質問文から検索語を作る。``(検索語, LLMを使えたか)`` を返す。"""
        messages = [
            {"role": "system", "content": self._system(_PLANNER_SYSTEM)},
            {"role": "user", "content": question},
        ]

        # JSONモードに対応していないローカルサーバーもあるので、
        # 弾かれたら指定なしでもう一度だけ試す。
        for kwargs in ({"response_format": {"type": "json_object"}}, {}):
            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    temperature=0,
                    messages=messages,
                    max_tokens=PLANNER_MAX_TOKENS,
                    **kwargs,
                )
                queries = _parse_queries(response.choices[0].message.content or "")
            except AssistantError:
                raise
            except Exception:
                continue
            if queries:
                return queries[:4], True

        # 検索語作りで失敗しても、簡易抽出で検索まではたどり着かせる
        return fallback_keywords(question), False

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
        self._require_ready()

        started = time.perf_counter()
        keywords, planned_by_llm = self.plan_keywords(question)
        plan_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        sources = self.collect_sources(conn, keywords, max_sources=max_sources, machine=machine)

        if not sources:
            # 言い回しが合わずに1件も出ないことがあるので、一度だけ広げて探し直す
            extra = widen_keywords(keywords, question)
            if extra:
                sources = self.collect_sources(
                    conn, extra, max_sources=max_sources, machine=machine
                )
                if sources:
                    keywords = keywords + extra
        search_ms = (time.perf_counter() - started) * 1000

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
                where=self.config.where,
                planned_by_llm=planned_by_llm,
                plan_ms=plan_ms,
                search_ms=search_ms,
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system(_ANSWER_SYSTEM)}
        ]
        for past_question, past_answer in (history or [])[-MAX_HISTORY_TURNS:]:
            messages.append({"role": "user", "content": past_question})
            messages.append({"role": "assistant", "content": past_answer})
        messages.append({"role": "user", "content": _format_prompt(question, sources, machine)})

        started = time.perf_counter()
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
        answer_ms = (time.perf_counter() - started) * 1000

        return Answer(
            question=question,
            answer=text,
            keywords=keywords,
            sources=sources,
            model=self.config.model,
            where=self.config.where,
            planned_by_llm=planned_by_llm,
            plan_ms=plan_ms,
            search_ms=search_ms,
            answer_ms=answer_ms,
        )


def _probe_filler(conn: sqlite3.Connection | None, target_chars: int) -> str:
    """接続テストで使う長文。実際のマニュアル本文を使い、本番と同じ負荷にする。"""
    if conn is not None:
        rows = conn.execute(
            "SELECT text FROM pages WHERE LENGTH(text) > 50 ORDER BY LENGTH(text) DESC LIMIT 40"
        ).fetchall()
        text = "\n\n".join(row["text"] for row in rows)
        if len(text) >= target_chars:
            return text[:target_chars]
        if text:
            # 索引が小さいときは繰り返して長さを稼ぐ
            return (text * (target_chars // len(text) + 1))[:target_chars]
    return "これは接続確認のための文章です。" * (target_chars // 16 + 1)


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _parse_queries(content: str) -> list[str]:
    """モデルの返答から検索語の配列を取り出す。

    小さいモデルは ```json … ``` で囲んだり前後に説明を付けたりするので、
    最初のJSONらしき塊を拾ってから読む。
    """
    match = _JSON_BLOCK.search(content)
    if not match:
        return []
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, list):
        return []
    return [str(q).strip() for q in queries if str(q).strip()]


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
