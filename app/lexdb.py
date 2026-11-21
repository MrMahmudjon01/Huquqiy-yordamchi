"""Lex.uz to'liq bazasi: amaldagi ~24 ming akt (qonun, kodeks, farmon, qaror, buyruq), SQLite FTS5.

Fayl: config.LEX_DB (standart: data/lex/full.db). U huquq-yordamchi loyihasidagi
scripts/build_full_index.py bilan lex.uz'dan to'g'ridan-to'g'ri qurilgan (faqat AMALDAGI hujjatlar).
Qidiruv to'liq diskda (BM25 ni SQLite o'zi hisoblaydi) — xotiraga yuklanmaydi, AI token sarflamaydi.

Indeksdagi matn normallashtirilgan: kirill → lotin, apostroflar olib tashlangan, so'z 6 harfgacha kesilgan.
So'rov ham aynan shu qoida bilan normallashtiriladi (norm_tokens).
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
from collections import defaultdict

from . import config, semantic
from .translit import CYR_RE, to_latin

log = logging.getLogger("infinity.lexdb")

APOS = re.compile(r"[ʻʼ'`‘’ʹ]")
TOKEN = re.compile(r"[a-z0-9]+")
# build_full_index.py dagi bilan bir xil bo'lishi shart
STOP = set("va yoki bilan uchun ham bu shu u ular bir esa agar lekin ammo hamda emas kabi qilib boʻyicha bo yicha "
           "toʻgʻrisida haqida etib qiladi qilinadi boʻladi mumkin kerak".split())
# Savol so'zlari: qidiruvga foyda bermaydi, faqat shovqin qo'shadi (so'rovdan olib tashlanadi)
QUESTION_STOP = {t[:6] for t in """qancha qanday qanaqa nima nimaga nega necha nechta qachon qayerda qayerga kim kimga
    mumkinmi bormi kerakmi boladimi bolsa bolsam qilsam qilish olish berish menga mening meni biz bizning siz
    sizning iltimos ayting aytib bering yordam savol javob kerak shart""".split()}
MAX_PER_DOC = 2          # bitta aktdan ko'pi bilan nechta bo'lak
MAX_TERMS = 12           # so'rovdagi eng ko'p so'z soni
ID_OFFSET = 10_000_000   # mahalliy indeks bo'laklari bilan ID to'qnashmasligi uchun

_local = threading.local()
_common: set[str] | None = None
_common_lock = threading.Lock()


def norm_tokens(text: str) -> list[str]:
    if CYR_RE.search(text):
        text = to_latin(text)
    return [t[:6] for t in TOKEN.findall(APOS.sub("", text.lower())) if t not in STOP and len(t) >= 2]


def query_tokens(text: str) -> list[str]:
    return [t for t in dict.fromkeys(norm_tokens(text)) if t not in QUESTION_STOP]


_idf_cache: dict[str, float] = {}


def _idf(con: sqlite3.Connection, term: str, total: int) -> float:
    if term not in _idf_cache:
        row = con.execute("SELECT doc FROM vocab WHERE term=?", (term,)).fetchone()
        df = row[0] if row else 0
        _idf_cache[term] = math.log(1 + (total - df + 0.5) / (df + 0.5))
    return _idf_cache[term]


def _con() -> sqlite3.Connection | None:
    """Har bir oqim uchun alohida, faqat o'qish uchun ulanish."""
    if not config.LEX_DB.exists():
        return None
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(f"file:{config.LEX_DB}?mode=ro", uri=True, check_same_thread=False)
        con.execute("PRAGMA mmap_size=268435456")
        _local.con = con
    return con


def available() -> bool:
    return _con() is not None


def _common_terms(con: sqlite3.Connection) -> set[str]:
    """Juda ko'p hujjatda uchraydigan so'zlar (respublika, qaror ...) — qidiruvni sekinlashtiradi, foydasi yo'q."""
    global _common
    if _common is None:
        with _common_lock:
            if _common is None:
                total = con.execute("SELECT max(rowid) FROM chunks").fetchone()[0] or 1
                _common = {t for (t,) in con.execute("SELECT term FROM vocab WHERE doc > ?", (total * 0.15,))}
    return _common


def warmup() -> None:
    con = _con()
    if con is not None:
        _common_terms(con)
        log.info("Lex.uz bazasi: %s (%d ta umumiy so'z chetlanadi)", config.LEX_DB, len(_common or ()))


def _prefixes(tokens: list[str]) -> set[str]:
    """So'zlar va ularning 4-5 harfli boshlanishlari: so'rovdagi qisqa o'zak ("kamar") uzun shaklga mos kelsin."""
    out = set(tokens)
    for t in tokens:
        out.update(t[:n] for n in (4, 5) if len(t) > n)
    return out


def _doc_key(name: str) -> str:
    """Lex.uz'da bir akt lotin va kirill yozuvida alohida turadi — ularni bitta deb hisoblash uchun kalit."""
    return re.sub(r"[^a-z0-9]+", " ", APOS.sub("", to_latin(name).lower())).replace("ts", "s").strip()[:80]


def search(queries: list[str], k: int | None = None):
    """Bir nechta so'rov bo'yicha eng mos bo'laklar (rag.Chunk ro'yxati)."""
    from .rag import Chunk  # aylanma importdan qochish

    k = k or config.LEX_TOP_K
    con = _con()
    if con is None or k <= 0:
        return []
    common = _common_terms(con)
    total = con.execute("SELECT max(rowid) FROM chunks").fetchone()[0] or 1
    agg: dict[int, float] = defaultdict(float)
    qsets: list[list[str]] = []
    for q in queries:
        toks = [t for t in query_tokens(q) if t not in common][:MAX_TERMS]
        if not toks:
            continue
        qsets.append(toks)
        # Prefiks qidiruv: "kamar"* -> "kamari" (indeksda so'zlar 6 harfgacha kesilgan, qo'shimchalar turlicha)
        match = " OR ".join(f'"{t}"*' if 4 <= len(t) < 6 else f'"{t}"' for t in toks)
        try:
            rows = con.execute("SELECT rowid, -bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 60",
                               (match,)).fetchall()
        except sqlite3.Error:
            log.exception("Lex.uz bazasida qidiruv xatosi")
            continue
        if rows:
            top = rows[0][1] or 1.0
            for rid, sc in rows:
                agg[rid] += sc / top

    # Semantik nomzodlar: ma'nosi yaqin, lekin boshqa so'zlar bilan yozilgan moddalar (mahalliy model, tekin)
    qv = semantic.query_vec(" ".join(queries)) if queries and config.LEX_SEMANTIC_WEIGHT > 0 else None
    cos: dict[int, float] = {}
    if qv is not None:
        for rid, sc in semantic.lex_top(qv, config.LEX_SEMANTIC_CANDIDATES):
            cos[rid] = sc
            agg.setdefault(rid, 0.0)
        for rid, v in semantic.lex_vec([r for r in agg if r not in cos]).items():
            cos[rid] = float(v @ qv)

    if not agg:
        return []
    marks = ",".join("?" * len(agg))
    meta = {r[0]: r[1:] for r in con.execute(
        f"SELECT id, doc_id, law, doc_type, doc_date, article, title, url, text FROM chunks WHERE id IN ({marks})",
        list(agg))}

    # Qayta saralash: BM25 bitta noyob so'z uchun ham yuqori baho beradi. Bo'lak so'rovning muhim so'zlarini
    # (IDF og'irligi bilan) qanchalik ko'p qamrasa, shuncha yuqori; sarlavhadagi moslik qo'shimcha ball beradi.
    def coverage(r) -> float:
        body = _prefixes(norm_tokens(f"{r[1]} {r[5] or ''} {r[7]}"))
        head = _prefixes(norm_tokens(f"{r[1]} {r[5] or ''}"))
        score = 0.0
        for toks in qsets:
            w = {t: _idf(con, t, total) for t in toks}
            tot = sum(w.values()) or 1
            score += sum(v for t, v in w.items() if t in body) / tot
            score += 0.5 * sum(v for t, v in w.items() if t in head) / tot
        return score / max(len(qsets), 1)

    TYPE_BONUS = {"Kodeks": 1.15, "Qonun": 1.1}
    # Kosinusni nomzodlar ichida 0..1 ga keltiramiz (e5 da o'xshashlik odatda 0.7-0.9 oralig'ida bo'ladi)
    lo, hi = (min(cos.values()), max(cos.values())) if cos else (0.0, 1.0)
    def sem(rid: int) -> float:
        return (cos[rid] - lo) / (hi - lo) if rid in cos and hi > lo else 0.0
    w_sem = config.LEX_SEMANTIC_WEIGHT if cos else 0.0
    scored = {rid: (0.25 * agg[rid] + coverage(r) + w_sem * sem(rid)) * TYPE_BONUS.get(r[2], 1.0)
              for rid, r in meta.items()}
    best = sorted(scored.items(), key=lambda x: -x[1])[:40]

    # Bir xil nomli aktlardan bittasi: eng yangisi, teng bo'lsa lotin yozuvidagisi
    chosen: dict[str, tuple] = {}
    for rid, _ in best:
        r = meta.get(rid)
        if not r:
            continue
        key, rank = _doc_key(r[1]), (r[3] or "", not CYR_RE.search(r[1]))
        if key not in chosen or rank > chosen[key][0]:
            chosen[key] = (rank, r[0])
    keep = {doc_id for _, doc_id in chosen.values()}

    out, per_doc = [], defaultdict(int)
    for rid, _ in best:
        r = meta.get(rid)
        if not r or r[0] not in keep or per_doc[r[0]] >= MAX_PER_DOC:
            continue
        per_doc[r[0]] += 1
        doc_id, law, dtype, ddate, article, title, url, text = r
        name = f"{law} ({dtype}, {ddate[:4]})" if ddate else f"{law} ({dtype})"
        out.append(Chunk(ID_OFFSET + rid, name, article or "", title or "", text, collection="lex", url=url or ""))
        if len(out) >= k:
            break
    return out


def stats() -> dict:
    con = _con()
    if con is None:
        return {"lex_db": False}
    try:
        info = dict(con.execute("SELECT key, value FROM info").fetchall())
    except sqlite3.Error:
        info = {}
    return {"lex_db": True, "lex_chunks": con.execute("SELECT max(rowid) FROM chunks").fetchone()[0] or 0,
            "lex_built": info.get("built", ""), "lex_source": info.get("source", "")}
