"""Mahalliy semantik qidiruv: embedding modeli kompyuterning o'zida ishlaydi (token va pul sarflanmaydi).

Vektorlar scripts/build_vectors.py bilan quriladi (data/vectors/). Fayl bo'lmasa yoki
sentence-transformers o'rnatilmagan bo'lsa — hamma narsa avvalgidek kalit so'z bo'yicha ishlaydi.
Vektor fayllari yangilansa (qurish davom etayotganda), 60 soniyada bir marta qayta o'qiladi.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache

import numpy as np

from . import config

log = logging.getLogger("infinity.semantic")

DIR = config.VECTORS_DIR
LEX_FILE = DIR / "lex.npy"
LOCAL_FILE = DIR / "local.npy"
META_FILE = DIR / "meta.json"
DIM = 384  # multilingual-e5-small; boshqa model uchun meta.json dagi "dim" ishlatiladi
PASSAGE_CHARS = 1200

_lock = threading.Lock()
_model = None
_model_failed = False
_store: dict = {"lex": None, "local": None, "lex_done": 0, "checked": 0.0, "mtime": 0.0}


def _e5() -> bool:
    return "e5" in config.LOCAL_EMBED_MODEL.lower()  # e5 modellari "query:"/"passage:" prefiksini talab qiladi


def passage(doc: str, title: str, text: str) -> str:
    head = f"{doc}. {title}".strip(". ") if title else doc
    return f"{'passage: ' if _e5() else ''}{head}\n{text[:PASSAGE_CHARS]}"


def read_meta() -> dict:
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_model(device: str | None = config.LOCAL_EMBED_DEVICE):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(config.LOCAL_EMBED_MODEL, device=device)
    model.max_seq_length = 320
    if model.device.type == "cuda":
        model.half()
    return model


def _get_model():
    global _model, _model_failed
    if _model is None and not _model_failed:
        with _lock:
            if _model is None and not _model_failed:
                try:
                    _model = load_model()
                    log.info("Semantik model yuklandi: %s (%s)", config.LOCAL_EMBED_MODEL, _model.device)
                except Exception:
                    _model_failed = True
                    log.exception("Semantik modelni yuklab bo'lmadi — kalit so'z qidiruvi ishlaydi")
    return _model


def _refresh() -> None:
    now = time.time()
    if now - _store["checked"] < 60:
        return
    _store["checked"] = now
    mtime = max((p.stat().st_mtime for p in (LEX_FILE, LOCAL_FILE, META_FILE) if p.exists()), default=0)
    if mtime == _store["mtime"]:
        return
    meta = read_meta()
    if meta.get("model") and meta["model"] != config.LOCAL_EMBED_MODEL:
        log.warning("Vektorlar boshqa model bilan qurilgan (%s) — ishlatilmaydi", meta["model"])
        return
    try:
        local = np.load(LOCAL_FILE) if LOCAL_FILE.exists() else None
        # 427k × 384 float16 ≈ 330 MB — mmap: RAMga nusxa olinmaydi, OS sahifa keshidan o'qiladi
        lex = np.load(LEX_FILE, mmap_mode="r") if LEX_FILE.exists() else None
    except Exception:
        log.exception("Vektor fayllarini o'qib bo'lmadi")
        return
    _store.update(local=local, lex=lex, lex_done=int(meta.get("lex_done", 0)), mtime=mtime)
    log.info("Vektorlar: asosiy %s, lex.uz %d tayyor", 0 if local is None else len(local), _store["lex_done"])


def enabled() -> bool:
    if not config.SEMANTIC_SEARCH:
        return False
    _refresh()
    return _store["local"] is not None or _store["lex"] is not None


@lru_cache(maxsize=512)
def _query_vec(text: str) -> np.ndarray | None:
    model = _get_model()
    if model is None:
        return None
    return model.encode([f"{'query: ' if _e5() else ''}{text}"], normalize_embeddings=True, convert_to_numpy=True)[0].astype(np.float32)


def query_vec(text: str) -> np.ndarray | None:
    return _query_vec(text.strip()[:1000]) if enabled() else None


def local_scores(qv: np.ndarray, n: int) -> np.ndarray | None:
    """Asosiy kodekslar bo'laklari uchun kosinus o'xshashlik (indeks bilan bir xil tartibda)."""
    v = _store["local"]
    if v is None or len(v) != n:
        return None
    return v.astype(np.float32) @ qv


def lex_top(qv: np.ndarray, k: int = 40) -> list[tuple[int, float]]:
    """Lex.uz bazasidan eng yaqin k ta bo'lak: [(chunks.id, kosinus), ...]."""
    v = _store["lex"]
    if v is None:
        return []
    upto = min(_store["lex_done"], len(v) - 1)
    if upto <= 0:
        return []
    best_ids, best_sc = [], []
    step = 65536  # xotirani tejash uchun bo'laklab
    for s in range(1, upto + 1, step):
        e = min(s + step, upto + 1)
        sc = v[s:e].astype(np.float32) @ qv
        top = np.argpartition(-sc, min(k, len(sc) - 1))[:k]
        best_ids.extend((top + s).tolist())
        best_sc.extend(sc[top].tolist())
    order = np.argsort(-np.array(best_sc))[:k]
    return [(best_ids[i], best_sc[i]) for i in order]


def lex_vec(ids: list[int]) -> dict[int, np.ndarray]:
    v = _store["lex"]
    if v is None:
        return {}
    upto = _store["lex_done"]
    return {i: v[i].astype(np.float32) for i in ids if 0 < i <= upto}


def stats() -> dict:
    meta = read_meta()
    return {"semantic": enabled(), "vec_local": meta.get("local", 0), "vec_lex_done": meta.get("lex_done", 0),
            "vec_lex_total": meta.get("lex_total", 0)}
