"""Telegram bot (aiogram 3): veb-sayt bilan BIR jarayonda ishlaydi (main.py lifespan).

* Ro'yxatdan o'tish: /start -> "📱 Telefon raqamni ulashish" tugmasi -> foydalanuvchi bazaga yoziladi.
* Saytga kirish: sayt t.me/<bot>?start=login_<kod> havolasini beradi -> bot kodni tasdiqlaydi.
* Savol (matn yoki ovoz) -> notary.answer; rasm/PDF (+ izoh) -> notary.explain.
* Suhbatlar sayt bilan umumiy bazada (data/app.db) — saytda ham ko'rinadi.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)

from . import ai, appeal, config, db, documents, guard, notary

log = logging.getLogger("infinity.bot")
dp = Dispatcher()
_bot: Bot | None = None
_username: str | None = None
_current_chat: dict[int, int] = {}     # tg_id -> joriy suhbat ID
_pending_login: dict[int, str] = {}    # ro'yxatdan o'tmagan foydalanuvchining sayt kirish kodi

BTN_NEW, BTN_SITE = "🆕 Yangi suhbat", "🌐 Saytda ochish"
BTN_APPEAL, BTN_TEMPLATES, BTN_BHM, BTN_ASK = "📝 Murojaat yozish", "📋 Tayyor shablonlar", "🧮 BHM kalkulyatori", "💬 Savol-javob"
MENU = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text=BTN_APPEAL), KeyboardButton(text=BTN_TEMPLATES)],
    [KeyboardButton(text=BTN_ASK), KeyboardButton(text=BTN_BHM)],
    [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_SITE)],
])
# Saytdagi modullar botda: "murojaat" — keyingi xabar murojaatga aylanadi, "bhm" — kalkulyator, None — savol-javob
_mode: dict[int, str | None] = {}
_appeal_state: dict[int, dict] = {}   # tg_id -> {"desc": ..., "savollar": [...]} (aniqlashtiruvchi javoblar uchun)
CONTACT_KB = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
    [KeyboardButton(text="📱 Telefon raqamni ulashish (ro'yxatdan o'tish)", request_contact=True)]])
DISCLAIMER = "<i>Bu yuridik maslahat emas. Amaldagi tahrirni lex.uz da tekshiring.</i>"


def username() -> str | None:
    return _username


# --- Formatlash -----------------------------------------------------------------
def _md(text: str) -> str:
    """Mini-Markdown -> Telegram HTML (**qalin**)."""
    t = html.escape(str(text or "").strip())
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)


def _bullets(items, mark="•") -> str:
    return "\n".join(f"{mark} {_md(str(x).rstrip('.;'))}" for x in items if str(x).strip())


def format_answer(r: dict) -> str:
    parts = []
    if r.get("javobgarlik"):
        icon = "🔴" if "jinoiy" in r["javobgarlik"].lower() else "🟠"
        parts.append(f"{icon} <b>Javobgarlik: {html.escape(r['javobgarlik'])}</b>")
    if r.get("xulosa"):
        parts.append(f"<b>{_md(re.sub(r'\s*\[[0-9,\s–-]+\]', '', r['xulosa']))}</b>")
    if r.get("javob"):
        parts.append(_md(r["javob"]))
    if r.get("narx") and not r.get("ogohlantirish"):
        parts.append("💳 <b>To'lov / jarima:</b>\n" + _md(r["narx"]))
    if r.get("hujjatlar"):
        parts.append("📋 <b>Kerakli hujjatlar:</b>\n" + _bullets(r["hujjatlar"]))
    if r.get("qadamlar"):
        parts.append("🧭 <b>Tartib:</b>\n" + "\n".join(f"{i}. {_md(s)}" for i, s in enumerate(r["qadamlar"], 1)))
    if r.get("ogohlantirish"):
        parts.append("⚠️ " + html.escape(r["ogohlantirish"]))
    for a in r.get("aniqlashtirish") or []:
        parts.append(f"🎯 <i>{html.escape(a.get('savol', ''))}</i>")
    src = r.get("manbalar") or []
    if src:
        lines = []
        for s in src:
            name = html.escape(f"{s['hujjat']}{', ' + s['modda'] if s.get('modda') else ''}")
            lines.append(f"[{s['raqam']}] " + (f'<a href="{html.escape(s["url"])}">{name}</a>' if s.get("url") else name))
        parts.append("📚 <b>Manbalar:</b>\n" + "\n".join(lines))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def format_doc(r: dict) -> str:
    parts = [f"📄 <b>{html.escape(r.get('hujjat_turi', 'Hujjat'))}</b>"
             + (f"\n⏳ {html.escape(r['muddat'])}" if r.get("muddat") else "")]
    if r.get("savolga_javob"):
        parts.append("💬 <b>Savolingizga javob:</b>\n" + _md(r["savolga_javob"]))
    if r.get("qisqacha"):
        parts.append(_md(r["qisqacha"]))
    if r.get("tomonlar"):
        parts.append("👥 <b>Tomonlar:</b>\n" + _bullets(r["tomonlar"]))
    if r.get("huquq_majburiyatlar"):
        parts.append("📌 <b>Huquq va majburiyatlar:</b>\n" + _bullets(r["huquq_majburiyatlar"]))
    if r.get("etibor"):
        parts.append("⚠️ <b>E'tibor bering:</b>\n" + _bullets(r["etibor"]))
    if r.get("keyingi_qadam"):
        parts.append("➡️ " + _md(r["keyingi_qadam"]))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)


def split_html(text: str, limit: int = 3900) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut])
            para = para[cut:].lstrip("\n")
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


async def _send(message: Message, text: str) -> None:
    for chunk in split_html(text):
        try:
            await message.answer(chunk, reply_markup=MENU, disable_web_page_preview=True)
        except Exception:  # HTML xato bo'lsa — oddiy matn
            await message.answer(re.sub(r"<[^>]+>", "", chunk), reply_markup=MENU, parse_mode=None)


# --- Ro'yxatdan o'tish va kirish -----------------------------------------------------
def _user_from(message: Message) -> dict:
    u = message.from_user
    return db.upsert_tg_user(u.id, u.username, " ".join(x for x in (u.first_name, u.last_name) if x))


async def _confirm_login(message: Message, user: dict, code: str) -> None:
    if db.confirm_login_code(code, user["id"]):
        await message.answer("✅ Saytga kirish tasdiqlandi. Brauzerga qayting — sahifa o'zi yangilanadi.", reply_markup=MENU)
    else:
        await message.answer("⚠️ Kirish havolasi eskirgan yoki ishlatilgan. Saytda «Kirish» tugmasini qayta bosing.",
                             reply_markup=MENU)


@dp.message(CommandStart())
async def start(message: Message, command: CommandObject):
    user = _user_from(message)
    arg = (command.args or "").strip()
    code = arg[6:] if arg.startswith("login_") else ""
    if not db.is_registered(user):
        if code:
            _pending_login[message.from_user.id] = code
        await message.answer(
            "Assalomu alaykum! Men <b>Huquqiy yordamchi</b> botiman.\n\n"
            "Foydalanish uchun ro'yxatdan o'ting: pastdagi tugma orqali telefon raqamingizni ulashing.",
            reply_markup=CONTACT_KB)
        return
    if code:
        await _confirm_login(message, user, code)
        return
    await message.answer(
        f"Xush kelibsiz, {html.escape(user['name'] or '')}! Saytdagi hamma imkoniyatlar shu yerda ham bor:\n\n"
        "• 💬 <b>Savol-javob</b> — huquqiy savolingizni yozing yoki 🎤 gapiring (lex.uz moddalari bilan);\n"
        "• 📄 <b>Hujjatni tushuntirish</b> — rasm yoki PDF yuboring (savolni izohiga yozing);\n"
        "• 📝 <b>Murojaat yozish</b> — ariza, shikoyat, taklif + Word fayl;\n"
        "• 📋 <b>Tayyor shablonlar</b> — 14 ta tayyor murojaat;\n"
        "• 🧮 <b>BHM kalkulyatori</b>.\n\nSuhbatlaringiz saytda ham ko'rinadi.",
        reply_markup=MENU)


@dp.message(F.contact)
async def on_contact(message: Message):
    c = message.contact
    if c.user_id != message.from_user.id:
        await message.answer("Iltimos, o'zingizning raqamingizni tugma orqali ulashing.", reply_markup=CONTACT_KB)
        return
    u = message.from_user
    user = db.upsert_tg_user(u.id, u.username, " ".join(x for x in (u.first_name, u.last_name) if x), c.phone_number)
    await message.answer("✅ Ro'yxatdan o'tdingiz! Endi savolingizni yozing yoki ovozli xabar yuboring.",
                         reply_markup=MENU)
    code = _pending_login.pop(u.id, None)
    if code:
        await _confirm_login(message, user, code)


@dp.message(Command("new"))
@dp.message(F.text == BTN_NEW)
async def new_chat(message: Message):
    _current_chat.pop(message.from_user.id, None)
    _mode.pop(message.from_user.id, None)
    _appeal_state.pop(message.from_user.id, None)
    await message.answer("🆕 Yangi suhbat boshlandi. Savolingizni yozing.", reply_markup=MENU)


# --- Saytdagi modullar: Murojaat ustasi, shablonlar, BHM kalkulyatori -------------------
@dp.message(Command("murojaat"))
@dp.message(F.text == BTN_APPEAL)
async def appeal_start(message: Message):
    if not await _registered(message):
        return
    _mode[message.from_user.id] = "murojaat"
    _appeal_state.pop(message.from_user.id, None)
    await message.answer(
        "📝 <b>Murojaat ustasi</b>\n\nMuammoingizni oddiy so'zlar bilan yozing yoki 🎤 ovozli xabar yuboring. "
        "Rasm yoki PDF (dalil) ham yuborishingiz mumkin — izohiga muammoni yozing.\n\n"
        "Men rasmiy <b>ariza, shikoyat yoki taklif</b> tuzaman, mas'ul organni va javob muddatini aytaman, "
        "Word fayl beraman.\n<i>Masalan: «Uyimizda 3 kundan beri suv yo'q, kommunal xizmat kelmayapti».</i>",
        reply_markup=MENU)


@dp.message(Command("shablonlar"))
@dp.message(F.text == BTN_TEMPLATES)
async def templates_list(message: Message):
    rows, row = [], []
    for t in appeal.templates():
        row.append(InlineKeyboardButton(text=t["nomi"][:60], callback_data=f"tpl:{t['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await message.answer("📋 <b>Tayyor shablonlar</b> — AI'siz, darhol. Kerakligini tanlang:",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("tpl:"))
async def template_open(call: CallbackQuery):
    await call.answer()
    user = db.user_by_tg(call.from_user.id)
    if not db.is_registered(user):
        await call.message.answer("Avval ro'yxatdan o'ting: telefon raqamingizni ulashing.", reply_markup=CONTACT_KB)
        return
    res = appeal.from_template(call.data[4:])
    if not res:
        await call.message.answer("Shablon topilmadi.", reply_markup=MENU)
        return
    await _send_appeal(call.message, user, res)


@dp.message(Command("bhm"))
@dp.message(F.text == BTN_BHM)
async def bhm_start(message: Message):
    _mode[message.from_user.id] = "bhm"
    await message.answer(
        f"🧮 <b>BHM kalkulyatori</b>\nBazaviy hisoblash miqdori: <b>{_som(config.BHM_SOM)}</b>.\n\n"
        "Miqdorni yozing: <code>25%</code> (BHMning 25 foizi) yoki <code>3</code> (3 baravari).", reply_markup=MENU)


@dp.message(F.text == BTN_ASK)
async def ask_mode(message: Message):
    _mode.pop(message.from_user.id, None)
    await message.answer("💬 Huquqiy savolingizni yozing yoki 🎤 ovozli xabar yuboring. "
                         "Hujjat (rasm/PDF) yuborsangiz — sodda tilda tushuntiraman.", reply_markup=MENU)


def _som(n: float) -> str:
    return f"{round(n):,}".replace(",", " ") + " so'm"


async def _bhm_calc(message: Message, text: str) -> None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|foiz|baravar|x|×)?", text.lower())
    if not m:
        await message.answer("Miqdorni son bilan yozing: <code>25%</code> yoki <code>3</code>.", reply_markup=MENU)
        return
    n = float(m.group(1).replace(",", "."))
    pct = m.group(2) in ("%", "foiz")
    total = config.BHM_SOM * (n / 100 if pct else n)
    label = f"BHMning {n:g} {'foizi' if pct else 'baravari'}"
    await message.answer(f"🧮 {label} = <b>{_som(total)}</b>\n<i>BHM: {_som(config.BHM_SOM)}</i>", reply_markup=MENU)


def format_appeal(r: dict) -> str:
    cat = r.get("kategoriya") or {}
    title = {"ariza": "ARIZA", "shikoyat": "SHIKOYAT", "taklif": "TAKLIF"}.get(r.get("turi"), "MUROJAAT")
    parts = [f"📝 <b>{title}</b>" + (f" — {html.escape(r['mavzu'])}" if r.get("mavzu") else "")
             + (f"\n<i>Shablon: {html.escape(r['shablon'])}</i>" if r.get("shablon") else "")]
    if cat.get("organ"):
        parts.append(f"🏛 <b>Kimga:</b> {html.escape(cat['organ'])}")
    if r.get("muddat_kun"):
        sana = r.get("muddat_sana", "")
        if re.match(r"\d{4}-\d{2}-\d{2}", sana):
            sana = f"{sana[8:10]}.{sana[5:7]}.{sana[:4]}"
        parts.append(f"⏱ <b>Javob muddati:</b> {r['muddat_kun']} kun ({html.escape(sana)} gacha)")
    if r.get("matn"):
        parts.append("<b>Matn:</b>\n" + html.escape(r["matn"]))
    if r.get("talablar"):
        parts.append("✅ <b>So'rov:</b>\n" + _bullets(r["talablar"]))
    if r.get("maslahatlar"):
        parts.append("💡 <b>Maslahat:</b>\n" + _bullets(r["maslahatlar"]))
    if r.get("kanallar"):
        parts.append("📮 <b>Qayerga yuborish:</b>\n" + "\n".join(
            f"• <b>{html.escape(k.get('nomi', ''))}</b> — {html.escape(k.get('manzil', ''))}" for k in r["kanallar"][:4]))
    if r.get("savollar"):
        parts.append("🎯 <b>Murojaatni kuchaytirish uchun javob bering</b> (yozib yuboring — hujjatni yangilayman):\n"
                     + _bullets(r["savollar"], "❓"))
    parts.append("📎 Word fayl pastda. <i>Yuborishdan oldin [qavs ichidagi] joylarni to'ldiring.</i>")
    return "\n\n".join(parts)


async def _send_appeal(message: Message, user: dict, res: dict, images: list | None = None) -> None:
    await _send(message, format_appeal(res))
    data = {"kimga": (res.get("kategoriya") or {}).get("organ", ""), "fio": user.get("name") or "",
            "telefon": user.get("phone") or "", "manzil": "", "turi": res.get("turi"), "mavzu": res.get("mavzu", ""),
            "matn": res.get("matn", ""), "ilovalar": res.get("ilovalar") or []}
    content = await asyncio.to_thread(documents.build_docx, data, images or [])
    await message.answer_document(BufferedInputFile(content, f"{res.get('turi', 'murojaat')}.docx"), reply_markup=MENU)


async def _appeal(message: Message, user: dict, text: str, files: list | None = None) -> None:
    tg_id = message.from_user.id
    if await _limited(message, cost=1 + len(files or [])):
        return
    state = _appeal_state.get(tg_id)
    if state and not files:   # aniqlashtiruvchi savollarga javob — oldingi murojaat yangilanadi
        desc, answers = state["desc"], [{"savol": "; ".join(state["savollar"]), "javob": text}]
    else:
        desc, answers = text, []
    if len(desc) < 10:
        await message.answer("Muammoni batafsilroq yozing (kamida bir-ikki gap).", reply_markup=MENU)
        return
    chat_id, _ = _chat_for(user, tg_id, "Murojaat: " + desc[:60])
    db.add_message(chat_id, "user", text if not files else (text or "📄 dalil"), {"fayllar": [f.name for f in files]} if files else None)
    wait = await message.answer("⏳ Murojaat tayyorlanmoqda: turi, mas'ul organ va rasmiy matn…")
    try:
        await message.bot.send_chat_action(message.chat.id, "upload_document")
        res = await asyncio.to_thread(appeal.draft, description=desc, answers=answers, files=files or [])
        db.add_message(chat_id, "assistant", res.get("matn", ""), {**res, "tur": "murojaat"})
        _appeal_state[tg_id] = {"desc": desc, "savollar": res.get("savollar") or []}
        await wait.delete()
        await _send_appeal(message, user, res, [f for f in files or [] if f.mime_type.startswith("image/")])
    except ai.AIError as e:
        await wait.edit_text(f"⚠️ {html.escape(str(e))}")
    except Exception:
        log.exception("Botda murojaat tayyorlashda xato")
        await wait.edit_text("⚠️ Kutilmagan xatolik. Qayta urinib ko'ring.")


@dp.message(F.text == BTN_SITE)
async def site(message: Message):
    await message.answer(f"🌐 Sayt: {config.PUBLIC_URL}/notarius\nSaytdagi «Kirish» tugmasi orqali shu Telegram "
                         "hisobingiz bilan kirasiz — suhbatlaringiz u yerda ham ko'rinadi.", reply_markup=MENU)


# --- Savol va hujjatlar --------------------------------------------------------------
async def _registered(message: Message) -> dict | None:
    user = _user_from(message)
    if not db.is_registered(user):
        await message.answer("Avval ro'yxatdan o'ting: telefon raqamingizni ulashing.", reply_markup=CONTACT_KB)
        return None
    return user


def _chat_for(user: dict, tg_id: int, title: str) -> tuple[int, list[dict]]:
    chat_id = _current_chat.get(tg_id)
    if chat_id and db.get_chat(chat_id, user["id"]):
        return chat_id, db.history_for_ai(chat_id, turns=config.HISTORY_TURNS)
    chat_id = db.create_chat(user["id"], title, source="telegram")
    _current_chat[tg_id] = chat_id
    return chat_id, []


async def _limited(message: Message, cost: int = 1) -> bool:
    try:
        guard.check_key(f"tg:{message.from_user.id}", cost)
        return False
    except guard.LimitError as e:
        await message.answer("⏳ " + e.args[1], reply_markup=MENU)
        return True


async def _ask(message: Message, user: dict, text: str):
    if await _limited(message):
        return
    chat_id, history = _chat_for(user, message.from_user.id, text)
    db.add_message(chat_id, "user", text)
    wait = await message.answer("⏳ Qonun bazasidan qidirilmoqda va javob tayyorlanmoqda…")
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        res = await asyncio.to_thread(notary.answer, text, history)
        db.add_message(chat_id, "assistant", res.get("javob", ""), {**res, "tur": "javob"})
        await wait.delete()
        await _send(message, format_answer(res))
    except ai.AIError as e:
        await wait.edit_text(f"⚠️ {html.escape(str(e))}")
    except Exception:
        log.exception("Bot javobida xato")
        await wait.edit_text("⚠️ Kutilmagan xatolik. Qayta urinib ko'ring.")


@dp.message(F.voice | F.audio)
async def on_voice(message: Message):
    user = await _registered(message)
    if not user or await _limited(message):
        return
    media = message.voice or message.audio
    buf = io.BytesIO()
    await message.bot.download(media, destination=buf)
    mime = getattr(media, "mime_type", None) or "audio/ogg"
    try:
        text = await asyncio.to_thread(ai.transcribe, ai.FilePart(buf.getvalue(), mime, "voice.ogg"))
    except ai.AIError as e:
        await message.answer(f"⚠️ {html.escape(str(e))}", reply_markup=MENU)
        return
    if not text:
        await message.answer("Ovozni tushunib bo'lmadi, qayta yozib ko'ring.", reply_markup=MENU)
        return
    await message.answer(f"🎤 <i>{html.escape(text)}</i>")
    await _route(message, user, text)


@dp.message(F.photo | F.document)
async def on_file(message: Message):
    user = await _registered(message)
    if not user:
        return
    if message.document:
        mime = message.document.mime_type or ""
        if not (mime.startswith("image/") or mime == "application/pdf"):
            await message.answer("Faqat rasm yoki PDF hujjatni tushuntira olaman.", reply_markup=MENU)
            return
        if (message.document.file_size or 0) > config.MAX_UPLOAD_MB * 1024 * 1024:
            await message.answer(f"Fayl {config.MAX_UPLOAD_MB} MB dan oshmasin.", reply_markup=MENU)
            return
        media, name = message.document, message.document.file_name or "hujjat"
    else:
        media, mime, name = message.photo[-1], "image/jpeg", "rasm.jpg"
    question = (message.caption or "").strip()
    if _mode.get(message.from_user.id) == "murojaat":   # murojaat ustasi: rasm/PDF — dalil (ilova)
        buf = io.BytesIO()
        await message.bot.download(media, destination=buf)
        if len(question) < 10 and not _appeal_state.get(message.from_user.id):
            await message.answer("Dalil qabul qilindi. Iltimos, rasm <b>izohiga</b> muammoni bir-ikki gap bilan yozib, "
                                 "qayta yuboring.", reply_markup=MENU)
            return
        await _appeal(message, user, question or _appeal_state[message.from_user.id]["desc"],
                      files=[ai.FilePart(buf.getvalue(), mime, name)])
        return
    if await _limited(message, cost=2):
        return
    buf = io.BytesIO()
    await message.bot.download(media, destination=buf)
    chat_id, history = _chat_for(user, message.from_user.id, question or f"Hujjat: {name}")
    db.add_message(chat_id, "user", question or f"📄 {name}", {"fayllar": [name]})
    wait = await message.answer("⏳ Hujjat o'qilmoqda…")
    try:
        res = await asyncio.to_thread(notary.explain, [ai.FilePart(buf.getvalue(), mime, name)], question, history)
        db.add_message(chat_id, "assistant", res.get("savolga_javob") or res.get("qisqacha", ""), {**res, "tur": "hujjat"})
        await wait.delete()
        await _send(message, format_doc(res))
    except ai.AIError as e:
        await wait.edit_text(f"⚠️ {html.escape(str(e))}")
    except Exception:
        log.exception("Hujjatni tushuntirishda xato")
        await wait.edit_text("⚠️ Kutilmagan xatolik. Qayta urinib ko'ring.")


async def _route(message: Message, user: dict, text: str) -> None:
    mode = _mode.get(message.from_user.id)
    if mode == "murojaat":
        await _appeal(message, user, text)
    elif mode == "bhm":
        await _bhm_calc(message, text)
    else:
        await _ask(message, user, text)


@dp.message(F.text)
async def on_text(message: Message):
    if _mode.get(message.from_user.id) == "bhm":   # kalkulyator ro'yxatdan o'tishsiz ham ishlaydi
        await _bhm_calc(message, message.text)
        return
    user = await _registered(message)
    if user:
        await _route(message, user, message.text.strip())


# --- Ishga tushirish ------------------------------------------------------------------
async def _run():
    global _bot, _username
    _bot = Bot(config.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    me = await _bot.get_me()
    _username = me.username
    log.info("Telegram bot: @%s", _username)
    await _bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(_bot, handle_signals=False)


def start_background() -> asyncio.Task:
    async def runner():
        while True:  # tarmoq uzilsa — qayta ulanadi
            try:
                await _run()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Telegram bot to'xtadi, 15 s dan keyin qayta ulanadi")
                await asyncio.sleep(15)
    return asyncio.create_task(runner())


async def stop(task: asyncio.Task) -> None:
    try:
        await dp.stop_polling()
    except Exception:
        pass
    task.cancel()
    if _bot:
        await _bot.session.close()
