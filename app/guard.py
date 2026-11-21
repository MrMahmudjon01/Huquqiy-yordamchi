"""AI byudjetini suiiste'moldan himoya qilish.

Pulli kalit ulanganda kimdir minglab so'rov yuborib tokenlar va pulni tugatib
qo'ymasligi uchun AI chaqiradigan har bir so'rov shu yerdan o'tadi:

* IP bo'yicha daqiqalik limit   — botlar va tez-tez bosishdan himoya;
* IP bo'yicha kunlik limit      — bitta foydalanuvchi butun byudjetni sarflay olmaydi;
* butun sayt bo'yicha kunlik limit — eng yomon holatda ham kunlik xarajat chegaralangan;
* boshqa saytlardan (Origin) kelgan so'rovlar rad etiladi.

Hisoblagichlar xotirada saqlanadi (bitta server nusxasi uchun yetarli).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import date
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from . import config

_lock = threading.Lock()
_minute: dict[str, deque] = defaultdict(deque)
_daily: dict[str, int] = defaultdict(int)
_day = date.today()
_global = 0


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:  # Railway kabi proksi orqasida haqiqiy IP shu sarlavhada
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return  # brauzer bo'lmagan mijozlar (curl) — boshqa limitlar baribir ishlaydi
    host = urlparse(origin).netloc
    allowed = {request.headers.get("host", ""), *config.ALLOWED_HOSTS}
    if host not in allowed:
        raise HTTPException(403, "So'rov ruxsat etilmagan manbadan keldi.")


class LimitError(Exception):
    """Telegram bot uchun: limit oshdi (HTTPException o'rniga)."""


def check(request: Request, cost: int = 1) -> None:
    """AI chaqiruvidan oldin chaqiriladi. Limit oshsa HTTPException ko'taradi."""
    _check_origin(request)
    try:
        check_key(client_ip(request), cost)
    except LimitError as e:
        raise HTTPException(*e.args) from None


def check_key(ip: str, cost: int = 1) -> None:
    """Kalit (IP yoki "tg:<id>") bo'yicha daqiqalik, kunlik va umumiy limitlar. Oshsa LimitError(status, matn)."""
    global _day, _global
    now = time.time()
    with _lock:
        if date.today() != _day:  # yangi kun — kunlik hisoblagichlar nolga
            _day, _global = date.today(), 0
            _daily.clear()

        q = _minute[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= config.RATE_LIMIT_PER_MINUTE:
            raise LimitError(429, "Juda ko'p so'rov. Bir daqiqadan so'ng urinib ko'ring.")
        if _daily[ip] + cost > config.DAILY_LIMIT_PER_IP:
            raise LimitError(429, "Bugungi so'rovlar limiti tugadi. Ertaga qayta urinib ko'ring.")
        if _global + cost > config.GLOBAL_DAILY_LIMIT:
            raise LimitError(503, "Xizmat bugun juda band. Iltimos, ertaga qayta urinib ko'ring.")

        q.append(now)
        _daily[ip] += cost
        _global += cost


def stats() -> dict:
    with _lock:
        return {"bugun_jami": _global, "global_limit": config.GLOBAL_DAILY_LIMIT, "ip_soni": len(_daily)}


def limit_text(value: str, max_chars: int, what: str) -> str:
    value = (value or "").strip()
    if len(value) > max_chars:
        raise HTTPException(400, f"{what} juda uzun (ko'pi bilan {max_chars} belgi).")
    return value
