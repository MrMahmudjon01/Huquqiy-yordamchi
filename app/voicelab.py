"""VoiceLab API mijozi (https://docs.voicelab.uz): LLM (aisha-orbit ...), fayllar (rasm/PDF), STT.

Faqat app/ai.py chaqiradi. Tuzilgan javob function calling orqali olinadi: VoiceLab response_format ni
qo'llamaydi, majburiy tool_choice esa javobni aynan berilgan JSON-sxemada qaytaradi.

Token tejash:
  * reasoning_effort="low" (aisha-orbit standarti "max" — o'ylash tokenlari bir necha barobar ko'p);
  * max_tokens har doim aniq beriladi (VoiceLab oldindan shu hajmda kredit band qiladi);
  * har bir chaqiruv data/llm_usage.log ga yoziladi: model, kirish/chiqish tokenlari, taxminiy narx.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Iterator

import httpx

from . import config

log = logging.getLogger("infinity.voicelab")


class VoiceLabError(RuntimeError):
    def __init__(self, message: str, *, retry: bool = False, status: int = 0, code: str = ""):
        super().__init__(message)
        self.retry, self.status, self.code = retry, status, code


# Bu xatolarda keyingi (zaxira) modelga o'tiladi
RETRYABLE_CODES = {"model_capabilities_unavailable", "llm_model_temporarily_unavailable", "llm_unavailable",
                   "llm_upstream_canceled", "rate_limited", "llm_request_incomplete"}
REASONING = {"aisha-orbit": ("low", "high", "max"), "aisha-halo": ("low", "high", "max"),
             "aisha-gemma-4": ("none", "high")}

_client: httpx.Client | None = None
_cooldown: dict[str, float] = {}


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=config.VOICELAB_BASE_URL,
                               timeout=httpx.Timeout(config.VL_TIMEOUT_S, connect=10))
    return _client


def _headers(**extra) -> dict:
    return {"Authorization": f"Bearer {config.VOICELAB_API_KEY}", **extra}


def _error(r: httpx.Response) -> VoiceLabError:
    """Ikki xil xato ko'rinishini ham o'qiydi: {"error": {"code", "message"}} va {"message", "error": {"code"}}."""
    try:
        body = r.json()
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code", "")
    msg = err.get("message") or body.get("message") or r.text[:200]
    retry = r.status_code in (429, 502, 503, 504) or code in RETRYABLE_CODES
    return VoiceLabError(f"{r.status_code} {code}: {msg}", retry=retry, status=r.status_code, code=code)


def available(model: str) -> bool:
    return _cooldown.get(model, 0) <= time.time()


def model_status() -> dict[str, str]:
    now = time.time()
    out = {}
    for m in dict.fromkeys([config.VL_MAIN_MODEL, *config.VL_FALLBACK_MODELS, config.VL_VISION_MODEL]):
        left = int(_cooldown.get(m, 0) - now)
        out[m] = "tayyor" if left <= 0 else f"band, {left} s"
    return out


# --- JSON-sxema --------------------------------------------------------------
def tool_schema(schema_cls) -> dict:
    """Pydantic sxemasini function calling parametrlariga aylantiradi: $ref lar ochiladi, "title" lar
    olib tashlanadi (ortiqcha token)."""
    raw = schema_cls.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(raw)


# --- Token hisobi ------------------------------------------------------------
def _log_usage(kind: str, model: str, usage: dict, credits: str | None = None) -> None:
    pin, pout = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    p_in, p_out = config.VL_PRICES.get(model, (0, 0))
    cost = (pin * p_in + pout * p_out) / 1e6
    log.info("%s %s: kirish %d (kesh %d), chiqish %d (o'ylash %d), ~$%.4f", kind, model, pin, cached, pout,
             reasoning, cost)
    try:
        new = not config.USAGE_LOG.exists()
        config.USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with config.USAGE_LOG.open("a", encoding="utf-8") as f:
            if new:
                f.write("vaqt\tturi\tmodel\tkirish\tkesh\tchiqish\toylash\tnarx_usd\tkredit\n")
            f.write(f"{time.strftime('%F %T')}\t{kind}\t{model}\t{pin}\t{cached}\t{pout}\t{reasoning}"
                    f"\t{cost:.5f}\t{credits or ''}\n")
    except OSError:
        pass


# --- Fayllar (rasm/PDF) --------------------------------------------------------
def upload_files(files, model: str) -> list[str]:
    """Rasm va PDF'larni /v1/files ga yuklaydi (data: URI qabul qilinmaydi). Fayl ID lari qaytadi."""
    ids = []
    for f in files:
        purpose = "document" if f.mime_type == "application/pdf" else "vision"
        r = _http().post("/v1/files", headers=_headers(), data={"purpose": purpose, "model": model},
                         files={"file": (f.name or "fayl", f.data, f.mime_type)})
        if r.status_code not in (200, 201):
            raise _error(r)
        ids.append(r.json()["id"])
    return ids


def delete_files(ids: list[str]) -> None:
    """Kunlik limit (25 ta faol fayl) tez tugamasligi uchun ishlatilgan fayllar o'chiriladi."""
    for fid in ids:
        try:
            _http().delete(f"/v1/files/{fid}", headers=_headers(), timeout=10)
        except httpx.HTTPError:
            pass


# --- LLM ---------------------------------------------------------------------
def _body(model: str, system: str, prompt: str, file_ids: list[str], tool_name: str, params: dict,
          temperature: float, max_tokens: int, stream: bool) -> dict:
    content = prompt if not file_ids else [{"type": "text", "text": prompt}] + [
        {"type": "file", "file": {"file_id": fid}} for fid in file_ids]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "tools": [{"type": "function", "function": {"name": tool_name, "description": "Javobni shu sxemada qaytar",
                                                     "parameters": params}}],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    efforts = REASONING.get(model)
    if efforts:
        want = config.VL_REASONING if config.VL_REASONING in efforts else efforts[0]
        body["reasoning_effort"] = want
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def _parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


def _candidates(model: str | None, files) -> list[str]:
    if files:  # aisha-orbit rasm o'qimaydi — vision model, zaxira: aisha-halo
        models = [config.VL_VISION_MODEL, "aisha-halo"]
    else:
        models = [model or config.VL_MAIN_MODEL, *config.VL_FALLBACK_MODELS]
    models = list(dict.fromkeys(m for m in models if m))
    return [m for m in models if available(m)] or sorted(models, key=lambda m: _cooldown.get(m, 0))


def chat_json(*, system: str, prompt: str, schema_cls, files=None, model: str | None = None,
              temperature: float = 0.3, max_tokens: int | None = None, kind: str = "javob") -> dict:
    """Bitta so'rov: tuzilgan (JSON) javob. Band model bo'lsa zaxira modelga o'tiladi."""
    params, tool = tool_schema(schema_cls), schema_cls.__name__
    last: VoiceLabError | None = None
    for m in _candidates(model, files):
        ids: list[str] = []
        try:
            ids = upload_files(files, m) if files else []
            r = _http().post("/v1/chat/completions",
                             headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
                             json=_body(m, system, prompt, ids, tool, params, temperature,
                                        max_tokens or config.MAX_OUTPUT_TOKENS, False))
            if r.status_code != 200:
                raise _error(r)
        except VoiceLabError as e:
            last = e
            if not e.retry:
                raise
            _cooldown[m] = time.time() + 30
            log.warning("%s band (%s), keyingi model sinaladi", m, e)
            continue
        except httpx.HTTPError as e:
            last = VoiceLabError(f"Tarmoq xatosi: {e}", retry=True)
            _cooldown[m] = time.time() + 30
            continue
        finally:
            if ids:
                delete_files(ids)
        data = r.json()
        _log_usage(kind, m, data.get("usage") or {}, r.headers.get("X-Credits-Charged"))
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        for call in msg.get("tool_calls") or []:
            args = (call.get("function") or {}).get("arguments")
            try:
                return json.loads(args) if isinstance(args, str) else args
            except ValueError as e:
                raise VoiceLabError(f"Javob to'liq kelmadi ({choice.get('finish_reason')})") from e
        try:
            return _parse_json(msg.get("content"))
        except ValueError as e:
            raise VoiceLabError("Model tuzilgan javob qaytarmadi") from e
    raise last or VoiceLabError("Barcha modellar band")


def chat_json_stream(*, system: str, prompt: str, schema_cls, files=None, model: str | None = None,
                     temperature: float = 0.3, max_tokens: int | None = None, kind: str = "javob",
                     reset_marker: str = "") -> Iterator[str]:
    """Oqimli variant: JSON matnini (function arguments) bo'laklab qaytaradi.
    Birinchi bo'lakdan oldin xato bo'lsa — keyingi model; o'rtada uzilsa — reset_marker va keyingi model."""
    params, tool = tool_schema(schema_cls), schema_cls.__name__
    last: VoiceLabError | None = None
    for m in _candidates(model, files):
        ids: list[str] = []
        emitted = False
        content_buf = ""
        usage: dict = {}
        try:
            ids = upload_files(files, m) if files else []
            body = _body(m, system, prompt, ids, tool, params, temperature,
                         max_tokens or config.MAX_OUTPUT_TOKENS, True)
            with _http().stream("POST", "/v1/chat/completions", json=body,
                                headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())})) as r:
                if r.status_code != 200:
                    r.read()
                    raise _error(r)
                credits = r.headers.get("X-Credits-Charged")
                finished = False
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        finished = True
                        break
                    try:
                        ev = json.loads(payload)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        err = ev["error"] if isinstance(ev["error"], dict) else {"message": str(ev["error"])}
                        raise VoiceLabError(f"oqim xatosi {err.get('code', '')}: {err.get('message', '')}",
                                            retry=True, code=err.get("code", ""))
                    if ev.get("usage"):
                        usage = ev["usage"]
                    for ch in ev.get("choices") or []:
                        delta = ch.get("delta") or {}
                        for tc in delta.get("tool_calls") or []:
                            piece = (tc.get("function") or {}).get("arguments") or ""
                            if piece:
                                emitted = True
                                yield piece
                        if delta.get("content"):
                            content_buf += delta["content"]
                if not finished and not emitted and not content_buf:
                    raise VoiceLabError("Oqim to'liq kelmadi", retry=True)
            _log_usage(kind, m, usage, credits)
            if not emitted and content_buf:  # model tool o'rniga oddiy matnda JSON yozgan bo'lsa
                text = content_buf.strip()
                if text.startswith("```"):
                    text = text.strip("`").removeprefix("json").strip()
                yield text
                emitted = True
            if emitted:
                return
            last = VoiceLabError("Bo'sh javob", retry=True)
        except VoiceLabError as e:
            last = e
            if not e.retry:
                raise
            _cooldown[m] = time.time() + 30
            log.warning("%s xato berdi (%s), keyingi model sinaladi", m, e)
            if emitted and reset_marker:
                yield reset_marker
        except httpx.HTTPError as e:
            last = VoiceLabError(f"Tarmoq xatosi: {e}", retry=True)
            _cooldown[m] = time.time() + 30
            if emitted and reset_marker:
                yield reset_marker
        finally:
            if ids:
                delete_files(ids)
    raise last or VoiceLabError("Barcha modellar band")


# --- STT ---------------------------------------------------------------------
def transcribe(data: bytes, filename: str, mime: str, timeout: float = 90) -> str:
    """Audio → matn (asinxron ish + so'rov). Natija olingach audio serverdan o'chiriladi."""
    r = _http().post("/v1/stt", headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
                     files={"audio": (filename or "audio.webm", data, mime)},
                     data={"language": config.VL_STT_LANGUAGE, "include_speakers": "false"}, timeout=60)
    if r.status_code not in (200, 202):
        raise _error(r)
    job = r.json()["id"]
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            p = _http().get(f"/v1/stt/transcriptions/{job}", headers=_headers(), timeout=20)
            if p.status_code != 200:
                raise _error(p)
            d = p.json()
            if d.get("status") == "completed":
                return (d.get("transcript") or "").strip()
            if d.get("status") == "failed":
                raise VoiceLabError("STT bajarilmadi")
            time.sleep(min(float(p.headers.get("Retry-After", 1)), 1.5))
        raise VoiceLabError("STT vaqt chegarasidan oshdi")
    finally:
        try:
            _http().delete(f"/v1/stt/transcriptions/{job}", headers=_headers(), timeout=10)
        except httpx.HTTPError:
            pass
