"""Infinity — barcha sozlamalar shu yerda.

AI provayderi .env orqali tanlanadi:
  * VOICELAB_API_KEY bo'lsa — VoiceLab (aisha-orbit va boshqalar), asosiy provayder;
  * aks holda GEMINI_API_KEY bo'lsa — Gemini;
  * ikkalasi ham bo'lmasa — demo (mock) rejim.
Majburan tanlash: AI_PROVIDER=voicelab | gemini | mock.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- AI provayder ------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
VOICELAB_API_KEY = os.getenv("VOICELAB_API_KEY", "").strip()

AI_PROVIDER = os.getenv("AI_PROVIDER", "").strip().lower() or (
    "voicelab" if VOICELAB_API_KEY else "gemini" if GEMINI_API_KEY else "mock")
VOICELAB = AI_PROVIDER == "voicelab"
# Kalit bo'lmasa, tizim "demo" (mock) rejimda ishlaydi — namunaviy javoblar qaytaradi
MOCK_MODE = AI_PROVIDER == "mock" or (VOICELAB and not VOICELAB_API_KEY) or (
    AI_PROVIDER == "gemini" and not GEMINI_API_KEY)

# --- VoiceLab (https://docs.voicelab.uz) -------------------------------------
VOICELAB_BASE_URL = os.getenv("VOICELAB_BASE_URL", "https://api.voicelab.uz").rstrip("/")
# Asosiy matn modeli: murojaat matni va savol-javob
VL_MAIN_MODEL = os.getenv("VL_MAIN_MODEL") or os.getenv("VOICELAB_LLM_MODEL") or "aisha-orbit"  # huquq-yordamchi nomi ham o'qiladi
# Asosiy model band (503) yoki limitda bo'lsa navbat bilan sinaladigan zaxira modellar
VL_FALLBACK_MODELS = [m.strip() for m in (os.getenv("VL_FALLBACK_MODELS") or os.getenv("VOICELAB_LLM_FALLBACK") or "aisha-muon").split(",") if m.strip()]
# Rasm/PDF o'qiy oladigan model (aisha-orbit rasm qabul qilmaydi). gemma-4 — eng arzon vision model
VL_VISION_MODEL = os.getenv("VL_VISION_MODEL", "aisha-gemma-4")
# "O'ylash" darajasi. MUHIM: aisha-orbit da standart qiymat "max" — tokenlarni bir necha barobar ko'paytiradi
VL_REASONING = os.getenv("VL_REASONING", "low").strip().lower()
VL_STT_LANGUAGE = os.getenv("VL_STT_LANGUAGE", "uz")
VL_TIMEOUT_S = float(os.getenv("VL_TIMEOUT_S", "90"))
# $ / 1 mln token (kirish, chiqish) — faqat data/llm_usage.log dagi taxminiy narx uchun
VL_PRICES = {"aisha-orbit": (1.20, 4.20), "aisha-muon": (0.65, 2.50), "aisha-halo": (3.20, 12.70),
             "aisha-gemma-4": (0.45, 0.70)}
USAGE_LOG = ROOT / "data" / "llm_usage.log"

# --- Gemini (rasm/PDF va zaxira provayder) -------------------------------------
# Rasm va PDF kim orqali o'qiladi: gemini (standart, kalit bo'lsa) yoki voicelab
FILES_PROVIDER = os.getenv("FILES_PROVIDER", "gemini").strip().lower()
# VoiceLab ishlamay qolsa (503, limit, kredit) javob Gemini'dan olinadi (GEMINI_API_KEY bo'lsa)
GEMINI_FALLBACK = os.getenv("GEMINI_FALLBACK", "1") == "1"
MAIN_MODEL = os.getenv("MAIN_MODEL", "gemini-3.6-flash")
FAST_MODEL = os.getenv("FAST_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "FALLBACK_MODEL", "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
# Embedding modeli: faqat Gemini rejimida (VoiceLab'da embedding yo'q — qidiruv BM25/FTS5 bilan)
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-2")

AI_TIMEOUT_MS = int(os.getenv("AI_TIMEOUT_MS", "25000"))
STREAM_TIMEOUT_MS = int(os.getenv("STREAM_TIMEOUT_MS", "15000"))
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "LOW").strip().upper() or None

# --- Token tejash -------------------------------------------------------------
# Bitta javob uchun eng ko'p chiqish tokeni ("o'ylash" tokenlari ham shunga kiradi)
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "3000"))
SOURCE_CHARS = int(os.getenv("SOURCE_CHARS", "1600"))     # modelga beriladigan har bir manba uzunligi
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "8"))          # savol-javobda eng ko'p manba soni
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "4"))      # suhbatdan nechta oxirgi xabar yuboriladi
HISTORY_CHARS = int(os.getenv("HISTORY_CHARS", "350"))    # har bir xabardan nechta belgi
ANSWER_CACHE_SIZE = int(os.getenv("ANSWER_CACHE_SIZE", "300"))      # bir xil savolga qayta token sarflanmaydi
ANSWER_CACHE_TTL = int(os.getenv("ANSWER_CACHE_TTL", str(24 * 3600)))
# Murakkab savolni qism-so'rovlarga AI bilan ajratish (qo'shimcha AI chaqiruvi). 0 — mahalliy, tekin usul
QUERY_PLAN_LLM = os.getenv("QUERY_PLAN_LLM", "0") == "1"

# --- Mahalliy semantik qidiruv (tekin, kompyuterda) ------------------------------
# Vektorlar: python -m scripts.build_vectors. Fayl bo'lmasa — kalit so'z qidiruvi
SEMANTIC_SEARCH = os.getenv("SEMANTIC_SEARCH", "1") == "1"
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "intfloat/multilingual-e5-small")
# Vektorlar papkasi (har bir model uchun alohida): e5 -> data/vectors, bge-m3 -> data/vectors_bge
VECTORS_DIR = Path(os.getenv("VECTORS_DIR", str(ROOT / "data" / "vectors"))).expanduser()
LOCAL_EMBED_DEVICE = os.getenv("LOCAL_EMBED_DEVICE", "cpu")  # server uchun; qurish skripti GPU'ni o'zi tanlaydi
# Semantik ballning og'irligi (0..1). 16 savollik baholashda 0.6 eng yaxshi chiqdi (hit@5 13/16, MRR 0.74)
SEMANTIC_WEIGHT = float(os.getenv("SEMANTIC_WEIGHT", "0.6"))
# Lex.uz bazasi (427k bo'lak) uchun alohida. Sinovda e5-small bu yerda natijani YOMONLASHTIRDI (masalan, "maktabga
# qabul" -> sport va shaxmat qarorlari), shuning uchun standart 0 — faqat kalit so'z. Kuchliroq model bilan sinab ko'ring
LEX_SEMANTIC_WEIGHT = float(os.getenv("LEX_SEMANTIC_WEIGHT", "0"))
LEX_SEMANTIC_CANDIDATES = int(os.getenv("LEX_SEMANTIC_CANDIDATES", "8"))

# --- Yo'llar ----------------------------------------------------------------
APP_DIR = ROOT / "app"
STATIC_DIR = ROOT / "static"
LAWS_DIR = ROOT / "data" / "laws"          # asosiy kodekslar (.txt), scripts.ingest indekslaydi
INDEX_FILE = ROOT / "data" / "index" / "index.json"
ORGANS_FILE = APP_DIR / "data" / "organs.json"
APP_DB = ROOT / "data" / "app.db"   # foydalanuvchilar va suhbatlar tarixi
# Lex.uz'dan olingan to'liq baza: amaldagi ~24 ming akt, SQLite FTS5 (huquq-yordamchi/scripts/build_full_index.py)
LEX_DB = Path(os.getenv("LEX_DB", str(ROOT / "data" / "lex" / "full.db"))).expanduser()

# --- RAG --------------------------------------------------------------------
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
LEX_TOP_K = int(os.getenv("LEX_TOP_K", "4"))   # lex.uz bazasidan nechta bo'lak qo'shiladi
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
EMBED_TOKENS_PER_MINUTE = int(os.getenv("EMBED_TOKENS_PER_MINUTE", "25000"))
EMBED_PRICE_PER_M = float(os.getenv("EMBED_PRICE_PER_M", "0.20"))
EMBED_BUDGET_USD = float(os.getenv("EMBED_BUDGET_USD", "3"))
VECTORS_FILE = ROOT / "data" / "index" / "vectors.npy"
CHUNK_MAX_CHARS = 2500

# --- Cheklovlar -------------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "8"))   # Gemini orqali; VoiceLab'da bitta fayl 4 MiB gacha
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "5"))
MAX_FILES = 4                                           # VoiceLab: bitta xabarda 4 tagacha fayl
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "4000"))
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "1500"))

# --- Byudjet himoyasi (app/guard.py) ------------------------------------------
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "8"))
DAILY_LIMIT_PER_IP = int(os.getenv("DAILY_LIMIT_PER_IP", "60"))
GLOBAL_DAILY_LIMIT = int(os.getenv("GLOBAL_DAILY_LIMIT", "2000"))
TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"
ALLOWED_HOSTS = {h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()}

SHOW_API_DOCS = os.getenv("SHOW_API_DOCS", "1") == "1"

# --- Foydalanuvchilar va Telegram bot -------------------------------------------
# 1 — AI ishlatish uchun ro'yxatdan o'tish (Telegram orqali) majburiy: byudjetni begonalardan himoya qiladi
REQUIRE_LOGIN = os.getenv("REQUIRE_LOGIN", "1") == "1"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT = os.getenv("TELEGRAM_BOT", "1") == "1"        # bot server bilan birga ishga tushadimi
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# --- Davlat boji hisobi ---------------------------------------------------------
BHM_SOM = int(os.getenv("BHM_SOM", "440000"))
