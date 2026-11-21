# Huquqiy yordamchi

O'zbekiston fuqarolari uchun AI huquqiy yordamchi: veb-sayt va Telegram bot. Javoblar lex.uz qonunchilik
bazasidagi amaldagi hujjatlarga tayanadi va har bir da'vo qonun moddasiga havola bilan beriladi.

## Nima qilindi

| Modul | Imkoniyatlar |
|---|---|
| **Murojaat ustasi** (`/murojaat`) | Muammo matn, ovoz yoki rasm orqali aytiladi. Tizim rasmiy ariza, shikoyat yoki taklif tuzadi, mas'ul organni va javob muddatini aniqlaydi. Natija Word yoki PDF faylda. 14 ta tayyor shablon bor |
| **Huquqiy savol-javob** (`/notarius`) | Notarial, oilaviy, mehnat, uy-joy va boshqa savollarga lex.uz moddalariga havola bilan javob. Hujjat rasmi yoki PDF'ni sodda tilda tushuntiradi. Suhbatlar tarixi saqlanadi. BHM kalkulyatori |
| **Telegram bot** | Saytdagi barcha imkoniyatlar: savol-javob, ovozli xabar, hujjatni tushuntirish, murojaat va Word fayl, shablonlar, BHM. Ro'yxatdan o'tish telefon raqami orqali |
| **Kirish** | Saytga Telegram orqali kiriladi. Suhbatlar sayt va bot o'rtasida umumiy |
| **Narxlar** (`/narxlar`) | Obuna tariflari va daromad modeli (to'lov hali ulanmagan) |

## Arxitektura

```
 Brauzer (HTML/JS)           Telegram
        │                        │
        ▼                        ▼
 ┌──────────────────────────────────────────────┐
 │ FastAPI (app/main.py) + aiogram bot (tgbot.py)│  bitta jarayon
 └──────────────┬───────────────────────────────┘
                │
   appeal.py (murojaat)   notary.py (savol-javob, hujjat tushuntirish)
                │                │
                ▼                ▼
      ┌─────────────── Qidiruv (RAG) ───────────────┐
      │ rag.py    — asosiy kodekslar: BM25 + vektor  │
      │ lexdb.py  — lex.uz: ~37 ming amaldagi hujjat,│
      │             SQLite FTS5                       │
      │ semantic.py — mahalliy embedding (ixtiyoriy) │
      └──────────────────────┬───────────────────────┘
                             ▼
      ai.py → voicelab.py: aisha-orbit (zaxira: aisha-muon), aisha-gemma-4 (rasm/PDF), STT
              Gemini — zaxira provayder
                             ▼
      Javob (JSON sxema) → manba raqamlari va summalar tekshiriladi → sayt / bot
```

| Fayl | Vazifasi |
|---|---|
| `app/main.py` | FastAPI: API, sahifalar, SSE oqimi, Telegram orqali kirish |
| `app/tgbot.py` | Telegram bot (aiogram 3), sayt bilan bitta jarayonda ishlaydi |
| `app/appeal.py` | Murojaat ustasi: tur, mas'ul organ, rasmiy matn, muddat |
| `app/notary.py` | Savol-javob (RAG) va hujjatni tushuntirish |
| `app/rag.py` | Qonun matnlarini moddalarga bo'lish, indekslash, qidirish |
| `app/lexdb.py` | Lex.uz to'liq bazasi bo'yicha qidiruv (FTS5) |
| `app/semantic.py` | Mahalliy semantik qidiruv (multilingual-e5 / bge-m3) |
| `app/ai.py`, `app/voicelab.py` | AI provayder qatlami va VoiceLab API mijozi; model xato JSON qaytarsa, javob tuzatib olinadi |
| `app/db.py` | Foydalanuvchilar, sessiyalar, suhbatlar (SQLite) |
| `app/documents.py` | Word (.docx) hujjat yaratish |
| `app/guard.py` | So'rovlar chegarasi (AI byudjetini himoya qilish) |
| `static/` | Frontend: HTML, CSS (Tailwind), JS |

## Foydalanilgan texnologiyalar
- **Backend:** Python 3.12, FastAPI, Uvicorn, aiogram 3, SQLite (FTS5), NumPy, python-docx, pypdf.
- **AI:** VoiceLab: `aisha-orbit` LLM, `aisha-muon` zaxira, `aisha-gemma-4` rasm va PDF uchun, nutqni matnga aylantirish (STT). Google Gemini zaxira provayder.
- **Qidiruv:** BM25 va SQLite FTS5. Ixtiyoriy semantik qidiruv uchun sentence-transformers (`intfloat/multilingual-e5-small`, `BAAI/bge-m3`).
- **Ma'lumot:** lex.uz hujjatlari (amaldagi tahrir, holati yuridik kartochka bo'yicha tekshirilgan).
- **Frontend:** HTML, Tailwind CSS, vanilla JavaScript. Dizayn sodda va klassik, yoshi kattalar uchun ham qulay.


## Ma'lumotlar
Repoda faqat asosiy qonun matnlari (`data/laws`) va ularning indeksi (`data/index`) bor. Quyidagilar **kompyuterda qoladi**
va repoga kirmaydi:
- `data/app.db`: foydalanuvchilar va suhbatlar;
- `data/lex/full.db`: lex.uz to'liq bazasi (~1,7 GB, `LEX_DB` sozlamasi orqali ulanadi);
- `data/vectors*`: semantik qidiruv vektorlari (bo'lmasa, qidiruv kalit so'z bo'yicha ishlaydi);
- loglar.

`full.db` bo'lmasa, tizim faqat `data/laws` bilan ishlayveradi.

> Ma'lumot beruvchi yordamchi, yuridik maslahat o'rnini bosmaydi. Qonunning amaldagi tahririni lex.uz da tekshiring.
