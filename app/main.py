"""Infinity — FastAPI ilovasi."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import ai, appeal, config, db, documents, guard, lexdb, notary, rag, semantic, tgbot

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("infinity")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.con()  # jadvallar yaratiladi
    bot_task = tgbot.start_background() if config.TELEGRAM_BOT and config.TELEGRAM_BOT_TOKEN else None
    # Qonun bazasi fonda oldindan yuklanadi — birinchi foydalanuvchi kutib qolmaydi
    # Embedding modeli ham oldindan yuklanadi — birinchi savol ~10 s kutib qolmaydi
    threading.Thread(target=lambda: (rag.get_index(), lexdb.warmup(), semantic.query_vec("salom")),
                     daemon=True).start()
    yield
    if bot_task:
        await tgbot.stop(bot_task)


app = FastAPI(title="Infinity", docs_url="/api/docs" if config.SHOW_API_DOCS else None, redoc_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

ALLOWED = ("image/", "application/pdf")

# Statik fayllar versiyasi: ularning mazmunidan hisoblanadi (deploy'da fayl o'zgarsa — yangi versiya)
ASSET_VERSION = hashlib.sha1(b"".join(
    p.read_bytes() for p in sorted(config.STATIC_DIR.rglob("*")) if p.is_file()
)).hexdigest()[:10]


# --- Yordamchi funksiyalar --------------------------------------------------
async def read_files(files: list[UploadFile] | None, allowed=ALLOWED, max_mb: int | None = None) -> list[ai.FilePart]:
    files = [f for f in (files or []) if f and f.filename]
    max_mb = max_mb or config.MAX_UPLOAD_MB
    if len(files) > config.MAX_FILES:
        raise HTTPException(400, f"Ko'pi bilan {config.MAX_FILES} ta fayl yuklash mumkin.")
    out = []
    for f in files:
        mime = (f.content_type or "").split(";")[0]
        if not mime.startswith(allowed):
            raise HTTPException(400, f"{f.filename}: bu turdagi fayl qabul qilinmaydi.")
        data = await f.read(max_mb * 1024 * 1024 + 1)
        if len(data) > max_mb * 1024 * 1024:
            raise HTTPException(400, f"{f.filename}: fayl hajmi {max_mb} MB dan oshmasin.")
        out.append(ai.FilePart(data=data, mime_type=mime, name=f.filename))
    return out


SESSION_COOKIE = "infinity_sid"


def current_user(request: Request) -> dict | None:
    return db.user_by_session(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> dict | None:
    """REQUIRE_LOGIN=1 bo'lsa, AI ishlatish uchun ro'yxatdan o'tish shart (401)."""
    user = current_user(request)
    if config.REQUIRE_LOGIN and not config.MOCK_MODE and not user:
        raise HTTPException(401, "Iltimos, avval Telegram orqali ro'yxatdan o'ting (yuqoridagi «Kirish» tugmasi).")
    return user


def parse_answers(raw: str) -> list[dict]:
    try:
        qa = json.loads(raw)
        return [a for a in qa if isinstance(a, dict)][:6] if isinstance(qa, list) else []
    except json.JSONDecodeError:
        return []


def sse(events) -> StreamingResponse:
    """(tur, ma'lumot) juftliklari generatorini Server-Sent Events oqimiga aylantiradi."""
    def gen():
        try:
            for kind, data in events:
                payload = {"text": data} if kind == "delta" else {"data": data}
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except ai.AIError as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception:
            log.exception("Oqimda kutilmagan xato")
            yield f"event: error\ndata: {json.dumps({'message': 'Kutilmagan xatolik. Qayta urinib ko‘ring.'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and request.query_params.get("v") == ASSET_VERSION:
        # Versiyali statik fayl o'zgarmaydi — brauzer uni qayta so'ramaydi (takroriy kirish tezlashadi)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/"):
        # Sahifalar har safar tekshiriladi — yangi versiya darhol ko'rinadi
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ai.AIError)
async def ai_error(_: Request, exc: ai.AIError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# --- API --------------------------------------------------------------------
@app.get("/api/status")
def status():
    idx = rag.get_index()
    return {
        "mock": config.MOCK_MODE,
        "provider": config.AI_PROVIDER,
        "rag_chunks": len(idx.chunks),
        **lexdb.stats(),
        **semantic.stats(),
        "rag_vectors": int(idx.has_vec.sum()) if idx.vectors is not None else 0,
        "bhm": config.BHM_SOM,
        "modellar": ai.model_status(),
    }


@app.post("/api/stt")
async def stt(request: Request, audio: UploadFile = File(...)):
    require_user(request)
    files = await read_files([audio], allowed=("audio/", "video/webm"), max_mb=config.MAX_AUDIO_MB)
    guard.check(request)
    part = files[0]
    if part.mime_type.startswith("video/"):  # ba'zi brauzerlar webm ni video deb belgilaydi
        part.mime_type = "audio/webm"
    return {"text": ai.transcribe(part), "mock": config.MOCK_MODE}


async def _appeal_input(request, description, answers, files):
    require_user(request)
    description = guard.limit_text(description, config.MAX_TEXT_CHARS, "Muammo tavsifi")
    if len(description) < 10:
        raise HTTPException(400, "Muammoni batafsilroq yozing (kamida bir-ikki gap).")
    parts = await read_files(files)
    guard.check(request, cost=1 + len(parts))  # rasm/PDF qimmatroq — ko'proq "narx"
    return dict(description=description, answers=parse_answers(answers), files=parts)


@app.post("/api/appeal/draft")
async def appeal_draft(request: Request, description: str = Form(...), answers: str = Form("[]"),
                       files: list[UploadFile] | None = File(None)):
    return appeal.draft(**await _appeal_input(request, description, answers, files))


@app.post("/api/appeal/draft/stream")
async def appeal_draft_stream(request: Request, description: str = Form(...), answers: str = Form("[]"),
                              files: list[UploadFile] | None = File(None)):
    return sse(appeal.draft_stream(**await _appeal_input(request, description, answers, files)))


@app.get("/api/appeal/templates")
def appeal_templates():
    return [{k: t[k] for k in ("id", "nomi", "icon", "guruh", "turi")} for t in appeal.templates()]


@app.get("/api/appeal/templates/{template_id}")
def appeal_template(template_id: str):
    result = appeal.from_template(template_id)
    if not result:
        raise HTTPException(404, "Shablon topilmadi.")
    return result


@app.post("/api/appeal/docx")
async def appeal_docx(data: str = Form(...), files: list[UploadFile] | None = File(None)):
    payload = json.loads(guard.limit_text(data, 20000, "Hujjat"))
    images = [f for f in await read_files(files) if f.mime_type.startswith("image/")]
    content = documents.build_docx(payload, images)
    name = f"{payload.get('turi', 'murojaat')}.docx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@app.get("/api/appeal/categories")
def appeal_categories():
    return appeal.organs()


async def _question_input(request: Request) -> tuple[str, list[dict]]:
    require_user(request)
    body = await request.json()
    q = guard.limit_text(body.get("question") or "", config.MAX_QUESTION_CHARS, "Savol")
    if not q:
        raise HTTPException(400, "Savolni yozing.")
    history = [h for h in (body.get("history") or []) if isinstance(h, dict)][-6:]
    guard.check(request)
    return q, history


@app.post("/api/notary/ask")
async def notary_ask(request: Request):
    q, history = await _question_input(request)
    return notary.answer(q, history)


@app.post("/api/notary/ask/stream")
async def notary_ask_stream(request: Request):
    q, history = await _question_input(request)
    return sse(notary.answer_stream(q, history))


@app.post("/api/notary/explain")
async def notary_explain(request: Request, files: list[UploadFile] = File(...)):
    require_user(request)
    parts = await read_files(files)
    if not parts:
        raise HTTPException(400, "Hujjat rasmini yoki PDF faylini yuklang.")
    guard.check(request, cost=1 + len(parts))
    return notary.explain(parts)


# --- Ro'yxatdan o'tish va kirish (Telegram orqali) -------------------------------
@app.get("/api/me")
def me(request: Request):
    u = current_user(request)
    return {"user": {"name": u["name"], "username": u["tg_username"]} if u else None,
            "require_login": config.REQUIRE_LOGIN and not config.MOCK_MODE, "bot": tgbot.username()}


@app.post("/api/auth/telegram/start")
def auth_start():
    if not tgbot.username():
        raise HTTPException(503, "Telegram bot hozir ishlamayapti.")
    code = db.new_login_code()
    return {"code": code, "url": f"https://t.me/{tgbot.username()}?start=login_{code}"}


@app.get("/api/auth/telegram/poll")
def auth_poll(code: str, request: Request):
    token = db.claim_login_code(code)
    if not token:
        return {"ok": False}
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, max_age=db.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# --- Suhbatlar tarixi va yagona chat (savol-javob + hujjat tushuntirish) ----------
@app.get("/api/chats")
def chats(request: Request):
    u = current_user(request)
    return db.list_chats(u["id"]) if u else []


@app.get("/api/chats/{chat_id}")
def chat_messages(chat_id: int, request: Request):
    u = current_user(request)
    if not u or not db.get_chat(chat_id, u["id"]):
        raise HTTPException(404, "Suhbat topilmadi.")
    return {"chat": db.get_chat(chat_id, u["id"]), "messages": db.messages(chat_id)}


@app.delete("/api/chats/{chat_id}")
def chat_delete(chat_id: int, request: Request):
    u = current_user(request)
    if not u or not db.delete_chat(chat_id, u["id"]):
        raise HTTPException(404, "Suhbat topilmadi.")
    return {"ok": True}


def chat_events(user: dict | None, chat_id: int | None, question: str, parts: list[ai.FilePart], history: list[dict]):
    """Yagona chat: fayl bo'lsa — hujjat tushuntirish (+ savolga javob), aks holda savol-javob (oqim bilan).
    Ro'yxatdan o'tgan foydalanuvchi uchun savol va javob tarixga yoziladi."""
    if parts:
        res = {**notary.explain(parts, question, history), "tur": "hujjat"}
        if chat_id:
            db.add_message(chat_id, "assistant", res.get("savolga_javob") or res.get("qisqacha", ""), res)
        yield "done", {**res, "chat_id": chat_id}
        return
    for kind, data in notary.answer_stream(question, history):
        if kind == "done":
            data = {**data, "tur": "javob", "chat_id": chat_id}
            if chat_id:
                db.add_message(chat_id, "assistant", data.get("javob", ""), data)
        yield kind, data


@app.post("/api/chat/stream")
async def chat_stream(request: Request, question: str = Form(""), chat_id: int | None = Form(None),
                      files: list[UploadFile] | None = File(None)):
    user = require_user(request)
    question = guard.limit_text(question, config.MAX_QUESTION_CHARS, "Savol")
    parts = await read_files(files)
    if not question and not parts:
        raise HTTPException(400, "Savol yozing yoki hujjat yuklang.")
    guard.check(request, cost=1 + len(parts))
    history: list[dict] = []
    if user:
        if chat_id and db.get_chat(chat_id, user["id"]):
            history = db.history_for_ai(chat_id, turns=config.HISTORY_TURNS)
        else:
            title = question or ("Hujjat: " + (parts[0].name or "fayl"))
            chat_id = db.create_chat(user["id"], title)
        names = [p.name for p in parts]
        db.add_message(chat_id, "user", question or "📄 " + ", ".join(names), {"fayllar": names} if names else None)
    else:
        chat_id = None
    return sse(chat_events(user, chat_id, question, parts, history))


# --- Sahifalar --------------------------------------------------------------
PAGES = {"/": "index.html", "/murojaat": "murojaat.html", "/notarius": "notarius.html", "/narxlar": "narxlar.html"}


def _page(name: str):
    # HTML ichidagi /static/... havolalariga ?v=<versiya> qo'shiladi; fayllar o'zgarsa versiya ham o'zgaradi
    def serve():
        html = re.sub(r'(/static/[\w./-]+\.(?:css|js))"', rf'\1?v={ASSET_VERSION}"',
                      (config.STATIC_DIR / name).read_text(encoding="utf-8"))
        return HTMLResponse(html)
    return serve


for _path, _name in PAGES.items():
    app.add_api_route(_path, _page(_name), methods=["GET"], include_in_schema=False)

FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="4" fill="#0064a9"/>'
    '<text x="16" y="23" font-size="20" text-anchor="middle" fill="white" font-family="Georgia" font-weight="bold">§</text></svg>'
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(FAVICON, media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
