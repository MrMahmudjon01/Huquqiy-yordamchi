// Narxlar sahifasi. Tarif narxlari va imkoniyatlarini shu yerda o'zgartiring.

const YEARLY_DISCOUNT = 0.2;

const PLANS = [
  {
    id: "free", name: "Bepul", price: 0, tagline: "Boshlash uchun",
    features: [`Kuniga ${FREE_DAILY} ta so'rov`, "Matn orqali murojaat tuzish", "Notarius savol-javob", "Word yuklab olish"],
    cta: "Hozir foydalanish", href: "/murojaat",
  },
  {
    id: "premium", name: "Premium", price: 29000, tagline: "Fuqarolar uchun", featured: true,
    features: ["Cheksiz so'rovlar", "Ovoz va rasm orqali murojaat", "Hujjatni sodda tilda tushuntirish", "Javob muddati eslatmalari (Telegram)", "Kuchliroq AI modeli", "Murojaatlar tarixi barcha qurilmalarda"],
    cta: "Tez orada",
  },
  {
    id: "business", name: "Biznes", price: 199000, tagline: "Yuristlar va tashkilotlar uchun",
    features: ["Premium'dagi barcha imkoniyatlar", "5 tagacha xodim", "Jamoa uchun shablonlar", "Mijozlar murojaatlari bazasi", "API orqali ulanish", "Ustuvor qo'llab-quvvatlash"],
    cta: "Tez orada",
  },
];

const COMPARE = [
  ["Kunlik so'rovlar", `${FREE_DAILY} ta`, "Cheksiz", "Cheksiz"],
  ["Murojaat tuzish (ariza, shikoyat, taklif)", true, true, true],
  ["Notarius savol-javob, manba bilan", true, true, true],
  ["Word / PDF yuklab olish", true, true, true],
  ["Ovoz orqali kiritish", false, true, true],
  ["Rasm va hujjat yuklash", false, true, true],
  ["Hujjatni tushuntirish", false, true, true],
  ["Muddat eslatmalari", false, true, true],
  ["Jamoa va xodimlar", false, false, "5 tagacha"],
  ["API", false, false, true],
];

const FAQ = [
  ["To'lov tizimi qachon ishga tushadi?", "Hozir barcha imkoniyatlar bepul ochiq. Obuna Click, Payme, Uzcard va Humo orqali rasmiy ishga tushirilganda faollashadi."],
  ["Bepul tarif doim bepul bo'ladimi?", "Ha. Asosiy imkoniyatlar — murojaat tuzish va notarial savollarga javob — fuqarolar uchun bepul qoladi."],
  ["Obunani bekor qilish mumkinmi?", "Ha, istalgan vaqtda. Obuna joriy to'lov davri oxirigacha amal qiladi."],
  ["Ma'lumotlarim xavfsizmi?", "Yuklangan fayllar serverda saqlanmaydi, shaxsiy ma'lumotlar (F.I.Sh., manzil) AI ga yuborilmaydi — ular faqat hujjatga yoziladi."],
];

let period = "month";
const som = (n) => n.toLocaleString("ru-RU").replace(/,/g, " ") + " so'm";
const monthly = (p) => Math.round((period === "year" ? p.price * (1 - YEARLY_DISCOUNT) : p.price) / 1000) * 1000;

function renderPlans() {
  $("#plans").innerHTML = PLANS.map((p) => `
    <div class="card plan p-6 sm:p-7 ${p.featured ? "plan-featured md:-mt-3 md:mb-[-0.75rem]" : ""}">
      ${p.featured ? `<span class="badge bg-indigo-600 text-white absolute -top-3 left-1/2 -translate-x-1/2 shadow">Eng mashhur</span>` : ""}
      <div class="text-sm font-semibold ${p.featured ? "text-indigo-600" : "text-slate-500"}">${esc(p.tagline)}</div>
      <h3 class="text-xl font-extrabold mt-1">${esc(p.name)}</h3>
      <div class="mt-5 flex items-end gap-1.5">
        <span class="text-4xl font-extrabold tracking-tight">${p.price ? som(monthly(p)).replace(" so'm", "") : "0"}</span>
        <span class="text-slate-500 mb-1">so'm${p.price ? " / oy" : ""}</span>
      </div>
      <div class="text-xs text-slate-500 h-4 mt-1">${p.price && period === "year" ? `Yiliga ${som(monthly(p) * 12)} · ${som(p.price * 12 - monthly(p) * 12)} tejaysiz` : ""}</div>
      <ul class="mt-6 space-y-2.5 text-sm flex-1">
        ${p.features.map((f) => `<li class="flex gap-2.5"><span class="w-5 h-5 rounded-full ${p.featured ? "bg-indigo-600 text-white" : "bg-indigo-50 text-indigo-600"} flex items-center justify-center text-[0.65rem] flex-none mt-px">✓</span><span>${esc(f)}</span></li>`).join("")}
      </ul>
      ${p.href
        ? `<a href="${p.href}" class="btn btn-ghost w-full mt-7">${esc(p.cta)}</a>`
        : `<button type="button" class="btn ${p.featured ? "btn-primary" : "btn-ghost"} w-full mt-7" data-soon="${esc(p.name)}">${esc(p.cta)}</button>`}
    </div>`).join("");
}

function renderCompare() {
  const cell = (v) => v === true ? '<span class="text-emerald-600 font-bold">✓</span>' : v === false ? '<span class="text-slate-300">—</span>' : `<span class="font-medium">${esc(v)}</span>`;
  $("#compare").innerHTML = COMPARE.map(([name, ...vals]) =>
    `<tr class="feature-row"><td class="text-slate-700">${esc(name)}</td>${vals.map((v, i) => `<td class="text-center ${i === 1 ? "bg-indigo-50/40" : ""}">${cell(v)}</td>`).join("")}</tr>`).join("");
}

function renderFaq() {
  $("#faq").innerHTML = FAQ.map(([q, a]) => `
    <details class="card px-5 py-4 group">
      <summary class="cursor-pointer font-semibold list-none flex justify-between gap-4">${esc(q)}<span class="text-slate-400 group-open:rotate-45 transition text-xl leading-none">+</span></summary>
      <p class="text-sm text-slate-600 mt-3 leading-relaxed">${esc(a)}</p>
    </details>`).join("");
}

function renderRevenue() {
  const prem = +$("#r-premium").value, biz = +$("#r-biz").value;
  $("#v-premium").textContent = prem.toLocaleString("ru-RU");
  $("#v-biz").textContent = biz.toLocaleString("ru-RU");
  const month = prem * PLANS[1].price + biz * PLANS[2].price;
  $("#rev-month").textContent = som(month);
  $("#rev-year").textContent = som(month * 12);
}

$$("[data-period]").forEach((b) => b.addEventListener("click", () => {
  period = b.dataset.period;
  $$("[data-period]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  renderPlans();
}));
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-soon]");
  if (b) toast(`${b.dataset.soon} tarifi to'lov tizimi ishga tushirilganda faollashadi. Hozircha barcha imkoniyatlar bepul.`, "info");
});
["#r-premium", "#r-biz"].forEach((s) => $(s).addEventListener("input", renderRevenue));

renderHeader("narxlar");
renderPlans();
renderCompare();
renderFaq();
renderRevenue();
reveal($$("#plans > .card"), 110);
showStatus();
