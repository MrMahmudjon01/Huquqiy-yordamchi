"""AI provayder qatlami.

Modullar (appeal, notary, rag) faqat shu fayldagi funksiyalarni chaqiradi. Provayder config.AI_PROVIDER
bo'yicha tanlanadi: VoiceLab (app/voicelab.py, asosiy) yoki Gemini (shu faylda, zaxira).
"""
from __future__ import annotations

import json

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterator, TypeVar

from pydantic import BaseModel

from . import config, voicelab

log = logging.getLogger("infinity.ai")
T = TypeVar("T", bound=BaseModel)

_client = None


class AIError(RuntimeError):
    """Foydalanuvchiga ko'rsatiladigan AI xatosi."""


def _gemini_ready() -> bool:
    return bool(config.GEMINI_API_KEY)


def _use_voicelab(files) -> bool:
    """VoiceLab ishlatiladimi? Rasm/PDF standart holatda Gemini orqali o'tadi (FILES_PROVIDER=gemini):
    Gemini faylni to'g'ridan-to'g'ri qabul qiladi, VoiceLab esa avval o'z omboriga yuklashni talab qiladi."""
    if not config.VOICELAB:
        return False
    if files and config.FILES_PROVIDER == "gemini" and _gemini_ready():
        return False
    return True


def _gemini_fallback_ok(e) -> bool:
    """VoiceLab ishlamasa Gemini'ga o'tish mumkinmi (kalit bor, sozlamada yoqilgan, xato vaqtinchalik yoki javob buzuq)."""
    if not (config.GEMINI_FALLBACK and _gemini_ready()):
        return False
    return e is None or e.retry or e.status in (402, 413, 422)


def _gemini_model(model: str | None) -> str | None:
    """VoiceLab model nomi (aisha-...) Gemini'ga uzatilmaydi."""
    return None if model and model.startswith("aisha") else model


def _vl_message(e: "voicelab.VoiceLabError") -> str:
    """VoiceLab xatosini foydalanuvchiga tushunarli matnga aylantiradi."""
    if e.status == 402 or "credit" in e.code:
        return "AI xizmati hisobida mablag' tugadi. Administratorga murojaat qiling."
    if e.status in (401, 403):
        return "AI xizmati kaliti noto'g'ri yoki ruxsat yetarli emas."
    if e.status == 413:
        return "So'rov juda katta. Matnni yoki fayllarni qisqartiring."
    if e.retry:
        return "AI xizmati hozir band. Birozdan so'ng qayta urinib ko'ring."
    return "AI xizmatiga ulanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring."


# stream_json shu belgini qaytarsa: oqim o'rtada uzildi, javob boshqa modeldan qaytadan keladi
RESET = "\x00RESET\x00"


@dataclass
class FilePart:
    data: bytes
    mime_type: str
    name: str = ""


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        # SDK ning o'z qayta urinishlari o'chiriladi (ular limit tugaganda daqiqalab kutadi);
        # qayta urinish va zaxira modelga o'tishni _call boshqaradi.
        _client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=config.AI_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    return _client


# Limiti tugagan modellar: model -> qaysi vaqtgacha chetlab o'tiladi (time.time())
_cooldown: dict[str, float] = {}


def _cooldown_for(e: Exception) -> float:
    """Xato turiga qarab modelni necha soniya chetlab o'tish kerakligi (0 — chetlamaslik)."""
    s = str(e)
    if "PerDay" in s:  # kunlik limit tugagan — bir soatdan keyin qayta tekshiramiz
        return 3600
    if "429" in s or "RESOURCE_EXHAUSTED" in s:  # daqiqalik limit
        return 60
    if "503" in s or "UNAVAILABLE" in s or "504" in s or "DEADLINE" in s or _is_timeout(e):
        return 30  # model band
    return 0


def _is_timeout(e: Exception) -> bool:
    return "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()


def model_status() -> dict[str, str]:
    """Har bir model holati (/api/status uchun): tayyor yoki qancha vaqt chetlab o'tilishi."""
    if config.VOICELAB:
        return voicelab.model_status()
    now = time.time()
    models = dict.fromkeys([config.MAIN_MODEL, config.FAST_MODEL, *config.FALLBACK_MODELS, config.EMBED_MODEL])
    out = {}
    for m in models:
        left = int(_cooldown.get(m, 0) - now)
        out[m] = "tayyor" if left <= 0 else ("limit tugagan" if left > 600 else "band") + f", {left} s"
    return out


def model_available(model: str) -> bool:
    if config.VOICELAB:
        return voicelab.available(model)
    return _cooldown.get(model, 0) <= time.time()


def _call(fn, models: list[str], attempts: int = 1, delay: float = 1.5):
    """fn(model) ni chaqiradi. Model band yoki limiti tugagan bo'lsa, kutmasdan keyingi
    zaxira modelga o'tadi; bunday model bir muddat chetlab o'tiladi."""
    last: Exception | None = None
    candidates = list(dict.fromkeys(m for m in models if m))
    # hammasi band bo'lsa — eng tez bo'shaydiganidan boshlab hammasini sinaymiz
    ready = [m for m in candidates if model_available(m)] or sorted(candidates, key=lambda m: _cooldown.get(m, 0))
    for model in ready:
        for i in range(attempts):
            try:
                return fn(model)
            except Exception as e:
                last = e
                wait = _cooldown_for(e)
                if not wait:
                    raise
                if wait >= 3600 or _is_timeout(e) or i == attempts - 1:
                    _cooldown[model] = time.time() + wait
                    log.warning("%s vaqtincha chetlab o'tiladi (%ds): %s", model, wait, str(e)[:70])
                    break
                time.sleep(delay * (i + 1))
    raise last  # type: ignore[misc]


# "O'ylash" darajasini qo'llab-quvvatlamaydigan modellar (birinchi rad javobidan keyin eslab qolinadi)
_no_thinking: set[str] = set()


def _gen_config(model: str, *, system: str, schema, temperature: float, max_tokens: int | None = None):
    """Generatsiya sozlamalari. THINKING_LEVEL=LOW javobni bir necha barobar tezlashtiradi
    (masalan, gemini-3.6-flash: birinchi so'z ~60 s o'rniga ~3 s)."""
    from google.genai import types

    thinking = None
    if config.THINKING_LEVEL and model not in _no_thinking:
        thinking = types.ThinkingConfig(thinking_level=config.THINKING_LEVEL)
    return types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
        max_output_tokens=max_tokens or config.MAX_OUTPUT_TOKENS,
        thinking_config=thinking,
    )


def _with_thinking_fallback(model: str, fn):
    """Model thinking_level ni rad etsa (400), sozlamasiz qayta urinadi."""
    try:
        return fn()
    except Exception as e:
        if "400" in str(e) and config.THINKING_LEVEL and model not in _no_thinking:
            log.warning("%s thinking_level ni qo'llamaydi, sozlamasiz qayta urinilmoqda", model)
            _no_thinking.add(model)
            return fn()
        raise


def _contents(prompt: str, files: list[FilePart] | None):
    from google.genai import types

    parts: list = [types.Part.from_bytes(data=f.data, mime_type=f.mime_type) for f in (files or [])]
    parts.append(prompt)
    return parts



# --- Bag'rikeng sxema tekshiruvi ------------------------------------------------
# Model ba'zan JSON'ni biroz noto'g'ri tuzadi: ro'yxat ichida ro'yxat (["a", ["a", "b"]]), majburiy maydonni
# tushirib qoldiradi yoki son o'rniga matn yozadi. Bunday javobni tashlab, foydalanuvchiga xato ko'rsatish
# o'rniga — tuzatib, ishlatamiz.
def _flatten(items):
    out = []
    for x in items:
        if isinstance(x, list):
            out.extend(_flatten(x))
        elif x is not None:
            out.append(x)
    return out


def _empty_for(annotation):
    import typing
    origin = typing.get_origin(annotation)
    if origin in (list, tuple, set) or annotation in (list, tuple, set):
        return []
    if origin is dict or annotation is dict:
        return {}
    if annotation is bool:
        return False
    if annotation in (int, float):
        return 0
    if origin is typing.Literal:
        return typing.get_args(annotation)[0]
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return None if len(args) < len(typing.get_args(annotation)) else _empty_for(args[0])
    return ""


def _normalize(schema, data):
    import typing
    if not isinstance(data, dict):
        return data
    for name, field in schema.model_fields.items():
        ann = field.annotation
        origin = typing.get_origin(ann)
        if name not in data or data[name] is None and field.is_required():
            if field.is_required():
                data[name] = _empty_for(ann)
            continue
        val = data[name]
        if origin is list:
            (item_type,) = typing.get_args(ann) or (str,)
            if not isinstance(val, list):
                val = [val] if val not in ("", None) else []
            val = _flatten(val)
            if item_type is str:
                seen, clean = set(), []
                for x in val:
                    x = x if isinstance(x, str) else (json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x))
                    if x.strip() and x not in seen:
                        seen.add(x)
                        clean.append(x)
                val = clean
            elif isinstance(item_type, type) and issubclass(item_type, BaseModel):
                val = [_normalize(item_type, x) if isinstance(x, dict) else x for x in val]
            data[name] = val
        elif ann is str and not isinstance(val, str):
            data[name] = "\n".join(map(str, _flatten(val))) if isinstance(val, list) else str(val)
        elif isinstance(ann, type) and issubclass(ann, BaseModel) and isinstance(val, dict):
            data[name] = _normalize(ann, val)
    return data


def parse_lenient(schema, raw):
    """JSON matn yoki dict → schema. Avval qat'iy tekshiruv, o'tmasa — tuzatib qayta urinish."""
    if isinstance(raw, str):
        try:
            return schema.model_validate_json(raw)
        except Exception:
            data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    else:
        try:
            return schema.model_validate(raw)
        except Exception:
            data = raw
    fixed = schema.model_validate(_normalize(schema, data))
    log.warning("%s: model javobi tuzatildi (sxemaga to'liq mos emas edi)", schema.__name__)
    return fixed

def generate_json(
    *,
    system: str,
    prompt: str,
    schema: type[T],
    files: list[FilePart] | None = None,
    model: str | None = None,
    mock: Callable[[], T],
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> T:
    """Modeldan `schema` ko'rinishidagi tuzilgan javob oladi."""
    if config.MOCK_MODE:
        return mock()
    if _use_voicelab(files):
        try:
            data = voicelab.chat_json(system=system, prompt=prompt, schema_cls=schema, files=files, model=model,
                                      temperature=temperature, max_tokens=max_tokens, kind=schema.__name__)
            return parse_lenient(schema, data)
        except voicelab.VoiceLabError as e:
            log.error("VoiceLab xatosi: %s", e)
            if not _gemini_fallback_ok(e):
                raise AIError(_vl_message(e)) from e
            log.warning("VoiceLab ishlamadi — javob Gemini'dan olinadi")
        except Exception as e:  # sxemaga mos kelmagan javob
            log.error("VoiceLab javobi sxemaga mos emas: %s", e)
            if not _gemini_fallback_ok(None):
                raise AIError("AI javobini o'qib bo'lmadi. Qayta urinib ko'ring.") from e
    model = _gemini_model(model)

    from google.genai import types

    contents = _contents(prompt, files)

    def one(m):
        return _with_thinking_fallback(m, lambda: _get_client().models.generate_content(
            model=m, contents=contents, config=_gen_config(m, system=system, schema=schema, temperature=temperature,
                                                           max_tokens=max_tokens)))

    try:
        resp = _call(
            one,
            [model or config.MAIN_MODEL, *config.FALLBACK_MODELS], attempts=1,
        )
    except Exception as e:  # tarmoq, kvota, noto'g'ri kalit
        log.exception("Gemini xatosi")
        raise AIError("AI xizmatiga ulanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.") from e

    if isinstance(resp.parsed, schema):
        return resp.parsed
    try:
        return parse_lenient(schema, resp.text or "")
    except Exception as e:
        log.error("JSON tahlil xatosi: %s", resp.text)
        raise AIError("AI javobini o'qib bo'lmadi. Qayta urinib ko'ring.") from e


def stream_json(
    *,
    system: str,
    prompt: str,
    schema: type[T],
    files: list[FilePart] | None = None,
    model: str | None = None,
    mock: Callable[[], T],
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> Iterator[str]:
    """generate_json ning oqimli varianti: JSON matnini bo'laklab qaytaradi.

    Model band yoki limiti tugagan bo'lsa, birinchi bo'lak kelguncha keyingi zaxira
    modelga o'tiladi. Oqim boshlangandan keyingi xato AIError sifatida ko'tariladi."""
    if config.MOCK_MODE:
        text = mock().model_dump_json()
        for i in range(0, len(text), 24):
            time.sleep(0.02)
            yield text[i : i + 24]
        return
    if _use_voicelab(files):
        emitted = False
        try:
            for piece in voicelab.chat_json_stream(system=system, prompt=prompt, schema_cls=schema, files=files,
                                                   model=model, temperature=temperature, max_tokens=max_tokens,
                                                   kind=schema.__name__, reset_marker=RESET):
                emitted = emitted or piece != RESET
                yield piece
            return
        except voicelab.VoiceLabError as e:
            log.error("VoiceLab oqim xatosi: %s", e)
            if not _gemini_fallback_ok(e):
                raise AIError(_vl_message(e)) from e
            log.warning("VoiceLab ishlamadi — javob Gemini'dan olinadi")
            if emitted:
                yield RESET  # mijoz qisman matnni tashlab, Gemini javobini qabul qiladi
    model = _gemini_model(model)

    from google.genai import types

    contents = _contents(prompt, files)

    def start(m):
        cfg = _gen_config(m, system=system, schema=schema, temperature=temperature, max_tokens=max_tokens)
        # Oqimda har bir bo'lak orasidagi kutish qisqaroq: band model uzoq kutilmaydi
        cfg.http_options = types.HttpOptions(timeout=config.STREAM_TIMEOUT_MS,
                                             retry_options=types.HttpRetryOptions(attempts=1))
        it = _get_client().models.generate_content_stream(model=m, contents=contents, config=cfg)
        return it, next(it)

    candidates = list(dict.fromkeys(m for m in [model or config.MAIN_MODEL, *config.FALLBACK_MODELS] if m))
    ready = [m for m in candidates if model_available(m)] or sorted(candidates, key=lambda m: _cooldown.get(m, 0))
    last: Exception | None = None
    for m in ready:
        try:
            it, first = _with_thinking_fallback(m, lambda: start(m))
        except StopIteration:
            continue
        except Exception as e:
            last = e
            wait = _cooldown_for(e)
            if not wait:
                log.exception("Gemini xatosi")
                raise AIError("AI xizmatiga ulanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.") from e
            _cooldown[m] = time.time() + wait
            log.warning("%s vaqtincha chetlab o'tiladi (%ds): %s", m, wait, str(e)[:70])
            continue
        emitted = False
        try:
            if first.text:
                emitted = True
                yield first.text
            for chunk in it:
                if chunk.text:
                    emitted = True
                    yield chunk.text
        except Exception as e:
            last = e
            wait = _cooldown_for(e)
            if not wait:
                log.exception("Oqim uzildi")
                raise AIError("AI javobi uzilib qoldi. Qayta urinib ko'ring.") from e
            _cooldown[m] = time.time() + wait
            log.warning("%s oqim o'rtasida xato berdi (%s), keyingi model sinaladi", m, str(e)[:70])
            if emitted:
                yield RESET  # mijoz qisman matnni tashlab, javobni qaytadan qabul qiladi
            continue
        if not emitted:
            continue  # bo'sh javob — keyingi model
        return
    log.error("Barcha modellar band: %s", last)
    raise AIError("AI xizmati hozir band. Birozdan so'ng qayta urinib ko'ring.")


def transcribe(audio: FilePart) -> str:
    """Ovozli xabarni o'zbek (lotin) matniga aylantiradi."""
    if config.MOCK_MODE:
        return (
            "Assalomu alaykum. Bizning mahallada, Yunusobod tumani 7-mavze, 12-uy atrofida "
            "uch oydan beri ko'cha chiroqlari yonmayapti. Kechasi bolalar va keksalar "
            "yurishga qo'rqishadi. Hokimiyatga og'zaki aytdik, lekin hech narsa o'zgarmadi."
        )
    if config.VOICELAB:
        try:
            return voicelab.transcribe(audio.data, audio.name, audio.mime_type)
        except (voicelab.VoiceLabError, Exception) as e:
            log.exception("VoiceLab STT xatosi")
            raise AIError("Ovozni matnga aylantirib bo'lmadi. Qayta yozib ko'ring.") from e

    prompt = (
        "Ushbu audioyozuvni so'zma-so'z matnga aylantir. Nutq o'zbek tilida bo'lishi mumkin, "
        "sheva yoki rus so'zlari aralashgan bo'lishi mumkin. Natijani o'zbek lotin alifbosida yoz. "
        "Faqat matnning o'zini qaytar, izoh qo'shma. Agar nutq eshitilmasa, bo'sh qator qaytar."
    )
    contents = _contents(prompt, [audio])
    try:
        resp = _call(
            lambda m: _get_client().models.generate_content(model=m, contents=contents),
            [config.FAST_MODEL, *config.FALLBACK_MODELS], attempts=1,
        )
    except Exception as e:
        log.exception("STT xatosi")
        raise AIError("Ovozni matnga aylantirib bo'lmadi. Qayta yozib ko'ring.") from e
    return (resp.text or "").strip()


def embed(texts: list[str], *, is_query: bool) -> list[list[float]]:
    """Matnlar uchun embedding vektorlarini qaytaradi."""
    if config.MOCK_MODE or config.VOICELAB or not config.GEMINI_API_KEY:
        raise AIError("Embedding faqat Gemini rejimida mavjud.")

    from google.genai import types

    task = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
    out: list[list[float]] = []
    for i in range(0, len(texts), 100):  # API bitta so'rovda 100 tagacha matn qabul qiladi
        batch = texts[i : i + 100]
        cfg = types.EmbedContentConfig(task_type=task, output_dimensionality=config.EMBED_DIM)
        try:
            resp = _call(
                lambda m: _get_client().models.embed_content(
                    model=m,
                    # Har bir matn alohida Content — aks holda embedding-2 ularni bitta vektorga birlashtiradi
                    contents=[types.Content(parts=[types.Part(text=t)]) for t in batch],
                    config=cfg,
                ),
                [config.EMBED_MODEL],
                attempts=1 if is_query else 6, delay=10,  # qidiruvda kutmaymiz
            )
        except Exception as e:
            log.exception("Embedding xatosi")
            raise AIError("Qidiruv xizmatida xatolik.") from e
        out.extend(list(emb.values) for emb in resp.embeddings)
    return out
