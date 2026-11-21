"""1-modul: "Murojaat ustasi" — rasmiy ariza, shikoyat va takliflar."""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from . import ai, config, rag


# --- Ma'lumotnoma -----------------------------------------------------------
@lru_cache
def organs() -> dict:
    return json.loads(config.ORGANS_FILE.read_text(encoding="utf-8"))


def category(cat_id: str) -> dict:
    cats = organs()["kategoriyalar"]
    return next((c for c in cats if c["id"] == cat_id), next(c for c in cats if c["id"] == "boshqa"))


# Murojaatni ko'rib chiqish muddatlari (kunlarda). Qonun matni bilan tekshiring.
DEADLINES = {"ariza": 15, "shikoyat": 15, "taklif": 30}
# Murojaat 2-4 xatboshi — 2500 token yetarli (o'ylash tokenlari bilan birga)
APPEAL_MAX_TOKENS = min(config.MAX_OUTPUT_TOKENS, 2500)


# --- AI javobi sxemasi ------------------------------------------------------
class AppealAI(BaseModel):
    turi: Literal["ariza", "shikoyat", "taklif"] = Field(description="Murojaat turi")
    kategoriya_id: str = Field(description="Ro'yxatdagi kategoriya id si")
    mavzu: str = Field(description="Murojaat mavzusi, 5-10 so'z")
    matn: str = Field(description="Murojaatning asosiy matni, rasmiy uslubda, xatboshilar \\n\\n bilan ajratilgan")
    talablar: list[str] = Field(description="Fuqaro aniq nimani so'rayapti, 1-4 band")
    ilovalar: list[str] = Field(description="Har bir yuklangan fayl uchun qisqa tavsif (ilova sifatida)")
    savollar: list[str] = Field(description="Murojaatni kuchaytirish uchun fuqarodan so'raladigan 0-3 ta aniqlashtiruvchi savol")
    maslahatlar: list[str] = Field(description="Fuqaroga 1-3 ta amaliy maslahat")
    mediatsiya: bool = Field(description="Muammo ikki shaxs o'rtasidagi nizo bo'lib, mediatsiya orqali hal qilish mumkinmi")


SYSTEM = """Sen "Huquqiy yordamchi" platformasining yordamchisisan. Vazifang — O'zbekiston fuqarosining
oddiy so'zlar bilan aytgan muammosini davlat organiga yuboriladigan RASMIY murojaatga aylantirish.

Qoidalar:
1. "Jismoniy va yuridik shaxslarning murojaatlari to'g'risida"gi Qonun talablariga mos yoz:
   murojaat mazmuni aniq, faktlarga asoslangan, odobli va rasmiy uslubda bo'lsin.
2. Turini to'g'ri aniqla:
   - ariza: huquq yoki qonuniy manfaatni ro'yobga chiqarishda ko'maklashish so'rovi, yoki muammo haqida xabar;
   - shikoyat: buzilgan huquqni tiklash talabi (birovning harakati yoki harakatsizligi ustidan);
   - taklif: davlat va jamiyat faoliyatini yaxshilash bo'yicha tavsiya.
3. Faqat fuqaro aytgan faktlardan foydalan. Hech narsani o'ylab topma. Noma'lum ma'lumot o'rniga
   kvadrat qavsda joy qoldir, masalan: [sana], [manzil], [murojaat raqami].
4. "matn" faqat murojaatning asosiy qismi: salomlashuv yoki "Hurmatli ..." bilan boshlanmasin,
   kimga/kimdan rekvizitlari, sarlavha, sana va imzo YOZILMASIN — ularni tizim o'zi qo'shadi.
   Matn tuzilmasi: vaziyat bayoni -> muammoning oqibati -> (bo'lsa) avval qilingan harakatlar -> aniq talab.
   Oxirgi xatboshi "Yuqoridagilarni inobatga olib, Sizdan ..." ko'rinishida talab bilan tugasin.
5. Agar fuqaro rasm yoki hujjat yuklagan bo'lsa, ulardagi ma'lumotdan foydalan va matnda
   "ilova qilingan fotosurat(lar)/hujjat(lar)" ga havola qil.
6. kategoriya_id faqat berilgan ro'yxatdan bo'lsin.
7. Barcha matn o'zbek tilida, lotin alifbosida (o', g' harflari apostrof bilan).
8. Shaxsni haqoratlovchi yoki tahdidli iboralarni rasmiy, betaraf ifodaga aylantir.
9. Fuqaro "qanday yozaman", "namuna kerak" desa ham, tushuntirish emas, AYNAN o'sha hujjatning
   tayyor matnini uning nomidan, birinchi shaxsda yoz (masalan, ishdan bo'shash to'g'risidagi
   arizada: "...o'z xohishimga ko'ra [sana]dan boshlab egallab turgan lavozimimdan ozod etishingizni so'rayman.").
   Bunday hollarda murojaat oluvchisi ish beruvchi, maktab yoki boshqa tashkilot bo'lishi mumkin.
10. Matn qisqa va lo'nda bo'lsin: odatda 2-4 xatboshi.
"""


def _categories_prompt() -> str:
    return "\n".join(f"- {c['id']}: {c['nomi']} ({c['misollar']})" for c in organs()["kategoriyalar"])


def _mock(description: str, files: list[ai.FilePart]) -> AppealAI:
    return AppealAI(
        turi="ariza",
        kategoriya_id="obodonlashtirish",
        mavzu="Ko'cha yoritgichlarini ta'mirlash to'g'risida",
        matn=(
            "Men [manzil] hududida istiqomat qilaman. Uch oydan buyon mahallamizdagi ko'cha "
            "yoritgichlari ishlamayapti.\n\n"
            "Natijada kechki va tungi vaqtda aholi, ayniqsa bolalar va keksalarning xavfsiz "
            "harakatlanishi qiyinlashgan, jinoyat sodir etilishi xavfi ortgan.\n\n"
            "Mazkur muammo yuzasidan avval og'zaki murojaat qilingan bo'lsa-da, hozirgacha "
            "chora ko'rilmagan.\n\n"
            "Yuqoridagilarni inobatga olib, Sizdan mahallamizdagi ko'cha yoritgichlarini qisqa "
            "muddatda ta'mirlash choralarini ko'rishingizni so'rayman."
        ),
        talablar=["Ko'cha yoritgichlarini ta'mirlash", "Ko'rilgan choralar haqida yozma javob berish"],
        ilovalar=[f"Fotosurat ({f.name or 'rasm'})" for f in files],
        savollar=[
            "Muammo aniq qaysi ko'cha yoki uylar atrofida?",
            "Avval qachon va kimga murojaat qilgansiz?",
        ],
        maslahatlar=[
            "Muammoni tasdiqlovchi fotosuratlarni ilova qiling.",
            "Javob muddati o'tsa, yuqori organga shikoyat qilishingiz mumkin.",
        ],
        mediatsiya=False,
    )


@lru_cache
def templates() -> list[dict]:
    return json.loads((config.APP_DIR / "data" / "templates.json").read_text(encoding="utf-8"))


def from_template(template_id: str) -> dict | None:
    """Tayyor shablonni AI'siz, draft() natijasi bilan bir xil ko'rinishda qaytaradi."""
    t = next((x for x in templates() if x["id"] == template_id), None)
    if not t:
        return None
    result = AppealAI(
        turi=t["turi"], kategoriya_id=t["kategoriya_id"], mavzu=t["mavzu"], matn=t["matn"],
        talablar=[], ilovalar=[], savollar=[], maslahatlar=t.get("maslahatlar", []), mediatsiya=False,
    )
    out = _finalize(result)
    out["kategoriya"] = {**out["kategoriya"], "organ": t.get("kimga") or out["kategoriya"]["organ"]}
    out["shablon"] = t["nomi"]
    return out


def _prompt(description: str, answers: list[dict], files: list[ai.FilePart]) -> str:
    qa = "\n".join(
        f"- Savol: {str(a.get('savol', ''))[:300]}\n  Javob: {str(a.get('javob', ''))[:500]}"
        for a in answers[:6] if a.get("javob")
    )
    # Murojaatlar qonunidan 2 ta eng mos modda, qisqartirilgan holda (token tejash)
    law = rag.get_index().search(description, k=2, collection="murojaat")
    law_block = "\n\n".join(f"{c.label}\n{c.text[len(c.doc) + 1:].strip()[:900]}" for c in law)
    head = ("Murojaatlar to'g'risidagi qonundan tegishli moddalar (talablarga rioya qil):\n" + law_block + "\n\n") if law_block else ""
    return f"""{head}Kategoriyalar ro'yxati:
{_categories_prompt()}

Fuqaroning muammosi (o'z so'zlari bilan):
\"\"\"{description.strip()}\"\"\"
{('Fuqaroning aniqlashtiruvchi savollarga javoblari:' + chr(10) + qa) if qa else ''}
Yuklangan fayllar soni: {len(files)}.

Rasmiy murojaatni tuz."""


def _finalize(result: AppealAI) -> dict:
    cat = category(result.kategoriya_id)
    days = DEADLINES[result.turi]
    return {
        **result.model_dump(),
        "kategoriya": cat,
        "kanallar": organs()["kanallar"],
        "muddat_kun": days,
        "muddat_sana": (date.today() + timedelta(days=days)).isoformat(),
        "mock": config.MOCK_MODE,
    }


def draft(*, description: str, answers: list[dict], files: list[ai.FilePart]) -> dict:
    result = ai.generate_json(
        system=SYSTEM, prompt=_prompt(description, answers, files), schema=AppealAI,
        files=files, mock=lambda: _mock(description, files), max_tokens=APPEAL_MAX_TOKENS,
    )
    return _finalize(result)


def draft_stream(*, description: str, answers: list[dict], files: list[ai.FilePart]):
    """Murojaatni oqim bilan tayyorlaydi: ("delta", bo'lak) ... va oxirida ("done", natija)."""
    buf = ""
    for delta in ai.stream_json(
        system=SYSTEM, prompt=_prompt(description, answers, files), schema=AppealAI,
        files=files, mock=lambda: _mock(description, files), max_tokens=APPEAL_MAX_TOKENS,
    ):
        if delta == ai.RESET:  # boshqa model qaytadan yozadi
            buf = ""
            yield "reset", None
            continue
        buf += delta
        yield "delta", delta
    try:
        result = ai.parse_lenient(AppealAI, buf)
    except Exception as e:
        raise ai.AIError("AI javobini o'qib bo'lmadi. Qayta urinib ko'ring.") from e
    yield "done", _finalize(result)
