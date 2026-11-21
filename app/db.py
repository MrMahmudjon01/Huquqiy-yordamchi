"""Foydalanuvchilar, sessiyalar va suhbatlar tarixi (SQLite, data/app.db).

Ro'yxatdan o'tish Telegram orqali: foydalanuvchi botga /start bosadi va telefon raqamini ulashadi.
Veb-saytga kirish: sayt bir martalik kod beradi -> t.me/<bot>?start=login_<kod> -> bot kodni tasdiqlaydi
-> sayt sessiya cookie'sini oladi. Parol saqlanmaydi.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time

from . import config

_local = threading.local()
_init_lock = threading.Lock()
_ready = False

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, tg_id INTEGER UNIQUE, tg_username TEXT, name TEXT, phone TEXT,
    created REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL, expires REAL);
CREATE TABLE IF NOT EXISTS login_codes(code TEXT PRIMARY KEY, user_id INTEGER, created REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, source TEXT DEFAULT 'web', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY, chat_id INTEGER, role TEXT, text TEXT, payload TEXT, created REAL);
CREATE INDEX IF NOT EXISTS ix_chats_user ON chats(user_id, updated);
CREATE INDEX IF NOT EXISTS ix_messages_chat ON messages(chat_id, id);
"""
SESSION_DAYS = 30
LOGIN_CODE_TTL = 600  # 10 daqiqa


def con() -> sqlite3.Connection:
    global _ready
    c = getattr(_local, "con", None)
    if c is None:
        config.APP_DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.APP_DB, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        _local.con = c
    if not _ready:
        with _init_lock:
            if not _ready:
                c.executescript(SCHEMA)
                _ready = True
    return c


def _row(r) -> dict | None:
    return dict(r) if r else None


# --- Foydalanuvchilar ---------------------------------------------------------
def upsert_tg_user(tg_id: int, username: str | None, name: str | None, phone: str | None = None) -> dict:
    c, now = con(), time.time()
    with c:
        c.execute("""INSERT INTO users(tg_id, tg_username, name, phone, created, last_seen) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(tg_id) DO UPDATE SET tg_username=excluded.tg_username, name=excluded.name,
                     phone=COALESCE(excluded.phone, users.phone), last_seen=excluded.last_seen""",
                  (tg_id, username, name, phone, now, now))
    return _row(c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone())


def user_by_tg(tg_id: int) -> dict | None:
    return _row(con().execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone())


def is_registered(user: dict | None) -> bool:
    return bool(user and user.get("phone"))


# --- Veb-saytga Telegram orqali kirish -----------------------------------------
def new_login_code() -> str:
    code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    c = con()
    with c:
        c.execute("DELETE FROM login_codes WHERE created < ?", (time.time() - LOGIN_CODE_TTL,))
        c.execute("INSERT INTO login_codes(code, created) VALUES(?,?)", (code, time.time()))
    return code


def confirm_login_code(code: str, user_id: int) -> bool:
    c = con()
    with c:
        cur = c.execute("UPDATE login_codes SET user_id=? WHERE code=? AND user_id IS NULL AND created > ?",
                        (user_id, code, time.time() - LOGIN_CODE_TTL))
    return cur.rowcount == 1


def claim_login_code(code: str) -> str | None:
    """Kod bot orqali tasdiqlangan bo'lsa — sessiya tokeni (bir marta)."""
    c = con()
    r = c.execute("SELECT user_id FROM login_codes WHERE code=? AND used=0 AND user_id IS NOT NULL AND created > ?",
                  (code, time.time() - LOGIN_CODE_TTL)).fetchone()
    if not r:
        return None
    token = secrets.token_urlsafe(32)
    with c:
        c.execute("UPDATE login_codes SET used=1 WHERE code=?", (code,))
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                  (token, r["user_id"], time.time(), time.time() + SESSION_DAYS * 86400))
    return token


def user_by_session(token: str | None) -> dict | None:
    if not token:
        return None
    return _row(con().execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                                 WHERE s.token=? AND s.expires > ?""", (token, time.time())).fetchone())


def delete_session(token: str) -> None:
    c = con()
    with c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# --- Suhbatlar -------------------------------------------------------------------
def create_chat(user_id: int, title: str, source: str = "web") -> int:
    c, now = con(), time.time()
    with c:
        cur = c.execute("INSERT INTO chats(user_id, title, source, created, updated) VALUES(?,?,?,?,?)",
                        (user_id, title[:80], source, now, now))
    return cur.lastrowid


def get_chat(chat_id: int, user_id: int) -> dict | None:
    return _row(con().execute("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, user_id)).fetchone())


def list_chats(user_id: int, limit: int = 50) -> list[dict]:
    return [dict(r) for r in con().execute(
        "SELECT id, title, source, updated FROM chats WHERE user_id=? ORDER BY updated DESC LIMIT ?",
        (user_id, limit))]


def delete_chat(chat_id: int, user_id: int) -> bool:
    c = con()
    with c:
        cur = c.execute("DELETE FROM chats WHERE id=? AND user_id=?", (chat_id, user_id))
        if cur.rowcount:
            c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    return cur.rowcount == 1


def add_message(chat_id: int, role: str, text: str, payload: dict | None = None) -> None:
    c, now = con(), time.time()
    with c:
        c.execute("INSERT INTO messages(chat_id, role, text, payload, created) VALUES(?,?,?,?,?)",
                  (chat_id, role, text, json.dumps(payload, ensure_ascii=False) if payload else None, now))
        c.execute("UPDATE chats SET updated=? WHERE id=?", (now, chat_id))


def messages(chat_id: int, limit: int = 200) -> list[dict]:
    out = []
    for r in con().execute("SELECT role, text, payload, created FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                           (chat_id, limit)):
        d = dict(r)
        d["payload"] = json.loads(d["payload"]) if d["payload"] else None
        out.append(d)
    return out[::-1]


def history_for_ai(chat_id: int, turns: int = 6) -> list[dict]:
    """Model uchun suhbat konteksti: oxirgi xabarlar (yordamchi javobidan xulosa + qisqa matn)."""
    hist = []
    for m in messages(chat_id, limit=turns):
        text = m["text"]
        p = m.get("payload") or {}
        if m["role"] == "assistant" and p.get("xulosa"):
            text = f"{p['xulosa']} {p.get('javob', '')}"
        hist.append({"role": m["role"], "text": text})
    return hist
