"""社内向けの検索画面（FastAPI）。

``python -m manualsearch serve`` で起動して、ブラウザから使う。認証は付けていないので、
社内LANの外に出す場合はリバースプロキシ側で保護すること。

画面は3つ。

- ``/``         機種を選んでキーワード検索する
- ``/ask``      機種を選んで自然文で相談する（ChatGPT API）
- ``/library``  登録済みマニュアルの一覧
- ``/settings`` APIキーなどの設定。画面から保存でき、再起動なしで反映する
"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import __version__, db, library, search as search_mod
from .assistant import Assistant, AssistantError, HealthCheck, link_citations
from .config import (
    DEFAULT_LOCAL_BASE_URL,
    ENV_FILENAME,
    AiConfig,
    Config,
    mask_secret,
    save_env_file,
)
from .extract import ocr_status
from .library import LibraryEntry

PER_PAGE = 30
# AIの接続状態を確かめる間隔。毎回問い合わせると画面が重くなるので少し寝かせる。
AI_STATUS_TTL = 15.0
# 選んだ機種を覚えておく期間（毎回選び直さなくて済むように）
MACHINE_COOKIE = "manual_machine"
MACHINE_COOKIE_MAX_AGE = 60 * 60 * 24 * 90

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="マニュアル検索", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # 出典番号をリンクに変えたHTMLを、テンプレート側で |safe を書かずに使えるようにする
    templates.env.filters["cite"] = lambda answer: Markup(link_citations(answer))
    app.state.config = config
    app.state.ai_config = config.ai
    app.state.assistant = Assistant(config.ai) if config.ai.enabled else None
    # 設定画面から書き込む .env の場所（起動したフォルダの直下）
    app.state.env_path = ENV_FILENAME
    app.state.ai_status_cache = None

    def get_conn() -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(app.state, "conn", None)
        if conn is None:
            conn = db.connect(config.db_path, read_only=True)
            app.state.conn = conn
        return conn

    @app.on_event("shutdown")
    def _close() -> None:
        conn = getattr(app.state, "conn", None)
        if conn is not None:
            conn.close()

    def ai_status(force: bool = False) -> dict:
        """AIに繋がっているか、どのモデルかを返す。

        推論はせず、モデル一覧を引くだけの軽い確認にとどめる。結果は少しの間
        使い回して、ページを開くたびに問い合わせないようにする。
        """
        cache = app.state.ai_status_cache
        now = time.monotonic()
        if not force and cache and now - cache[0] < AI_STATUS_TTL:
            return cache[1]

        config: AiConfig = app.state.ai_config
        assistant: Assistant | None = app.state.assistant
        status = {
            "enabled": assistant is not None,
            "local": config.is_local,
            "model": config.model,
            "base_url": config.base_url,
            "where": config.where,
            "reachable": False,
            "model_found": None,
            "models": [],
        }

        # モデルが未入力でも、接続先さえ分かればサーバーの中身は聞ける。
        # その一覧を設定画面で選ばせたいので、ここでは enabled を条件にしない。
        prober = assistant if assistant is not None else (
            Assistant(config) if config.can_probe else None
        )
        if prober is not None:
            models = prober.list_models()
            status["models"] = models
            status["reachable"] = bool(models)
            if models and config.model:
                status["model_found"] = config.model in models

        status["missing"] = config.missing
        status["can_probe"] = config.can_probe

        app.state.ai_status_cache = (now, status)
        return status

    def base_context(
        request: Request, conn: sqlite3.Connection, machine: str = "", tab: str = "search"
    ) -> dict:
        machines = search_mod.list_machines(conn)
        known = {item.name for item in machines}
        # 覚えている機種が索引から消えていたら「すべて」に戻す
        selected = machine if machine in known else ""
        if not machine:
            # Cookieはlatin-1しか載らないので、日本語の機種名はURLエンコードして保存する
            remembered = unquote(request.cookies.get(MACHINE_COOKIE, ""))
            selected = remembered if remembered in known else ""
        return {
            "request": request,
            "stats": db.stats(conn),
            "machines": machines,
            "machine": selected,
            "ai_enabled": app.state.assistant is not None,
            "ai_configurable": True,
            "tab": tab,
            "version": __version__,
            "root": str(config.root),
        }

    def remember_machine(response, machine: str):
        """選んだ機種をブラウザに覚えさせる。"""
        if machine:
            response.set_cookie(
                MACHINE_COOKIE,
                quote(machine),
                max_age=MACHINE_COOKIE_MAX_AGE,
                samesite="lax",
            )
        return response

    # ------------------------------------------------------------------ 検索
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        return templates.TemplateResponse(request, "index.html", base_context(request, conn))

    @app.get("/search", response_class=HTMLResponse)
    def search_page(
        request: Request,
        q: str = Query("", description="検索語"),
        machine: str = Query(""),
        page: int = Query(1, ge=1),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        context = base_context(request, conn, machine)
        context.update({"q": q, "page": page})

        if not q.strip():
            return remember_machine(
                templates.TemplateResponse(request, "index.html", context), context["machine"]
            )

        result = search_mod.search(
            conn,
            q,
            limit=PER_PAGE,
            offset=(page - 1) * PER_PAGE,
            machine=context["machine"] or None,
        )
        context.update(
            {
                "result": result,
                "groups": result.by_document(),
                "total_pages": max(1, math.ceil(result.total / PER_PAGE)),
            }
        )
        return remember_machine(
            templates.TemplateResponse(request, "results.html", context), context["machine"]
        )

    # ------------------------------------------------------------------ 相談
    @app.get("/ask", response_class=HTMLResponse)
    def ask_page(
        request: Request,
        q: str = Query(""),
        machine: str = Query(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        context = base_context(request, conn, machine, tab="ask")
        context["q"] = q

        # キー未設定のときはテンプレート側が設定画面へ案内する
        assistant: Assistant | None = app.state.assistant
        if assistant is not None and q.strip():
            try:
                context["answer"] = assistant.ask(conn, q, machine=context["machine"] or None)
            except AssistantError as exc:
                context["ai_error"] = str(exc)

        return remember_machine(
            templates.TemplateResponse(request, "ask.html", context), context["machine"]
        )

    @app.get("/api/ask")
    def api_ask(
        q: str = Query(""),
        machine: str = Query(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        assistant: Assistant | None = app.state.assistant
        if assistant is None:
            raise HTTPException(status_code=503, detail="ChatGPT連携が無効です")
        try:
            answer = assistant.ask(conn, q, machine=machine or None)
        except AssistantError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return JSONResponse(
            {
                "question": answer.question,
                "answer": answer.answer,
                "keywords": answer.keywords,
                "model": answer.model,
                "sources": [
                    {
                        "number": source.number,
                        "document_id": source.document_id,
                        "title": source.title,
                        "machine": source.machine,
                        "page": source.page_no,
                        "url": source.url,
                    }
                    for source in answer.sources
                ],
            }
        )

    # ------------------------------------------------------------------ 設定
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(
        request: Request,
        saved: str = Query(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        return templates.TemplateResponse(
            request, "settings.html", {**settings_context(request, conn), "saved": saved}
        )

    @app.post("/settings/test", response_class=HTMLResponse)
    def test_connection(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        """接続先が本当に使えるかを、実際に問い合わせて確かめる。"""
        assistant: Assistant | None = app.state.assistant
        check = (
            assistant.check(conn)
            if assistant is not None
            else HealthCheck(detail="先にAPIキーか接続先を保存してください。")
        )
        return templates.TemplateResponse(
            request, "settings.html", {**settings_context(request, conn), "check": check}
        )

    @app.post("/settings")
    def save_settings(
        request: Request,
        provider: str = Form(""),
        api_key: str = Form(""),
        openai_model: str = Form(""),
        local_base_url: str = Form(""),
        local_model: str = Form(""),
        clear: str = Form(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        """OpenAIとローカルの設定を両方とも保存し、provider でどちらを使うか決める。"""
        values: dict[str, str] = {}

        if clear:
            # 「AI相談をやめる」。両方の設定を消す。
            values["OPENAI_API_KEY"] = ""
            values["OPENAI_BASE_URL"] = ""
            values["MANUAL_LOCAL_MODEL"] = ""
        else:
            if provider in ("openai", "local"):
                values["MANUAL_AI_PROVIDER"] = provider
            # 空欄で保存したときに今の値を消さない（キーは伏せ字で表示しているため）
            if api_key.strip():
                values["OPENAI_API_KEY"] = api_key.strip()
            if openai_model.strip():
                values["MANUAL_AI_MODEL"] = openai_model.strip()
            if local_base_url.strip():
                values["OPENAI_BASE_URL"] = local_base_url.strip()
            elif provider == "local":
                values["OPENAI_BASE_URL"] = DEFAULT_LOCAL_BASE_URL
            if local_model.strip():
                values["MANUAL_LOCAL_MODEL"] = local_model.strip()

        if values:
            save_env_file(values, app.state.env_path)
            # 再起動せずに反映する
            app.state.ai_config = AiConfig.from_env()
            app.state.assistant = (
                Assistant(app.state.ai_config) if app.state.ai_config.enabled else None
            )
            app.state.ai_status_cache = None

        return RedirectResponse(url="/settings?saved=1", status_code=303)

    def settings_context(request: Request, conn: sqlite3.Connection) -> dict:
        ai: AiConfig = app.state.ai_config
        ocr_available, ocr_detail = ocr_status()
        return {
            **base_context(request, conn, tab="settings"),
            "ai": ai,
            "status": ai_status(),
            "default_local_url": DEFAULT_LOCAL_BASE_URL,
            "api_key_masked": mask_secret(ai.api_key),
            "ocr_available": ocr_available,
            "ocr_detail": ocr_detail,
            "db_path": str(config.db_path),
            "library_path": str(config.library_path),
            "env_path": str(Path(app.state.env_path).resolve()),
        }

    # ------------------------------------------------------------------ 一覧
    @app.get("/library", response_class=HTMLResponse)
    def library_page(
        request: Request,
        machine: str = Query(""),
        saved: str = Query(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        context = base_context(request, conn, machine, tab="library")
        context["documents"] = search_mod.list_documents(conn, machine=context["machine"] or None)
        context["library_path"] = str(config.library_path)
        context["saved"] = saved
        return templates.TemplateResponse(request, "library.html", context)

    @app.post("/library")
    async def save_library(request: Request):
        """一覧の画面で編集した機種・分類を保存する。

        DBだけでなく library.csv にも書くので、索引を作り直しても設定が残る。
        """
        form = await request.form()
        machines: dict[int, str] = {}
        categories: dict[int, str] = {}
        for key, value in form.items():
            field, _, raw_id = str(key).partition("_")
            if not raw_id.isdigit():
                continue
            if field == "machine":
                machines[int(raw_id)] = str(value).strip()
            elif field == "category":
                categories[int(raw_id)] = str(value).strip()

        # 画面からの書き込みなので、読み取り専用ではない接続を別に開く
        write_conn = db.connect(config.db_path)
        try:
            entries = library.load(config.library_path)
            changed = 0
            for doc_id, machine in machines.items():
                row = search_mod.get_document(write_conn, doc_id)
                if row is None:
                    continue
                category = categories.get(doc_id, row["category"])
                if row["machine"] == machine and row["category"] == category:
                    continue

                db.update_metadata(
                    write_conn,
                    doc_id,
                    title=row["title"],
                    machine=machine,
                    category=category,
                    tags=row["tags"],
                    note=row["note"],
                )
                entry = entries.get(row["path"]) or LibraryEntry(path=row["path"])
                entry.machine = machine
                entry.category = category
                entries[row["path"]] = entry
                changed += 1

            if changed:
                library.save(
                    config.library_path, sorted(entries.values(), key=lambda e: e.path)
                )
        finally:
            write_conn.close()

        return RedirectResponse(url=f"/library?saved={changed}", status_code=303)

    @app.get("/api/search")
    def api_search(
        q: str = Query(""),
        machine: str = Query(""),
        limit: int = Query(30, ge=1, le=200),
        offset: int = Query(0, ge=0),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        result = search_mod.search(conn, q, limit=limit, offset=offset, machine=machine or None)
        return JSONResponse(
            {
                "query": result.query,
                "total": result.total,
                "took_ms": round(result.took_ms, 1),
                "hits": [
                    {
                        "document_id": hit.document_id,
                        "path": hit.path,
                        "title": hit.title,
                        "machine": hit.machine,
                        "page": hit.page_no,
                        "hits": hit.hits,
                        "snippet": hit.snippet,
                        "url": f"/file/{hit.document_id}#page={hit.page_no}",
                    }
                    for hit in result.hits
                ],
            }
        )

    @app.get("/api/ai/status")
    def api_ai_status(refresh: int = Query(0)):
        """画面の右上に出すAIの接続状態。"""
        return JSONResponse(ai_status(force=bool(refresh)))

    @app.get("/api/machines")
    def api_machines(conn: sqlite3.Connection = Depends(get_conn)):
        return [
            {"name": item.name, "documents": item.documents, "pages": item.pages}
            for item in search_mod.list_machines(conn)
        ]

    # ------------------------------------------------------------------ PDF配信
    @app.get("/file/{doc_id}")
    def serve_pdf(doc_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        row = search_mod.get_document(conn, doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="登録されていないマニュアルです")

        path = (config.root / row["path"]).resolve()
        # 索引に細工されたパスが入っていてもルート外を配信しない
        if not path.is_relative_to(config.root) or not path.is_file():
            raise HTTPException(status_code=404, detail="PDFの実体が見つかりません")

        # ブラウザ内蔵のPDFビューアで開かせる（inline なら #page=N が効く）。
        # 日本語ファイル名はRFC 5987形式でないとヘッダに載せられない。
        filename = quote(path.name)
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
        )

    @app.get("/healthz")
    def healthz(conn: sqlite3.Connection = Depends(get_conn)):
        return {"status": "ok", "ai": app.state.assistant is not None, **db.stats(conn)}

    return app
