"""2-modul: huquqiy savol-javob (RAG). Notarial masalalar ham shu yerda."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict

from pydantic import BaseModel, Field

from . import ai, config, lexdb, rag

log = logging.getLogger("infinity.qa")

FEE_RE = re.compile(r"narx|to[ʻ'‘`]?lov|boj|qancha|pul|xarajat|tarif|necha so[ʻ'‘`]?m", re.IGNORECASE)
# Savol bir necha qismdan iborat bo'lishi mumkinligi belgilari
MULTI_RE = re.compile(r"\s(va|hamda|yoki|shuningdek)\s|,|\?.+\?", re.IGNORECASE)


class Aniqlashtirish(BaseModel):
    savol: str = Field(description="Foydalanuvchiga beriladigan qisqa aniqlashtiruvchi savol")
    variantlar: list[str] = Field(description="2-4 ta qisqa javob varianti (tugma sifatida ko'rsatiladi), masalan: 'Birinchi marta', 'Bir yil ichida takroran'")


class NotaryAnswer(BaseModel):
    xulosa: str = Field(description="Savolga bir gapli qisqa javob (asosiy fikr), 20 so'zdan oshmasin. Summa bo'lsa so'mda yoz. [1] kabi havolalarsiz va Markdown belgilarisiz")
    javobgarlik: str = Field(description="Vaziyat uchun javobgarlik turi: \"ma'muriy\", \"jinoiy\", \"ma'muriy yoki jinoiy (holatga qarab)\", \"fuqarolik-huquqiy\", \"intizomiy\" yoki bo'sh qator (javobgarlikka oid savol bo'lmasa)")
    javob: str = Field(description="To'liq va aniq javob, 3-5 xatboshi (\\n\\n bilan ajratilgan): savolning HAR BIR qismiga javob, muddatlar, shartlar va istisnolar bilan. Muhim atama va raqamlarni **qalin** qil. Manbaga [1], [2] ko'rinishida havola qil")
    hujjatlar: list[str] = Field(description="Taqdim etish kerak bo'lgan hujjatlar ro'yxati (manbada bo'lsa, aks holda bo'sh)")
    qadamlar: list[str] = Field(description="Bosqichma-bosqich harakatlar (manbada bo'lsa, aks holda bo'sh)")
    narx: str = Field(description="Davlat boji, to'lov yoki jarima miqdori so'mda va BHMda (masalan: '**1 320 000 so'm** (BHMning 3 baravari)'), har bir holat alohida qatorda; manbada bo'lmasa bo'sh qator")
    aniqlashtirish: list[Aniqlashtirish] = Field(description="Javob holatga bog'liq bo'lsa (takroriylik, sharoit, shaxs toifasi), 0-2 ta aniqlashtiruvchi savol; bog'liq bo'lmasa bo'sh ro'yxat")
    manbalar: list[int] = Field(description="Javobda foydalanilgan manba raqamlari")
    topilmadi: bool = Field(description="Manbalarda javob yo'q bo'lsa true")
    keyingi_savollar: list[str] = Field(description="Foydalanuvchi keyin berishi mumkin bo'lgan 2-3 ta savol")


class DocExplain(BaseModel):
    savolga_javob: str = Field(description="Foydalanuvchi hujjat haqida savol bergan bo'lsa — hujjat matni asosida to'liq va aniq javob (Markdown, muhim joylari **qalin**); savol bo'lmasa bo'sh qator")
    hujjat_turi: str = Field(description="Hujjat turi, masalan: ishonchnoma, oldi-sotdi shartnomasi")
    qisqacha: str = Field(description="Hujjat mazmuni sodda tilda, to'liq: 4-8 gap. Hujjatdagi barcha muhim ma'lumot (sana, summa, muddat, predmet, shartlar) aniq ko'rsatilsin. Muhim ism, muddat va huquqlarni **qalin** qil (Markdown)")
    tomonlar: list[str] = Field(description="Hujjatdagi tomonlar va ularning roli")
    huquq_majburiyatlar: list[str] = Field(description="Hujjat beradigan asosiy huquq va majburiyatlar")
    muddat: str = Field(description="Amal qilish muddati, ko'rsatilmagan bo'lsa bo'sh qator")
    etibor: list[str] = Field(description="Fuqaro e'tibor berishi kerak bo'lgan muhim jihatlar va xavflar")
    keyingi_qadam: str = Field(description="Fuqaroga tavsiya etiladigan keyingi qadam")


class SearchPlan(BaseModel):
    sorovlar: list[str] = Field(description="Qonun bazasidan qidirish uchun 1-3 ta mustaqil, to'liq qidiruv so'rovi")


def qa_system() -> str:
    bhm = f"{config.BHM_SOM:,}".replace(",", " ")
    ex3 = f"{3 * config.BHM_SOM:,}".replace(",", " ")
    return f"""Sen "Huquqiy yordamchi" platformasining yordamchisisan. O'zbekiston fuqarolariga huquqiy savollar
bo'yicha sodda tilda javob berasan: notarial harakatlar, oila (nikoh, ajrashish, aliment), mehnat, uy-joy, yer,
soliq, bojxona, iste'molchilar huquqlari, pensiya, yo'l harakati, ma'muriy va jinoiy javobgarlik va boshqalar.

Joriy bazaviy hisoblash miqdori (BHM): {bhm} so'm.

Qat'iy qoidalar:
1. FAQAT berilgan manbalardagi ma'lumotga tayan. Manbada yo'q narsani o'ylab topma.
2. Har bir muhim fikrdan keyin manba raqamini [1] ko'rinishida yoz.
3. Jarima, boj yoki to'lov BHMga nisbatan berilgan bo'lsa, uni ALBATTA so'mga hisoblab yoz: avval so'm, keyin qavsda
   BHM ulushi. Masalan: "BHMning uch baravari" = **{ex3} so'm** (BHMning 3 baravari). Vergul emas, bo'shliq ishlat.
   Hisoblash: BHM × baravar yoki BHM × foiz / 100. "Ikkidan bir baravari" = 0,5 baravar. Raqamlarni minglik
   bo'shliq bilan yoz ({bhm}). Bir nechta holat bo'lsa (birinchi marta / takroran), har birining summasini alohida yoz.
   MUHIM: summani FAQAT aynan shu qoidabuzarlik tavsiflangan modda qismidagi "— ... miqdorida jarima" so'zlaridan ol.
   Boshqa moddalar yoki qismlardagi summalarni aralashtirma. Manbadagi so'zni aynan o'gir: "uch baravari" = 3,
   "besh baravari" = 5, "ikkidan bir baravari" = 0,5. Ishonching komil bo'lmasa, summani yozma.
4. Savol bir nechta qismdan iborat bo'lsa (masalan, "jarima va ball"), HAR BIR qismga alohida javob ber.
   Biror qism bo'yicha manbada faqat qisman ma'lumot bo'lsa, borini ayt va nima yetishmasligini ochiq yoz.
5. Javob holatga bog'liq bo'lsa (birinchi marta yoki takroran, kim sodir etgan, qanday sharoitda), eng ko'p
   uchraydigan holat bo'yicha javob ber, boshqa holatlarni qisqa eslatib o't va "aniqlashtirish" ga savol qo'y.
6. Manbalarda javob bo'lmasa: topilmadi=true, buni ochiq ayt va yurist yoki tegishli organga murojaat qilishni tavsiya qil.
7. Huquqqa aloqasi yo'q savollarga qisqa javob berib, platforma huquqiy masalalarga ixtisoslashganini ayt.
8. Huquqiy atamalarni oddiy so'zlar bilan tushuntir. O'zbek tilida, lotin alifbosida yoz.
9. "hujjatlar" va "qadamlar" ni faqat savol biror harakatni bajarishga oid bo'lsa to'ldir, aks holda bo'sh qoldir.
10. Ikki tomon o'rtasidagi nizo bo'lsa (qo'shni, oila, iste'mol, qarz), sudsiz hal qilish — mediatsiya imkoniyatini eslatib o't.
11. Yozish uslubi: qisqa gaplar, do'stona va ishonchli ohang. "javob" da muhim atamalar, muddatlar va
    summalarni **qalin** qil (Markdown). Sarlavha (#), jadval yoki kod bloklarini ishlatma.
    Hujjatlar va qadamlarni "javob" ichida takrorlama — ular alohida maydonlarda.
    Ro'yxat elementlari qisqa bo'lsin (bitta qator), oxirida nuqta qo'yma.
12. JAVOBGARLIK TURINI O'ZING ANIQLA. Foydalanuvchi "jarima qancha", "qanday ma'muriy chora" deb so'rasa ham, savolning
    so'zlariga emas, VAZIYATNING O'ZIGA baho ber: bu Ma'muriy javobgarlik to'g'risidagi kodeks bo'yicha huquqbuzarlikmi
    yoki Jinoyat kodeksi bo'yicha jinoyatmi. Chegarani belgilovchi omillar: zarar yoki mulk qiymati (BHMga nisbatan),
    shu turdagi huquqbuzarlik uchun ma'muriy jazodan keyin BIR YIL ichida takrorlash, oqibatlar (tan jarohati, o'lim,
    katta zarar), qurol, guruh bo'lib, mansab mavqeidan foydalanish va h.k.
    - Vaziyat jinoiy javobgarlikka olib kelsa: "xulosa" va "javob" boshida buni ochiq ayt (masalan: "Bu holat ma'muriy
      emas, jinoiy javobgarlikka sabab bo'ladi"), Jinoyat kodeksi moddasini va undagi jazolarni miqdori bilan yoz
      (jarima, axloq tuzatish ishlari, ozodlikni cheklash, ozodlikdan mahrum qilish). "narx" ga jinoiy jarimani yoz.
    - Chegara holatga bog'liq bo'lsa: ikkala holatni (ma'muriy va jinoiy) alohida ko'rsat va qaysi omil hal qilishini
      ayt; "aniqlashtirish" ga o'sha omil haqida savol qo'y (masalan, zarar miqdori, avval jazolanganmi).
13. "Jarima ko'rsatilmagan" yoki "topilmadi" deyishdan oldin manbalarni tekshir: boshqa jazo turi, bog'liq modda yoki
    jinoiy javobgarlik moddasi bo'lsa — o'shani tushuntir. topilmadi=true faqat manbalarda mavzuga oid hech narsa yo'q bo'lsa.
14. Suhbat kontekstini hisobga ol: "qancha jarima bo'ladi?", "bunga nima qilishadi?" kabi qisqa savollar oldingi
    xabarlarda tasvirlangan hodisaga tegishli — javobni aynan o'sha hodisa bo'yicha ber.
"""


PLAN_SYSTEM = """Sen qonun bazasidan qidiruv uchun so'rov tuzuvchi yordamchisan. Foydalanuvchi savolini 1-3 ta
mustaqil qidiruv so'roviga aylantir:
- savolda bir nechta qism bo'lsa (masalan, "jarima va ball"), har bir qism uchun alohida so'rov yoz;
- oldingi suhbat bo'lsa, kontekstni so'rovga qo'sh ("takroran bo'lsa-chi?" -> "qizil chiroqdan takroran o'tganlik uchun jarima");
- qonun atamalaridan foydalan (masalan, "qizil chiroq" -> "svetoforning taqiqlovchi signali");
- har bir so'rov qisqa (3-12 so'z), o'zbek lotin alifbosida."""


EXPLAIN_SYSTEM = """Sen notarial va huquqiy hujjatlarni oddiy fuqaroga sodda tilda tushuntiruvchi yordamchisan.
Yuklangan hujjatni (PDF bo'lsa — BARCHA sahifalarini) diqqat bilan o'qi va mazmunini to'liq, aniq tushuntir:
sana, summa, muddat, predmet, tomonlarning huquq va majburiyatlari, javobgarlik va bekor qilish shartlarini
hujjatdagi qiymatlar bilan aynan ko'rsat. Har bir ro'yxatda hujjatdagi barcha muhim bandlarni sanab o't. Hujjatda yo'q narsani o'ylab topma; o'qib bo'lmagan
joylarni "o'qib bo'lmadi" deb ko'rsat. Shaxsiy ma'lumotlarni (pasport raqami, JSHSHIR) javobda takrorlama.
O'zbek tilida, lotin alifbosida yoz."""


COLLECTION = "huquq"


def _clip(text: str, limit: int) -> str:
    """Matnni so'z chegarasida qisqartiradi; ortiqcha bo'sh joylar olib tashlanadi (token tejash)."""
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit * 0.8 else limit] + " …"


def _sources_block(chunks: list[rag.Chunk]) -> str:
    # Har bir manba config.SOURCE_CHARS belgigacha qisqartiriladi — kirish tokenlari (va xarajat) kamayadi.
    # Mahalliy bo'lak matni boshida hujjat nomi takrorlanadi — label da bor, shuning uchun olib tashlanadi.
    def body(c: rag.Chunk) -> str:
        text = c.text[len(c.doc) + 1:].lstrip(". \n") if c.text.startswith(c.doc) else c.text
        return _clip(text, config.SOURCE_CHARS)
    return "\n\n".join(f"[{i}] {c.label}{' — ' + c.title if c.title else ''}\n{body(c)}" for i, c in enumerate(chunks, 1))


def _mock_answer(chunks: list[rag.Chunk]) -> NotaryAnswer:
    return NotaryAnswer(
        xulosa="Demo rejim: bu namunaviy javob.",
        javob=(
            "Demo rejim: AI kaliti ulanmagan. Quyida savolingizga eng mos topilgan qonun moddalari "
            "ko'rsatilgan [1]. Kalit qo'shilgach, AI ular asosida aniq javob tuzadi."
            if chunks
            else "Demo rejim: AI kaliti ulanmagan va qonun bazasi hali yuklanmagan."
        ),
        hujjatlar=[], qadamlar=[], narx="", aniqlashtirish=[],
        manbalar=[1] if chunks else [],
        topilmadi=not chunks,
        keyingi_savollar=["Ishonchnoma qancha muddatga beriladi?", "Merosni rasmiylashtirish uchun nima kerak?"],
    )


# Og'zaki ibora -> qonundagi atama (AI'siz, tekin "semantik" kengaytirish). Embedding ishlatilmagani uchun
# qidiruv faqat so'zlarga tayanadi — eng ko'p uchraydigan og'zaki iboralar shu yerda qonun tiliga o'giriladi.
SYNONYMS = [
    (r"qizil chiroq|svetofor", "svetoforning taqiqlovchi signali chorraha"),
    (r"prava|haydovchilik guvohnoma|guvohnomadan mahrum", "transport vositasini boshqarish huquqidan mahrum etish"),
    (r"ishdan (?:bo.?shat|haydash|chiqar)", "mehnat shartnomasini ish beruvchining tashabbusi bilan bekor qilish"),
    (r"ishdan (?:ket|bo.?sha)", "mehnat shartnomasini xodimning tashabbusi bilan bekor qilish"),
    (r"ajras|ajrash|razvod", "nikohdan ajratish"),
    (r"maosh|oylik|ish haqi", "mehnatga haq to'lash ish haqi"),
    (r"otpusk|ta.?til", "mehnat ta'tili"),
    (r"meros|vasiyat", "meros vasiyatnoma merosxo'r"),
    (r"kvartira|uy[- ]joy|uyni sot", "uy-joy oldi-sotdi shartnomasi"),
    (r"ijara|kvartirant", "mulk ijarasi shartnomasi"),
    (r"qarz|qarzdor|pulni qaytar", "qarz shartnomasi qarz oluvchi majburiyat"),
    (r"pasport|zagran", "biometrik pasport"),
    (r"propiska|ro.?yxatga tur", "doimiy yashash joyi bo'yicha ro'yxatga olish"),
    (r"shtraf", "jarima"),
    (r"kamar|remen", "xavfsizlik kamaridan foydalanish qoidalariga rioya etmaslik transport vositalaridan foydalanish qoidalarini buzish"),
    (r"shlem|kaska", "motoshlemlardan foydalanish qoidalariga rioya etmaslik"),
    (r"telefon(?:da)? gaplash|rul(?:da)? telefon", "transport vositasini boshqarish vaqtida haydovchilarning telefondan foydalanishi"),
    (r"tezlik|tez yur|skorost", "belgilangan harakat tezligini oshirib yuborish"),
    (r"tex ?ko.?rik|texosmotr|sug.?urta|osago", "majburiy texnik ko'rikdan o'tkazilmagan transport vositasi sug'urta polisi"),
    (r"o.?g.?irla|o.?g.?ri|kissavur", "o'g'rilik oz miqdorda talon-toroj qilish o'g'irlash"),
    (r"urib|urdi|urish|do.?pposla|kaltak|jarohat", "tan jarohati yetkazish bezorilik"),
    (r"mast|alkogol|ichib", "transport vositalarini mastlik holatida boshqarish"),
    (r"pora", "pora olish pora berish"),
    (r"aldab|aldash|firib|moshennik", "firibgarlik"),
    (r"haqorat|so.?kdi|tuhmat", "haqorat tuhmat"),
    (r"narkotik|giyohvand|nasha", "giyohvandlik vositalari"),
    (r"bezori|janjal|mushtlash", "bezorilik"),
    (r"buzilgan tovar|sifatsiz|qaytarib ber", "sifatsiz tovar iste'molchi huquqlari"),
]


def _expand(text: str) -> str:
    extra = [term for pat, term in SYNONYMS if re.search(pat, text, re.IGNORECASE)]
    return f"{text} {' '.join(extra)}" if extra else text


# Javobgarlikka oid savol: ma'muriy VA jinoiy moddalar alohida qidiriladi (chegarani model o'zi baholaydi)
LIABILITY_RE = re.compile(r"jarima|shtraf|javobgar|jazo|chora|qamo|sud qil|nima bo.?ladi|nima qilishadi|ushla|"
                          r"jinoy|ma.?muriy|huquqbuzar|o.?g.?irla|urib|urdi|pora|firib|mast|bezori|narkotik",
                          re.IGNORECASE)


SPLIT_RE = re.compile(r"\?|;|\s(?:va|hamda|shuningdek|yoki)\s", re.IGNORECASE)


def _local_plan(question: str) -> list[str]:
    """Tekin usul: savolni "?", "va", "hamda" bo'yicha qismlarga ajratadi (AI chaqiruvisiz)."""
    parts = [p.strip(" ,.") for p in SPLIT_RE.split(question)]
    parts = [p for p in parts if len(p.split()) >= 2]
    return parts[:3] if len(parts) > 1 else []


def _plan_queries(question: str, history: list[dict]) -> list[str]:
    """Murakkab yoki davomiy savolni mustaqil qidiruv so'rovlariga ajratadi.
    Standart holatda mahalliy (tekin) usul; QUERY_PLAN_LLM=1 bo'lsa — AI bilan (qo'shimcha token sarflaydi).
    Xato bo'lsa — asl savol bilan davom etiladi."""
    if not config.QUERY_PLAN_LLM or config.MOCK_MODE:
        return _local_plan(question)
    if not (history or MULTI_RE.search(question)):
        return []
    last = [h.get("text", "")[:400] for h in history[-2:]]
    prompt = (("Oldingi suhbat:\n" + "\n".join(last) + "\n\n") if last else "") + f"Savol: {question}"
    try:
        plan = ai.generate_json(system=PLAN_SYSTEM, prompt=prompt, schema=SearchPlan,
                                model=None if config.VOICELAB else config.FAST_MODEL,
                                mock=lambda: SearchPlan(sorovlar=[question]), temperature=0, max_tokens=400)
        return [q.strip() for q in plan.sorovlar if q.strip()][:3]
    except ai.AIError:
        log.warning("Qidiruv rejasini tuzib bo'lmadi, asl savol ishlatiladi")
        return []


def _same_text(a: rag.Chunk, b: rag.Chunk) -> bool:
    """Bir xil modda ikki bazada ham bo'lishi mumkin (mahalliy kodeks va lex.uz) — takroriy manba token yeydi."""
    art_a, art_b = rag._base_article(a.article), rag._base_article(b.article)
    if art_a and art_a == art_b:  # bir xil modda raqami va hujjat nomi mos (yozuvi/tahriri farq qilsa ham)
        da, db = set(lexdb.norm_tokens(a.doc.split(" (")[0])), set(lexdb.norm_tokens(b.doc.split(" (")[0]))
        small, big = (da, db) if len(da) <= len(db) else (db, da)
        if small and len(small & big) / len(small) >= 0.75:
            return True
    na, nb = lexdb.norm_tokens(a.text[-400:]), lexdb.norm_tokens(b.text[-400:])
    if not na or not nb:
        return False
    return len(set(na) & set(nb)) / len(set(na) | set(nb)) > 0.7


def retrieve(question: str, history: list[dict]) -> list[rag.Chunk]:
    """Savol uchun manbalarni yig'adi. Ikki baza:
      * mahalliy indeks — asosiy kodekslar (data/laws, moddalararo havolalar bilan);
      * lex.uz to'liq bazasi (lexdb) — amaldagi ~24 ming akt: farmon, qaror, buyruq, nizomlar.
    Natija navbatma-navbat birlashtiriladi, takrorlar olib tashlanadi, soni config.MAX_SOURCES bilan cheklanadi."""
    index = rag.get_index()
    queries = _plan_queries(question, history)
    # Kontekst: qisqa savol ("qancha jarima?") oldingi xabarlardagi hodisaga tegishli bo'lishi mumkin
    prev = [h.get("text", "")[:300] for h in history if h.get("role") == "user"][-2:]
    base_q = " ".join(prev + [question])
    base_q, queries = _expand(base_q), [_expand(q) for q in queries]
    liability = bool(LIABILITY_RE.search(base_q))
    lists = [index.search(base_q, collection=COLLECTION)]
    lists += [index.search(q, k=3, collection=COLLECTION) for q in queries]
    lists.append(lexdb.search([base_q, *queries]))

    # Navbatma-navbat birlashtirish: har bir so'rovning eng yaxshi natijalari kiradi
    chunks: list[rag.Chunk] = []
    for rank in range(max((len(x) for x in lists), default=0)):
        for lst in lists:
            if rank < len(lst) and lst[rank] not in chunks and not any(_same_text(lst[rank], c) for c in chunks):
                chunks.append(lst[rank])
    chunks = chunks[: max(config.MAX_SOURCES - 2, 3)]

    # Javobgarlik savoli: Ma'muriy javobgarlik kodeksi va Jinoyat kodeksidan alohida eng mos moddalar —
    # model hodisa ma'muriy chegaradan o'tib, jinoiy javobgarlikka aylanishini baholay olsin
    if liability:
        for prefix in ("Mamuriy javobgarlik", "Jinoyat kodeksi"):
            for c in index.search(base_q, k=2, collection=COLLECTION, doc_prefix=prefix):
                if c not in chunks and not any(_same_text(c, x) for x in chunks):
                    chunks.append(c)

    # Bog'liq moddalar: eng muhim manbalarga havola qiluvchi moddalar (masalan, 128⁴ -> 34¹ jarima ballari)
    for c in list(chunks[:2]):
        for r in index.related(c, limit=1):
            if r not in chunks:
                chunks.append(r)

    # Narx so'ralgan bo'lsa, davlat boji stavkalarini alohida qidiramiz (jadval umumiy qidiruvda yo'qolib qoladi)
    if FEE_RE.search(base_q) and re.search(r"notar|ishonchnoma|shartnoma|hadya|meros|boj", base_q, re.IGNORECASE):
        fees = index.search("Notarial harakatlarni amalga oshirganlik uchun davlat boji: " + question, k=2,
                            collection=COLLECTION, doc_prefix="Davlat boji", article_prefix="Ilova")
        chunks += [c for c in fees if c not in chunks]
    return chunks[: config.MAX_SOURCES + (3 if liability else 0)]


def prepare(question: str, history: list[dict]) -> tuple[list[rag.Chunk], str]:
    """Savol uchun tegishli moddalarni topadi va modelga beriladigan so'rovni tuzadi."""
    chunks = retrieve(question, history)
    hist = "\n".join(
        f"{'Foydalanuvchi' if h.get('role') == 'user' else 'Yordamchi'}: {_clip(h.get('text', ''), config.HISTORY_CHARS)}"
        for h in history[-config.HISTORY_TURNS:]
    ) if config.HISTORY_TURNS > 0 else ""
    prompt = f"""Manbalar:
{_sources_block(chunks) if chunks else '(manba topilmadi)'}

{('Oldingi suhbat:' + chr(10) + hist + chr(10)) if hist else ''}
Foydalanuvchi savoli: {question}"""
    return chunks, prompt


# --- Summalarni manba bilan solishtirish -------------------------------------
# AI ba'zan to'g'ri moddani topib, raqamni adashtiradi (masalan, "uch baravari" o'rniga "5 baravari").
# Javobdagi har bir BHM ulushi va so'mdagi summa manbalardagi qiymatlar bilan tekshiriladi.
_UZ_NUM = {"bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9,
           "o'n": 10, "yigirma": 20, "o'ttiz": 30, "qirq": 40, "ellik": 50, "oltmish": 60, "yetmish": 70,
           "sakson": 80, "to'qson": 90}
_NUM = r"(\d+(?:[.,]\d+)?|[a-z']+(?:\s+[a-z']+){0,2})"
_BHM_RE = re.compile(rf"(?:bhm(?:ning)?|bazaviy hisoblash miqdori(?:ning)?)\s+{_NUM}(?:\s*(?:dan|-)\s*{_NUM})?\s*(foiz|baravar)")
_SOM_RE = re.compile(r"(\d{1,3}(?:[\s ,]\d{3})+|\d{4,})\s*so'm")


def _norm(text: str) -> str:
    return re.sub(r"[ʻʼ‘’`]", "'", text.lower()).replace("*", "")


def _uz_number(s: str) -> float | None:
    s = s.strip()
    m = re.fullmatch(r"(\S+)dan\s+bir", s)  # "ikkidan bir" = 1/2
    if m and m.group(1) in _UZ_NUM:
        return 1 / _UZ_NUM[m.group(1)]
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return float(s.replace(",", "."))
    total, cur = 0.0, 0.0
    for w in s.split():
        if w == "yuz":
            cur = (cur or 1) * 100
        elif w == "ming":
            total, cur = total + (cur or 1) * 1000, 0
        elif w in _UZ_NUM:
            cur += _UZ_NUM[w]
        else:
            return None
    return total + cur or None


def _bhm_multipliers(text: str) -> set[float]:
    out = set()
    for m in _BHM_RE.finditer(_norm(text)):
        k = 0.01 if m.group(3) == "foiz" else 1
        for g in (m.group(1), m.group(2)):
            v = _uz_number(g) if g else None
            if v:
                out.add(round(v * k, 4))
    return out


def check_amounts(result: NotaryAnswer, chunks: list[rag.Chunk]) -> str:
    """Javobdagi summalar manbalarda bormi? Mos kelmasa — ogohlantirish matnini qaytaradi."""
    answer = f"{result.narx}\n{result.javob}\n{result.xulosa}"
    src_mult = set().union(*(_bhm_multipliers(c.text) for c in chunks)) if chunks else set()
    src_soms = {int(re.sub(r"\D", "", s)) for c in chunks for s in _SOM_RE.findall(_norm(c.text))}
    allowed = {round(m * config.BHM_SOM) for m in src_mult} | src_soms
    bad = [m for m in _bhm_multipliers(answer) if m not in src_mult]
    bad += [v for v in (int(re.sub(r"\D", "", s)) for s in _SOM_RE.findall(_norm(answer)))
            if v >= 1000 and v not in allowed and all(abs(v - a) > 1000 for a in allowed)]
    if bad:
        log.warning("Javobdagi summa manbada topilmadi: %s", bad)
        return ("Javobdagi summa manbadagi raqam bilan mos kelmadi. Iltimos, quyidagi manba matnini o'zingiz "
                "tekshiring yoki savolni qayta bering.")
    return ""


def finalize(result: NotaryAnswer, chunks: list[rag.Chunk]) -> dict:
    used = [i for i in dict.fromkeys(result.manbalar) if 1 <= i <= len(chunks)]
    warning = check_amounts(result, chunks)
    return {
        **result.model_dump(),
        "ogohlantirish": warning,
        "manbalar": [
            {"raqam": i, "hujjat": chunks[i - 1].doc, "modda": chunks[i - 1].article, "baza": chunks[i - 1].collection,
             "sarlavha": chunks[i - 1].title, "matn": chunks[i - 1].text[:1500], "url": chunks[i - 1].url}
            for i in used
        ],
        "baza_tayyor": rag.get_index().ready,
        "bhm": config.BHM_SOM,
        "mock": config.MOCK_MODE,
    }


# --- Javoblar keshi ------------------------------------------------------------
# Bir xil savol (suhbat kontekstisiz) qayta berilsa — AI chaqirilmaydi, token sarflanmaydi.
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(question: str, history: list[dict]) -> str | None:
    if history or config.MOCK_MODE or config.ANSWER_CACHE_SIZE <= 0:
        return None  # suhbat davomidagi savol kontekstga bog'liq — keshlanmaydi
    norm = " ".join(lexdb.norm_tokens(question))
    return hashlib.sha1(f"{config.BHM_SOM}|{norm}".encode()).hexdigest() if norm else None


def _cache_get(key: str | None) -> dict | None:
    if not key:
        return None
    with _cache_lock:
        item = _cache.get(key)
        if not item or time.time() - item[0] > config.ANSWER_CACHE_TTL:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return item[1]


def _cache_put(key: str | None, value: dict) -> None:
    if not key or value.get("ogohlantirish"):  # shubhali javob keshlanmaydi
        return
    with _cache_lock:
        _cache[key] = (time.time(), value)
        while len(_cache) > config.ANSWER_CACHE_SIZE:
            _cache.popitem(last=False)


def answer(question: str, history: list[dict]) -> dict:
    key = _cache_key(question, history)
    cached = _cache_get(key)
    if cached:
        return {**cached, "keshdan": True}
    chunks, prompt = prepare(question, history)
    result = ai.generate_json(system=qa_system(), prompt=prompt, schema=NotaryAnswer, mock=lambda: _mock_answer(chunks))
    out = finalize(result, chunks)
    _cache_put(key, out)
    return out


def answer_stream(question: str, history: list[dict]):
    """Javobni oqim bilan beradi: ("delta", matn_bo'lagi) ... va oxirida ("done", natija)."""
    key = _cache_key(question, history)
    cached = _cache_get(key)
    if cached:
        yield "done", {**cached, "keshdan": True}
        return
    chunks, prompt = prepare(question, history)
    buf = ""
    for delta in ai.stream_json(system=qa_system(), prompt=prompt, schema=NotaryAnswer, mock=lambda: _mock_answer(chunks)):
        if delta == ai.RESET:  # boshqa model qaytadan yozadi
            buf = ""
            yield "reset", None
            continue
        buf += delta
        yield "delta", delta
    try:
        result = ai.parse_lenient(NotaryAnswer, buf)
    except Exception as e:
        raise ai.AIError("AI javobini o'qib bo'lmadi. Qayta urinib ko'ring.") from e
    out = finalize(result, chunks)
    _cache_put(key, out)
    yield "done", out


def explain(files: list[ai.FilePart], question: str = "", history: list[dict] | None = None) -> dict:
    mock = lambda: DocExplain(  # noqa: E731
        savolga_javob="",
        hujjat_turi="Ishonchnoma (demo)",
        qisqacha="Demo rejim: AI kaliti ulanmagan. Kalit qo'shilgach, yuklangan hujjat shu yerda sodda tilda tushuntiriladi.",
        tomonlar=["Ishonch bildiruvchi — huquqni beruvchi shaxs", "Vakil — nomidan harakat qiluvchi shaxs"],
        huquq_majburiyatlar=["Vakil avtomobilni boshqarish huquqiga ega"],
        muddat="[hujjatda ko'rsatilgan muddat]",
        etibor=["Ishonchnomada sotish huquqi berilganmi — tekshiring", "Ishonchnomani istalgan vaqtda bekor qilish mumkin"],
        keyingi_qadam="Savollaringiz bo'lsa, notariusga murojaat qiling.",
    )
    hist = "\n".join(f"{'Foydalanuvchi' if h.get('role') == 'user' else 'Yordamchi'}: {_clip(h.get('text', ''), 300)}"
                     for h in (history or [])[-config.HISTORY_TURNS:])
    prompt = (("Oldingi suhbat:\n" + hist + "\n\n") if hist else "") + (
        f"Foydalanuvchi savoli: {question}\nHujjatni tushuntir va savolga hujjat asosida javob ber." if question
        else "Ushbu hujjatni tushuntirib ber.")
    result = ai.generate_json(
        system=EXPLAIN_SYSTEM, prompt=prompt, schema=DocExplain, files=files, mock=mock,
        max_tokens=3500,
    )
    return {**result.model_dump(), "mock": config.MOCK_MODE}
