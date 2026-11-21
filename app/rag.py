"""RAG: qonun hujjatlarini moddalarga bo'lish, indekslash va qidirish.

Indeks `python -m scripts.ingest` buyrug'i bilan quriladi.
Gemini kaliti bo'lmasa, qidiruv kalit so'zlar bo'yicha ishlaydi.
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ai, config, semantic

log = logging.getLogger("infinity.rag")

# "16-modda." yoki "16 1 -modda." (16¹-modda: lex.uz da yuqori indeks bo'shliq bilan keladi)
ARTICLE_RE = re.compile(r"(?m)^\s*(\d+(?:\.\d+)?)(?:\s(\d{1,2}))?\s*[-–]\s*(?:modda|модда)\b\.?\s*(.*)$", re.IGNORECASE)
SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
CHAPTER_RE = re.compile(r"(?m)^\s*(\d+)\s*[-–]\s*(?:bob|боб)\b\.?\s*(.*)$", re.IGNORECASE)
APPENDIX_RE = re.compile(r"(?m)^\s*(?:[A-ZʻʼО'‘ʼ\-\s]{0,60}MIQDORLARI|\d+\s*[-–]\s*ilova|ILOVA)\s*$")
WORD_RE = re.compile(r"[a-zа-яёўқғҳʻʼ'’‘`ʹ]+", re.IGNORECASE)  # ʼ (U+02BC) va ‘ ham so'z ichida


@dataclass
class Chunk:
    id: int
    doc: str
    article: str
    title: str
    text: str
    collection: str = ""
    url: str = ""

    @property
    def label(self) -> str:
        return f"{self.doc}, {self.article}" if self.article else self.doc


# --- Fayllarni o'qish -------------------------------------------------------
def read_file(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".txt":
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")
    if suf == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if suf == ".pdf":
        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
    return ""


def doc_title(path: Path) -> str:
    return re.sub(r"[_]+", " ", path.stem).strip()


# --- Bo'laklarga ajratish ---------------------------------------------------
def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > limit and cur:
            parts.append(cur.strip())
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts


def chunk_document(doc: str, text: str, start_id: int) -> list[Chunk]:
    text = text.replace("\r", "")
    matches = list(ARTICLE_RE.finditer(text))
    chunks: list[Chunk] = []

    if not matches:  # moddalar yo'q (yo'riqnoma, reglament) — bob va bandlar bo'yicha
        return _chunk_by_points(doc, text, start_id)

    # Oxirgi moddadan keyingi ilova (masalan, stavkalar jadvali) alohida bo'laklanadi
    appendix_at = len(text)
    am = APPENDIX_RE.search(text, matches[-1].end())
    if am:
        appendix_at = am.start()

    chapters = [(m.start(), f"{m.group(1)}-bob. {m.group(2).strip()}") for m in CHAPTER_RE.finditer(text)]
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else appendix_at
        body = text[m.start() : end].strip()
        chapter = next((c for pos, c in reversed(chapters) if pos < m.start()), "")
        article = f"{m.group(1)}{(m.group(2) or '').translate(SUP)}-modda"
        title = m.group(3).strip()
        for j, part in enumerate(_split_long(body, config.CHUNK_MAX_CHARS)):
            header = f"{doc}. {chapter}\n" if chapter else f"{doc}.\n"
            chunks.append(
                Chunk(start_id + len(chunks), doc, article + (f" ({j + 1}-qism)" if j else ""), title, header + part)
            )
    if appendix_at < len(text):
        appendix = text[appendix_at:]
        orig = appendix.strip().split("\n", 1)[0].strip()
        # Davlat boji qonuni ilovasining sarlavhasi faqat "MIQDORLARI" deb keladi
        name = "Davlat boji stavkalari miqdorlari" if orig.upper() == "MIQDORLARI" else orig.capitalize()
        for c in _chunk_by_points(doc, appendix, start_id + len(chunks)):
            c.article = f"Ilova, {c.article}" if c.article else "Ilova"
            if not c.title or c.title == orig:
                c.title = f"Ilova: {name}"
            chunks.append(c)
    return chunks


POINT_RE = re.compile(r"^(\d{1,4})\.\s")
SECTION_RE = re.compile(r"^(\d+\s*[-–]\s*(?:bob|§)|\d+\s*[-–]\s*ilova|[IVXLC]+\s+bob|§\s*\d+)", re.IGNORECASE)


def _chunk_by_points(doc: str, text: str, start_id: int) -> list[Chunk]:
    """Raqamlangan bandlar ("12. ...") asosida, bob/paragraf sarlavhalarini saqlab bo'laklaydi."""
    chunks: list[Chunk] = []
    section, prev_blank = "", True
    buf: list[str] = []
    points: list[str] = []
    last_point = ("", "")  # oxirgi band: (raqam, boshlang'ich matni)
    cont: tuple[str, str] | None = None  # joriy bo'lak band o'rtasidan boshlangan bo'lsa — o'sha band

    def flush():
        nonlocal cont
        body = "\n".join(buf).strip()
        if body:
            label = (f"{points[0]}-band" if len(points) == 1 else f"{points[0]}–{points[-1]}-bandlar") if points else ""
            header = f"{doc}. {section}\n" if section else f"{doc}.\n"
            if cont:  # band sarlavhasini qo'shamiz, aks holda kontekst yo'qoladi
                header += f"({cont[1]} — davomi)\n"
                label = f"{cont[0]}-band (davomi)" + (f", {label}" if label else "")
            chunks.append(Chunk(start_id + len(chunks), doc, label, section, header + body))
        buf.clear()
        points.clear()
        cont = None

    for line in text.split("\n"):
        s = line.strip()
        if not s:
            prev_blank = True
            continue
        is_header = SECTION_RE.match(s) or (prev_blank and len(s) < 160 and not POINT_RE.match(s) and not s.endswith((";", ",")))
        prev_blank = False
        if is_header and len(s) < 200:
            flush()
            section = s
            last_point = ("", "")
            continue
        m = POINT_RE.match(s)
        if m and sum(len(x) for x in buf) > config.CHUNK_MAX_CHARS * 0.6:
            flush()
        if not buf and not m and last_point[0]:
            cont = last_point
        if m:
            points.append(m.group(1))
            last_point = (m.group(1), s[:160])
        buf.append(s)
        if sum(len(x) for x in buf) > config.CHUNK_MAX_CHARS * 1.4:
            flush()
    flush()
    return chunks


# --- Indeks -----------------------------------------------------------------
def build_index() -> int:
    files = sorted(p for p in config.LAWS_DIR.rglob("*") if p.suffix.lower() in {".txt", ".docx", ".pdf"})
    if not files:
        raise SystemExit(f"Hujjatlar topilmadi: {config.LAWS_DIR}")

    chunks: list[Chunk] = []
    for f in files:
        text = read_file(f)
        url = ""
        m = re.match(r"\s*Manba:\s*(\S+)", text)
        if m:  # fetch_laws.py qo'ygan manba havolasi
            url, text = m.group(1), text[m.end():]
        collection = f.parent.name if f.parent != config.LAWS_DIR else ""
        new = chunk_document(doc_title(f), text, len(chunks))
        for c in new:
            c.collection, c.url = collection, url
        print(f"  {collection}/{f.name}: {len(new)} bo'lak")
        chunks.extend(new)

    config.INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.INDEX_FILE.write_text(
        json.dumps({"embed_model": config.EMBED_MODEL, "dim": config.EMBED_DIM,
                    "chunks": [c.__dict__ for c in chunks]}, ensure_ascii=False),
        encoding="utf-8",
    )

    if not USE_VECTORS:
        # Eski vektorlar (pulga olingan) o'chirilmaydi — Gemini rejimiga qaytilsa yana ishlatiladi
        print("Gemini rejimi emas: faqat kalit so'z qidiruvi ishlaydi (vektorlar yaratilmadi).")
    else:
        def save(got: list) -> None:
            # float16 — fayl 2 baravar kichik, qidiruv sifatiga ta'siri sezilmaydi
            vecs = np.zeros((len(chunks), config.EMBED_DIM), dtype=np.float16)  # vektori yo'qlar — nol qator
            for i, v in enumerate(got):
                if v is not None:
                    vecs[i] = v
            tmp = config.VECTORS_FILE.with_suffix(".tmp.npy")
            np.save(tmp, vecs)
            tmp.replace(config.VECTORS_FILE)  # ishlab turgan server yarim yozilgan faylni o'qimasligi uchun

        # Eng ko'p so'raladigan hujjatlar birinchi vektorlanadi (limit tugasa ham eng muhimlari tayyor bo'ladi)
        order = {d: i for i, d in enumerate(EMBED_PRIORITY)}
        prio = [min((v for k, v in order.items() if c.doc.startswith(k)), default=99) for c in chunks]
        got = _embed_with_cache([c.text for c in chunks], on_progress=save, priority=prio)
        save(got)
        done = sum(v is not None for v in got)
        print(f"Vektorlar saqlandi: {done}/{len(chunks)} bo'lak"
              + ("" if done == len(chunks) else " (qolganlari kalit so'z bo'yicha qidiriladi)"))
    return len(chunks)


CACHE_KEYS = config.INDEX_FILE.parent / "embed_cache_keys.json"
CACHE_VECS = config.INDEX_FILE.parent / "embed_cache_vecs.npy"
OLD_CACHE = config.INDEX_FILE.parent / "embed_cache.json"
# Vektorlash navbati (fayl nomi boshlanishi bo'yicha) — ro'yxatda yo'qlari oxirida
EMBED_PRIORITY = [
    "Murojaatlar", "Mehnat kodeksi", "Mamuriy javobgarlik", "Oila kodeksi", "Notariat", "Davlat boji",
    "Istemolchilarning", "Uy-joy kodeksi", "Fuqarolik kodeksi 2-qism", "Fuqarolik kodeksi 1-qism",
    "Oliy sud Plenumi", "Yol harakati", "Konstitutsiya", "Fuqarolarning davlat pensiya", "Transport egalari",
    "Aholi bandligi", "Nogironligi", "Notarial harakatlarni", "Ayrim notarial", "Jinoyat kodeksi",
]


def _load_cache() -> dict[str, np.ndarray]:
    """Embedding keshi: sha1(model|dim|matn) -> vektor (float16). Eski JSON keshi bir marta ko'chiriladi."""
    cache: dict[str, np.ndarray] = {}
    if CACHE_KEYS.exists() and CACHE_VECS.exists():
        keys = json.loads(CACHE_KEYS.read_text(encoding="utf-8"))
        vecs = np.load(CACHE_VECS)
        cache = dict(zip(keys, vecs))
    if OLD_CACHE.exists():
        for k, v in json.loads(OLD_CACHE.read_text(encoding="utf-8")).items():
            cache.setdefault(k, np.asarray(v, dtype=np.float16))
    return cache


def _save_cache(cache: dict[str, np.ndarray]) -> None:
    keys = list(cache)
    tmp = CACHE_VECS.with_suffix(".tmp.npy")
    np.save(tmp, np.stack([cache[k] for k in keys]).astype(np.float16) if keys else np.zeros((0, config.EMBED_DIM)))
    tmp.replace(CACHE_VECS)
    CACHE_KEYS.write_text(json.dumps(keys), encoding="utf-8")
    OLD_CACHE.unlink(missing_ok=True)


def _embed_with_cache(texts: list[str], on_progress=None, priority: list[int] | None = None) -> list:
    """Embedding'larni limitga moslab, qismlab oladi. Natijalar keshda saqlanadi —
    jarayon to'xtab qolsa yoki limit tugasa, qayta ishga tushirilganda davom etadi.
    Boshlashdan oldin xarajat hisoblanadi va byudjetdan oshsa to'xtatiladi.
    Hali vektori yo'q matnlar uchun None qaytariladi (ular kalit so'z bo'yicha qidiriladi)."""
    import hashlib

    key = lambda t: hashlib.sha1(f"{config.EMBED_MODEL}|{config.EMBED_DIM}|{t}".encode()).hexdigest()  # noqa: E731
    cache = _load_cache()
    rank = {t: p for t, p in zip(texts, priority or [0] * len(texts))}
    todo = sorted((t for t in dict.fromkeys(texts) if key(t) not in cache), key=lambda t: rank.get(t, 99))
    est = lambda t: len(t) // 3 + 10  # noqa: E731  — taxminiy token soni (o'zbekcha matn uchun ehtiyotkor)
    tokens = sum(map(est, todo))
    cost = tokens / 1e6 * config.EMBED_PRICE_PER_M
    tpm = config.EMBED_TOKENS_PER_MINUTE
    print(f"Embedding: {len(texts) - len(todo)} tasi keshda, {len(todo)} tasi qoldi; "
          f"~{tokens / 1e6:.2f} mln token, taxminiy xarajat ~${cost:.2f}, ~{tokens // tpm + 1} daqiqa")
    if cost > config.EMBED_BUDGET_USD:
        raise SystemExit(f"Taxminiy xarajat (${cost:.2f}) byudjetdan (${config.EMBED_BUDGET_USD}) oshadi. "
                         f"EMBED_BUDGET_USD ni oshiring yoki hujjatlarni kamaytiring.")

    batch: list[str] = []
    sent = 0
    window_start, window_tokens = time.time(), 0
    for t in todo + [None]:  # oxirida None — qolganini yuborish uchun
        if t is not None and len(batch) < 100 and sum(map(est, batch)) + est(t) <= min(tpm, 250_000):
            batch.append(t)
            continue
        if batch:
            # Daqiqalik token limitiga rioya qilish
            btok = sum(map(est, batch))
            if window_tokens + btok > tpm:
                time.sleep(max(0.0, 61 - (time.time() - window_start)))
                window_start, window_tokens = time.time(), 0
            try:
                vecs = ai.embed(batch, is_query=False)
            except ai.AIError as e:
                print(f"  To'xtatildi ({e.__cause__ or e}).\n"
                      f"  Limit tugagan bo'lishi mumkin — keyinroq 'python -m scripts.ingest' ni qayta ishga tushiring.")
                break
            window_tokens += btok
            for txt, vec in zip(batch, vecs):
                cache[key(txt)] = np.asarray(vec, dtype=np.float16)
            sent += len(batch)
            if sent % 1000 < len(batch) or t is None:  # keshni har ~1000 tadan keyin diskka yozamiz
                _save_cache(cache)
                if on_progress:
                    on_progress([cache.get(key(x)) for x in texts])
            print(f"  {len(texts) - len(todo) + sent}/{len(texts)} ta tayyor", flush=True)
        batch = [t] if t is not None else []
    _save_cache(cache)
    return [cache.get(key(t)) for t in texts]


_reload_lock = threading.Lock()
# Vektor qidiruv faqat Gemini rejimida (VoiceLab'da embedding yo'q — savol uchun token ham sarflanmaydi)
USE_VECTORS = config.AI_PROVIDER == "gemini" and bool(config.GEMINI_API_KEY)

BM25_K1, BM25_B, AVG_LEN = 1.2, 0.75, 300


class Index:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None
        self.has_vec: np.ndarray = np.zeros(0, dtype=bool)
        self.postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.load()

    def load(self) -> None:
        if not config.INDEX_FILE.exists():
            log.warning("RAG indeksi topilmadi. 'python -m scripts.ingest' ni ishga tushiring.")
            return
        self._index_mtime = config.INDEX_FILE.stat().st_mtime
        data = json.loads(config.INDEX_FILE.read_text(encoding="utf-8"))
        self.chunks = [Chunk(**c) for c in data["chunks"]]
        self.collections = np.array([c.collection for c in self.chunks])
        self._build_postings()
        self._build_refs()
        self._load_vectors()

    def _build_postings(self) -> None:
        """Teskari indeks: so'z -> (bo'lak raqamlari, BM25 og'irligi). Har bir bo'lak uchun alohida
        lug'at saqlashdan bir necha baravar kam xotira oladi."""
        ids: dict[str, list[int]] = {}
        weights: dict[str, list[float]] = {}
        for i, c in enumerate(self.chunks):
            tf = Counter(_words(c.text))
            norm = BM25_K1 * (1 - BM25_B + BM25_B * (sum(tf.values()) or 1) / AVG_LEN)
            for w, n in tf.items():
                ids.setdefault(w, []).append(i)
                weights.setdefault(w, []).append(n / (n + norm))
        total = len(self.chunks)
        self._sorted_keys = sorted(ids)
        self.postings = {
            w: (np.array(ids[w], dtype=np.int32),
                np.array(weights[w], dtype=np.float32) * math.log(1 + total / (1 + len(ids[w]))))
            for w in ids
        }

    def _build_refs(self) -> None:
        """Moddalararo havolalar: (hujjat, "128⁴-modda") -> shu moddaga havola qiluvchi bo'laklar.
        Masalan, 34¹-moddada "128 4 -moddasi" deb yozilgan — 128⁴ topilganda 34¹ ham manbaga qo'shiladi."""
        self.refs: dict[tuple[str, str], list[int]] = {}
        for i, c in enumerate(self.chunks):
            seen = set()
            for m in REF_RE.finditer(c.text):
                art = f"{m.group(1)}{(m.group(2) or '').translate(SUP)}-modda"
                if art != _base_article(c.article) and art not in seen:
                    seen.add(art)
                    self.refs.setdefault((c.doc, art), []).append(i)

    def related(self, chunk: Chunk, limit: int = 2) -> list[Chunk]:
        """Berilgan moddaga havola qiluvchi boshqa moddalar (ko'p havola qilinadigan umumiy moddalar bundan mustasno)."""
        ids = self.refs.get((chunk.doc, _base_article(chunk.article)), [])
        if not ids or len(ids) > 12:  # juda ko'p havola — bu umumiy modda, qo'shishning foydasi kam
            return []
        return [self.chunks[i] for i in ids[:limit]]

    def _load_vectors(self) -> None:
        self.vectors, self.has_vec = None, np.zeros(len(self.chunks), dtype=bool)
        self._vec_mtime = config.VECTORS_FILE.stat().st_mtime if config.VECTORS_FILE.exists() else 0
        if self._vec_mtime and USE_VECTORS:
            try:
                v = np.load(config.VECTORS_FILE).astype(np.float32)
            except Exception:  # fayl aynan shu payt yozilayotgan bo'lishi mumkin
                return
            if len(v) == len(self.chunks):
                norms = np.linalg.norm(v, axis=1, keepdims=True)
                self.has_vec = norms[:, 0] > 0
                self.vectors = v / (norms + 1e-9)
        log.info("RAG: %d bo'lak, vektorli: %d", len(self.chunks), int(self.has_vec.sum()))

    @property
    def ready(self) -> bool:
        return bool(self.chunks)

    def _variants(self, w: str) -> list[str]:
        """So'zning lug'atdagi shakllari: aynan o'zi + shu o'zak bilan boshlanadiganlar.
        O'zbek qo'shimchalari: "kamar" so'rovi "kamarid(an)", "kamarin(i)" ni ham topishi kerak."""
        if len(w) < 4:
            return [w] if w in self.postings else []
        keys = self._sorted_keys
        i = bisect.bisect_left(keys, w)
        out = []
        while i < len(keys) and keys[i].startswith(w) and len(out) < 40:
            out.append(keys[i])
            i += 1
        return out

    def _keyword_scores(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        for w in set(_words(query)) - QUESTION_WORDS:
            term = np.zeros(len(self.chunks), dtype=np.float32)
            for v in self._variants(w):  # bir bo'lakda bir nechta shakl bo'lsa — eng kattasi olinadi
                p = self.postings[v]
                np.maximum.at(term, p[0], p[1])
            scores += term
        return scores / (scores.max() or 1)

    def search(self, query: str, k: int | None = None, collection: str | None = None,
               doc_prefix: str | None = None, article_prefix: str | None = None) -> list[Chunk]:
        if not self.ready:
            return []
        k = k or config.RAG_TOP_K
        kw = self._keyword_scores(query)
        scores = kw
        qv = semantic.query_vec(query) if self.vectors is None else None
        sem = semantic.local_scores(qv, len(self.chunks)) if qv is not None else None
        if sem is not None:  # mahalliy semantik vektorlar (tekin)
            lo = float(np.percentile(sem, 50))
            sem_n = np.clip((sem - lo) / (float(sem.max()) - lo + 1e-9), 0, 1)
            scores = config.SEMANTIC_WEIGHT * sem_n + (1 - config.SEMANTIC_WEIGHT) * kw
        elif self.vectors is not None and ai.model_available(config.EMBED_MODEL):
            try:
                qv = np.array(ai.embed([query], is_query=True)[0], dtype=np.float32)
                qv /= np.linalg.norm(qv) + 1e-9
                cos = self.vectors @ qv
                # Vektori hali yo'q bo'laklar uchun o'xshashlik kalit so'z balidan taxminlanadi
                cos = np.where(self.has_vec, cos, 0.45 + 0.35 * kw)
                scores = 0.8 * cos + 0.2 * kw
            except ai.AIError:
                # Embedding limiti tugagan bo'lsa ham qidiruv to'xtamaydi — kalit so'zlar bo'yicha davom etadi
                log.warning("Vektor qidiruv ishlamadi, kalit so'z qidiruviga o'tildi")
        mask = np.ones(len(self.chunks), dtype=bool)
        if collection:
            mask &= self.collections == collection
        if doc_prefix or article_prefix:
            mask &= np.array([(not doc_prefix or c.doc.startswith(doc_prefix))
                              and (not article_prefix or c.article.startswith(article_prefix)) for c in self.chunks])
        scores = np.where(mask, scores, -1.0)
        top = np.argsort(-scores)[:k]
        return [self.chunks[i] for i in top if scores[i] > 0]


# Matn ichidagi modda havolalari: "128 4 -moddasi", "125-moddasi", "128 3 -moddalarida" (sarlavha "128-modda." emas)
REF_RE = re.compile(r"(\d+)(?:\s(\d{1,2}))?\s*[-–]\s*modda(?:si|sida|siga|lari|larida|lariga|da)?\b(?!\.)")


# Savol so'zlari (7 harfgacha kesilgan) — kalit so'z qidiruvida shovqin
QUESTION_WORDS = {w[:7] for w in """qancha qanday qanaqa nima nimaga nega necha nechta qachon qayerda qayerga kimga
    mumkinmi bormi kerakmi boladimi bolsa bolsam qilsam qilish olish berish menga mening meni bizning sizning
    iltimos ayting aytib bering yordam savol javob kerak shart uchun bilan""".split()}


def _base_article(article: str) -> str:
    """"128⁴-modda (2-qism)" -> "128⁴-modda"."""
    return article.split(" (")[0].split(",")[0].strip()


def _words(text: str) -> list[str]:
    out = []
    for w in WORD_RE.findall(text.lower()):
        w = re.sub(r"[ʻʼ'’‘`ʹ]", "", w)
        if len(w) > 2:
            out.append(w[:7])  # oddiy "stemming": o'zbek qo'shimchalarini qisman olib tashlaydi
    return out


_index: Index | None = None


_last_check = 0.0


def get_index() -> Index:
    """Joriy indeks. Fayllar yangilangan bo'lsa (ingest ishlayotganda), yangi indeks fonda
    to'liq quriladi va tayyor bo'lgach almashtiriladi — so'rovlar hech qachon yarim yuklangan
    indeksni ko'rmaydi."""
    global _index, _last_check
    if _index is None:
        with _reload_lock:
            if _index is None:
                _index = Index()
        return _index
    now = time.time()
    if now - _last_check > 10:  # fayllarni 10 soniyada bir marta tekshiramiz
        _last_check = now
        idx = _index
        changed = (config.INDEX_FILE.exists() and config.INDEX_FILE.stat().st_mtime != getattr(idx, "_index_mtime", 0)) or (
            config.VECTORS_FILE.exists() and config.VECTORS_FILE.stat().st_mtime != idx._vec_mtime)
        if changed and _reload_lock.acquire(blocking=False):
            def rebuild():
                global _index
                try:
                    _index = Index()
                except Exception:
                    log.exception("Indeksni yangilab bo'lmadi")
                finally:
                    _reload_lock.release()
            threading.Thread(target=rebuild, daemon=True).start()
    return _index
