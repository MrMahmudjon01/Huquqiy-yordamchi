// 1-modul: Murojaat ustasi

const TEMPLATES = [
  ["Svet yo'q", "Mahallamizda bir necha kundan beri elektr energiyasi yo'q. "],
  ["Yo'l buzilgan", "Uyimiz oldidagi yo'l buzilgan, chuqurlar ko'p. "],
  ["Ish haqi berilmayapti", "Ish joyimda bir necha oydan beri ish haqi to'lanmayapti. "],
  ["Bog'chaga joy", "Farzandimni davlat bog'chasiga joylashtira olmayapman. "],
  ["Sifatsiz tovar", "Do'kondan sotib olgan tovarim sifatsiz chiqdi, sotuvchi pulni qaytarmayapti. "],
  ["Chiqindi", "Mahallamizdan chiqindi muntazam olib ketilmayapti. "],
];
const TITLES = { ariza: "ARIZA", shikoyat: "SHIKOYAT", taklif: "TAKLIF" };
const LS_KEY = "infinity.appeals";
const LS_PERSONAL = "infinity.personal";

let current = null; // AI natijasi
const desc = $("#desc");

// --- localStorage (xavfsiz) ---
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

// --- Shablonlar ---
$("#templates").innerHTML = TEMPLATES.map(([t], i) => `<button type="button" class="chip" data-t="${i}">${esc(t)}</button>`).join("");
$("#templates").addEventListener("click", (e) => {
  const i = e.target.closest("[data-t]")?.dataset.t;
  if (i === undefined) return;
  desc.value = TEMPLATES[i][1];
  updateCount();
  desc.focus();
});

// --- Ovoz va fayllar ---
setupRecorder($("#mic"), (text) => {
  desc.value = (desc.value.trim() ? desc.value.trim() + " " : "") + text;
  updateCount();
});
function updateCount() {
  const n = desc.value.trim().length;
  $("#count").textContent = n ? `${n} belgi` : "";
}
desc.addEventListener("input", updateCount);
const picker = setupFilePicker({ input: $("#files"), list: $("#previews") });

// --- Shaxsiy ma'lumotlar ---
const personal = store.get(LS_PERSONAL, {});
for (const id of ["fio", "manzil", "telefon"]) {
  $("#" + id).value = personal[id] || "";
  $("#" + id).addEventListener("input", () => {
    personal[id] = $("#" + id).value;
    store.set(LS_PERSONAL, personal);
    renderKimdan();
  });
}

// --- So'rov ---
const TURI_STYLE = {
  ariza: ["bg-indigo-50 text-indigo-700", ""],
  shikoyat: ["bg-rose-50 text-rose-700", ""],
  taklif: ["bg-emerald-50 text-emerald-700", ""],
};

async function generate(answers = [], trigger = $("#submit")) {
  if (desc.value.trim().length < 10) return toast("Muammoni batafsilroq yozing.", "error");
  setBusy(trigger, true, "Tayyorlanmoqda…");
  $("#result").innerHTML = `<section class="card p-6 sm:p-7"><h3 class="font-bold mb-4">Murojaat tayyorlanmoqda</h3><div data-steps></div></section>`;
  const steps = progressSteps($("#result [data-steps]"), [
    "Muammo tahlil qilinmoqda",
    "Murojaat turi va mas'ul organ aniqlanmoqda",
    "Qonun talablari tekshirilmoqda",
    "Rasmiy matn yozilmoqda",
  ]);
  if (window.innerWidth < 1024) $("#result").scrollIntoView({ behavior: "smooth" });
  // Matn kelayotgan paytda qog'oz ko'rinishida jonli ko'rsatiladi
  let json = "", live = null;
  const paint = rafThrottle(() => {
    const matn = partialField(json, "matn");
    if (matn === null) return;
    if (!live) {
      steps.stop();
      $("#result").innerHTML = `<section class="card p-4 sm:p-5"><p class="text-sm font-semibold text-slate-600 flex items-center gap-2"><span class="spinner text-indigo-500"></span> Rasmiy matn yozilmoqda…</p></section>
        <div class="paper"><div class="text-center font-bold tracking-wide" data-t></div><div class="text-center text-sm mb-6 italic" data-m></div><div class="p-body" data-b></div></div>`;
      live = { t: $("#result [data-t]"), m: $("#result [data-m]"), b: $("#result [data-b]") };
    }
    live.t.textContent = TITLES[partialField(json, "turi")] || "";
    live.m.textContent = clean(partialField(json, "mavzu") || "");
    const paras = matn.split(/\n+/).map((p) => p.trim()).filter(Boolean);
    live.b.innerHTML = paras.map((p, i) => `<p class="${i === paras.length - 1 ? "typing-caret" : ""}">${esc(p.replace(/\*\*/g, ""))}</p>`).join("");
  });

  try {
    const fd = new FormData();
    fd.append("description", desc.value);
    fd.append("answers", JSON.stringify(answers));
    picker.files.forEach((f) => fd.append("files", f));
    const r = await streamPost("/api/appeal/draft/stream", fd, { onDelta: (d) => { json += d; paint(); }, onReset: () => { json = ""; paint(); } });
    steps.stop();
    current = r;
    current.answers = answers;
    usage.consume();
    await renderResult({ typed: !!live });
  } catch (e) {
    steps.stop();
    $("#result").innerHTML = `<section class="card p-6 text-red-700"><p class="font-semibold">Murojaatni tayyorlab bo'lmadi</p><p class="text-sm mt-1">${esc(e.message)}</p></section>`;
  } finally {
    setBusy(trigger, false);
  }
}
$("#submit").addEventListener("click", () => generate());

// --- Natija ---
async function renderResult({ typed = false } = {}) {
  const r = current;
  const cat = r.kategoriya;
  const qs = r.savollar || [];
  const [turiCls, turiIcon] = TURI_STYLE[r.turi] || TURI_STYLE.ariza;

  $("#result").innerHTML = `
    <section class="card p-5 sm:p-6">
      <div class="flex flex-wrap items-center gap-2">
        <span class="badge ${turiCls} uppercase">${turiIcon} ${esc(r.turi)}</span>
        <span class="badge bg-slate-100 text-slate-700">${esc(cat.nomi)}</span>
        <span class="badge bg-amber-50 text-amber-700 ml-auto">${r.muddat_kun} kun ichida javob</span>
      </div>
      ${r.shablon ? `<p class="mt-4 text-sm rounded-xl bg-amber-50 border border-amber-100 px-3 py-2"><b>${esc(r.shablon)}</b> shabloni. <mark class="ph">[Sariq]</mark> joylarni hujjatda bosib, o'z ma'lumotlaringiz bilan almashtiring.</p>` : ""}
      <label class="block text-xs font-semibold text-slate-500 uppercase tracking-wide mt-5">Kimga yuboriladi</label>
      <input type="text" id="kimga" value="${esc(cat.organ)}" class="mt-1.5 font-semibold">
      <p class="text-xs text-slate-500 mt-1.5">Aniq nomini yozing, masalan: "Yunusobod tumani hokimligi". Muqobil: ${esc(cat.qoshimcha)}</p>
    </section>

    <section>
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
        <span class="text-sm font-semibold text-slate-700">Hujjat <span class="font-normal text-slate-500">— matnni bosib tahrirlang</span></span>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-primary !py-2 text-sm" id="dl-docx">Word</button>
          <button class="btn btn-ghost !py-2 text-sm" id="dl-pdf">PDF</button>
          <button class="btn btn-ghost !py-2 text-sm" id="copy" title="Nusxa olish">⧉</button>
          <button class="btn btn-ghost !py-2 text-sm" id="save" title="Saqlash">Saqlash</button>
        </div>
      </div>
      <div class="paper" id="paper">
        <div class="ml-auto w-[min(100%,20rem)] text-right">
          <div class="font-bold" id="p-kimga"></div>
          <div class="mt-1 whitespace-pre-line" id="p-kimdan"></div>
        </div>
        <div class="text-center font-bold mt-8 tracking-wide">${TITLES[r.turi] || "MUROJAAT"}</div>
        <div class="text-center text-sm mb-6 italic" id="p-mavzu" contenteditable="true">${esc(clean(r.mavzu))}</div>
        <div class="p-body" id="p-body" contenteditable="true">${r.matn.split(/\n+/).map((p) => p.trim()).filter(Boolean).map((p) => `<p>${placeholders(esc(p.replace(/\*\*/g, "")))}</p>`).join("")}</div>
        ${r.ilovalar?.length ? `<div class="mt-5"><b>Ilova:</b><ol class="list-decimal ml-6" id="p-ilova">${r.ilovalar.map((x) => `<li contenteditable="true">${esc(clean(x))}</li>`).join("")}</ol></div>` : ""}
        <div class="mt-10 flex justify-between gap-4 flex-wrap">
          <span>Sana: ${fmtDate(new Date())}</span>
          <span>Imzo: ______________ <span id="p-fio"></span></span>
        </div>
      </div>
    </section>

    <div data-extra class="space-y-5">
      ${qs.length ? `
      <section class="card p-5 sm:p-6 !border-indigo-200 !bg-indigo-50/40">
        <h3 class="font-bold">Murojaatni kuchaytiring</h3>
        <p class="text-sm text-slate-600 mt-1">Javob bersangiz, matn aniqroq va ta'sirliroq bo'ladi. Ixtiyoriy.</p>
        <div class="space-y-3 mt-4" id="qs">
          ${qs.map((q, i) => `<div><label class="text-sm font-medium">${esc(clean(q))}</label><input type="text" data-q="${i}" class="mt-1.5" placeholder="Javobingiz…"></div>`).join("")}
        </div>
        <button id="refine" class="btn btn-primary mt-4">↻ Javoblar bilan yangilash</button>
      </section>` : ""}

      <section class="grid sm:grid-cols-2 gap-5">
        <div class="card p-5">
          <h3 class="section-title">Javob muddati</h3>
          <p class="mt-2"><span class="text-3xl font-extrabold gradient-text">${r.muddat_kun}</span> <span class="font-semibold text-slate-700">kun</span></p>
          <p class="text-sm text-slate-600 mt-1">Bugun yuborsangiz, taxminan <b>${fmtDate(r.muddat_sana)}</b> gacha. Qo'shimcha o'rganish talab etilsa, muddat uzaytirilishi mumkin.</p>
        </div>
        <div class="card p-5">
          <h3 class="section-title">Qanday yuborish mumkin</h3>
          <ul class="mt-3 space-y-2.5 text-sm">${r.kanallar.map((k) => `<li class="flex gap-2"><span class="text-indigo-400">●</span><span><b>${esc(k.nomi)}</b><span class="text-slate-500">${esc(k.manzil)} — ${esc(k.izoh)}</span></span></li>`).join("")}</ul>
        </div>
      </section>

      ${r.maslahatlar?.length ? `<section class="card p-5"><h3 class="section-title">Maslahatlar</h3><ul class="mt-3 space-y-2 text-sm">${r.maslahatlar.map((m) => `<li class="flex gap-2"><span class="text-amber-500"></span><span>${md(clean(m)).replace(/^<p>|<\/p>$/g, "")}</span></li>`).join("")}</ul></section>` : ""}

      ${r.mediatsiya ? `<section class="card p-5 !border-emerald-200 !bg-emerald-50/60 flex gap-3"><div><h3 class="font-bold">Sudsiz hal qilish mumkin</h3><p class="text-sm mt-1 text-slate-700">Bu ikki tomon o'rtasidagi nizoga o'xshaydi. <b>Mediatsiya</b> (betaraf vositachi yordamida kelishuv) sudga qaraganda tezroq va arzonroq bo'lishi mumkin.</p></div></section>` : ""}
    </div>
  `;

  $("#kimga").addEventListener("input", renderKimdan);
  renderKimdan();

  $("#refine")?.addEventListener("click", (e) => {
    const answers = $$("#qs [data-q]").map((el) => ({ savol: qs[+el.dataset.q], javob: el.value.trim() })).filter((a) => a.javob);
    if (!answers.length) return toast("Kamida bitta savolga javob bering.", "error");
    generate([...(current.answers || []), ...answers], e.currentTarget);
  });
  $("#dl-docx").addEventListener("click", downloadDocx);
  $("#dl-pdf").addEventListener("click", printPdf);
  $("#copy").addEventListener("click", copyText);
  $("#save").addEventListener("click", saveAppeal);

  // Animatsiya: avval qog'oz, keyin matn yoziladi (oqim bilan kelgan bo'lsa — yozilmaydi), so'ng qolgan bloklar
  const extra = [...$("[data-extra]", $("#result")).children];
  extra.forEach((x) => x.classList.add("reveal"));
  reveal([...$("#result").children].slice(0, 2), typed ? 0 : 120);
  if (!typed) {
    $("#p-body").contentEditable = "false";
    await typewrite($("#p-body"), { cps: 260 });
    $("#p-body").contentEditable = "true";
  }
  reveal(extra, 120);
}

// [sana], [manzil] kabi to'ldiriladigan joylarni ajratib ko'rsatish
const placeholders = (html) => html.replace(/\[([^\]\n]{1,60})\]/g, '<mark class="ph">[$1]</mark>');

// --- Tayyor shablonlar (AI'siz, bir zumda) ---
async function openTemplates() {
  let list;
  try {
    list = await (await api("/api/appeal/templates")).json();
  } catch (e) {
    return toast(e.message, "error");
  }
  const groups = [...new Set(list.map((t) => t.guruh))];
  const dlg = document.createElement("div");
  dlg.className = "fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-4";
  dlg.innerHTML = `<div class="bg-white w-full sm:max-w-3xl max-h-[88vh] overflow-auto rounded-t-3xl sm:rounded-3xl shadow-2xl p-5 sm:p-7" role="dialog" aria-modal="true" aria-label="Tayyor shablonlar">
      <div class="flex items-start justify-between gap-4">
        <div><h2 class="text-xl font-extrabold">Tayyor shablonlar</h2><p class="text-sm text-slate-600 mt-1">AI'siz, bir zumda ochiladi. Sariq joylarni o'zingizga moslab to'ldiring.</p></div>
        <button type="button" class="btn btn-ghost btn-icon !w-10 !h-10" data-close aria-label="Yopish">✕</button>
      </div>
      ${groups.map((g) => `<h3 class="section-title mt-6">${esc(g)}</h3>
        <div class="grid sm:grid-cols-2 gap-2 mt-2">${list.filter((t) => t.guruh === g).map((t) => `
          <button type="button" class="chip !rounded-2xl text-left flex gap-3 items-center !py-3 !px-4" data-tpl="${esc(t.id)}">
            <span class="text-xl">${t.icon}</span><span class="flex-1"><span class="font-semibold block">${esc(t.nomi)}</span><span class="text-xs text-slate-500 uppercase">${esc(t.turi)}</span></span>
          </button>`).join("")}</div>`).join("")}
    </div>`;
  const close = () => dlg.remove();
  dlg.addEventListener("click", async (e) => {
    if (e.target === dlg || e.target.closest("[data-close]")) return close();
    const id = e.target.closest("[data-tpl]")?.dataset.tpl;
    if (!id) return;
    close();
    try {
      current = await (await api(`/api/appeal/templates/${encodeURIComponent(id)}`)).json();
      current.answers = [];
      await renderResult({ typed: true });
      if (window.innerWidth < 1024) $("#result").scrollIntoView({ behavior: "smooth" });
      toast("Shablon ochildi. Sariq joylarni to'ldiring.", "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  });
  document.addEventListener("keydown", function onKey(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
  });
  document.body.appendChild(dlg);
}
$("#open-templates")?.addEventListener("click", openTemplates);

function renderKimdan() {
  if (!current) return;
  const p = personal;
  $("#p-kimga").textContent = $("#kimga")?.value || "";
  $("#p-kimdan").textContent = [p.fio, p.manzil, p.telefon && "Tel: " + p.telefon].filter(Boolean).join("\n") || "[F.I.Sh., manzil, telefon]";
  $("#p-fio").textContent = p.fio || "[F.I.Sh.]";
}

function collect() {
  return {
    turi: current.turi,
    kimga: $("#kimga").value.trim(),
    mavzu: $("#p-mavzu").innerText.trim(),
    matn: $("#p-body").innerText.split(/\n+/).map((s) => s.trim()).filter(Boolean).join("\n\n"),
    ilovalar: $$("#p-ilova li").map((li) => li.innerText.trim()),
    fio: personal.fio || "",
    manzil: personal.manzil || "",
    telefon: personal.telefon || "",
    sana: fmtDate(new Date()),
  };
}

async function downloadDocx(e) {
  const btn = e.currentTarget;
  setBusy(btn, true, "Tayyorlanmoqda...");
  try {
    const fd = new FormData();
    fd.append("data", JSON.stringify(collect()));
    picker.files.filter((f) => f.type.startsWith("image/")).forEach((f) => fd.append("files", f));
    const blob = await (await api("/api/appeal/docx", { method: "POST", body: fd })).blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${current.turi}.docx`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(btn, false);
  }
}

function printPdf() {
  const area = $("#print-area");
  area.innerHTML = `<div class="paper">${$("#paper").innerHTML}</div>` +
    picker.files.filter((f) => f.type.startsWith("image/"))
      .map((f, i) => `<div style="page-break-before:always;text-align:right;font-weight:bold">${i + 1}-ilova</div><img src="${URL.createObjectURL(f)}" style="max-width:100%;margin-top:12px">`).join("");
  area.querySelectorAll("[contenteditable]").forEach((el) => el.removeAttribute("contenteditable"));
  setTimeout(() => window.print(), 300);
}

async function copyText() {
  const d = collect();
  const text = `${d.kimga}\n${[d.fio, d.manzil, d.telefon].filter(Boolean).join(", ")}\n\n${TITLES[d.turi]}\n${d.mavzu}\n\n${d.matn}\n\nSana: ${d.sana}\nImzo: ________ ${d.fio}`;
  try {
    await navigator.clipboard.writeText(text);
    toast("Matn nusxalandi.", "ok");
  } catch {
    toast("Nusxa olib bo'lmadi.", "error");
  }
}

// --- Mening murojaatlarim ---
function saveAppeal() {
  const d = collect();
  const list = store.get(LS_KEY, []);
  list.unshift({ id: Date.now(), created: new Date().toISOString(), deadline: current.muddat_sana, turi: d.turi, mavzu: d.mavzu, kimga: d.kimga });
  store.set(LS_KEY, list.slice(0, 30));
  renderSaved();
  toast("Murojaat saqlandi. Muddat kuzatib boriladi.", "ok");
}

function renderSaved() {
  const list = store.get(LS_KEY, []);
  const box = $("#saved");
  if (!list.length) {
    box.innerHTML = `<p class="text-sm text-slate-500">Hozircha saqlangan murojaat yo'q.</p>`;
    return;
  }
  const today = new Date(new Date().toDateString());
  box.innerHTML = list.map((a) => {
    const left = Math.ceil((new Date(a.deadline) - today) / 86400000);
    const overdue = left < 0;
    return `<div class="rounded-xl border ${overdue ? "border-red-200 bg-red-50/50" : "border-slate-200"} p-3">
      <div class="flex justify-between gap-2">
        <div class="min-w-0"><div class="text-sm font-semibold truncate">${esc(a.mavzu)}</div>
        <div class="text-xs text-slate-500 truncate">${esc(a.kimga)} · ${fmtDate(a.created)}</div></div>
        <span class="text-xs font-bold whitespace-nowrap ${overdue ? "text-red-600" : "text-indigo-600"}">${overdue ? "Muddat o'tdi" : left + " kun qoldi"}</span>
      </div>
      <div class="flex gap-3 mt-2 text-xs">
        ${overdue ? `<button class="text-red-700 font-semibold" data-escalate="${a.id}">Yuqori organga shikoyat →</button>` : ""}
        <button class="text-slate-500" data-del="${a.id}">O'chirish</button>
      </div>
    </div>`;
  }).join("");
}

$("#saved").addEventListener("click", (e) => {
  const list = store.get(LS_KEY, []);
  const del = e.target.closest("[data-del]")?.dataset.del;
  const esc_ = e.target.closest("[data-escalate]")?.dataset.escalate;
  if (del) {
    store.set(LS_KEY, list.filter((a) => String(a.id) !== del));
    renderSaved();
  }
  if (esc_) {
    const a = list.find((x) => String(x.id) === esc_);
    desc.value = `Men ${fmtDate(a.created)} kuni ${a.kimga}ga "${a.mavzu}" mavzusida ${a.turi} bilan murojaat qilganman. Qonunda belgilangan muddat o'tgan bo'lsa-da, hozirgacha javob olmadim va muammo hal qilinmadi. Yuqori turuvchi organga shikoyat qilmoqchiman.`;
    desc.scrollIntoView({ behavior: "smooth" });
    desc.focus();
  }
});

renderHeader("murojaat");
mountUsage();
renderSaved();
showStatus();
