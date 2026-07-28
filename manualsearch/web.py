"""社内向けの検索画面（FastAPI）。

``python -m manualsearch serve`` で起動して、ブラウザから使う。認証は付けていないので、
社内LANの外に出す場合はリバースプロキシ側で保護すること。

画面は3つ。

- ``/``        機種を選んでキーワード検索する
- ``/ask``     機種を選んで自然文で相談する（ChatGPT API。キーが無ければ非表示）
- ``/library`` 登録済みマニュアルの一覧
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import db, search as search_mod
from .assistant import Assistant, AssistantError, link_citations
from .config import Config

PER_PAGE = 30
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
    app.state.assistant = Assistant(config.ai) if config.ai.enabled else None

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
            "tab": tab,
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

        assistant: Assistant | None = app.state.assistant
        if assistant is None:
            context["ai_error"] = (
                "ChatGPT連携が無効です。OPENAI_API_KEY を設定して起動し直してください。"
            )
        elif q.strip():
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

    # ------------------------------------------------------------------ 一覧
    @app.get("/library", response_class=HTMLResponse)
    def library_page(
        request: Request,
        machine: str = Query(""),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        context = base_context(request, conn, machine, tab="library")
        context["documents"] = search_mod.list_documents(conn, machine=context["machine"] or None)
        context["library_path"] = str(config.library_path)
        return templates.TemplateResponse(request, "library.html", context)

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
