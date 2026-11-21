// 2-modul: Notarius navigatori

const STARTERS = [
  ["", "Mashinani ishonchnoma bilan berish uchun nima kerak?"],
  ["", "Merosni rasmiylashtirish uchun qanday hujjatlar kerak?"],
  ["", "Ish beruvchi meni ogohlantirmasdan ishdan bo'shata oladimi?"],
  ["", "Aliment miqdori qanday belgilanadi?"],
  ["", "Qizil chiroqqa o'tganlik uchun jarima qancha?"],
  ["", "Sifatsiz tovarni necha kun ichida qaytarish mumkin?"],
];

// --- Tablar ---
function showTab(name) {
  $$(".tab").forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle("bg-indigo-600", on);
    t.classList.toggle("text-white", on);
    t.classList.toggle("text-slate-600", !on);
    t.setAttribute("aria-selected", on);
  });
  // Faqat sahifada bor bo'limlar (avvalgi "explain" tabi yagona chatga birlashtirilgan)
  document.querySelectorAll("[id^='tab-']").forEach((el) => el.classList.toggle("hidden", el.id !== `tab-${name}`));
  if (name === "bhm") calcBhm();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// --- BHM kalkulyatori ---
let bhmUnit = "foiz";
const BHM_QUICK = [["foiz", 10], ["foiz", 25], ["foiz", 50], ["baravar", 1], ["baravar", 2], ["baravar", 5], ["baravar", 10]];
$("#bhm-quick").innerHTML = BHM_QUICK.map(([u, n]) =>
  `<button type="button" class="chip" data-q-unit="${u}" data-q-n="${n}">${n}${u === "foiz" ? "%" : "×"}</button>`).join("");

function calcBhm() {
  const n = parseFloat(($("#bhm-n").value || "0").replace(",", ".")) || 0;
  const k = bhmUnit === "foiz" ? n / 100 : n;
  $$("[data-bhm]").forEach((el) => (el.textContent = fmtSom(BHM)));
  $("#bhm-label").textContent = `BHMning ${n} ${bhmUnit === "foiz" ? "foizi" : "baravari"}`;
  $("#bhm-result").textContent = fmtSom(k * BHM);
  $$("[data-unit]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.unit === bhmUnit));
}
$("#bhm-n").addEventListener("input", calcBhm);
$$("[data-unit]").forEach((b) => b.addEventListener("click", () => { bhmUnit = b.dataset.unit; calcBhm(); }));
$("#bhm-quick").addEventListener("click", (e) => {
  const b = e.target.closest("[data-q-unit]");
  if (!b) return;
  bhmUnit = b.dataset.qUnit;
  $("#bhm-n").value = b.dataset.qN;
  calcBhm();
});
{
  const want = new URLSearchParams(location.search).get("tab");
  showTab(want && document.getElementById(`tab-${want}`) ? want : "ask");   // eski ?tab=explain havolasi ham ishlaydi
}

// --- Savol-javob ---
const history = [];
const chat = $("#chat");
const q = $("#q");
let busy = false;

$("#starter").innerHTML = STARTERS.map(([icon, t]) =>
  `<button type="button" class="chip !rounded-2xl text-left flex gap-2 items-start !py-2.5" data-ask="${esc(t)}"><span>${esc(t)}</span></button>`).join("");

function followUps(list) {
  const items = (list || []).map(clean).filter(Boolean).slice(0, 3);
  if (!items.length) return "";
  return `<section class="pt-4 border-t border-slate-100">
    <h4 class="section-title">Yana so'rashingiz mumkin</h4>
    <div class="flex flex-wrap gap-2 mt-2.5">${items.map((t) => `<button type="button" class="chip text-left" data-ask="${esc(t)}">${esc(t)}</button>`).join("")}</div>
  </section>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-ask]");
  if (b) ask(b.dataset.ask);
});

setupRecorder($("#mic"), (text) => {
  q.value = text;
  autosize();
  q.focus();
});

function autosize() {
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 160) + "px";
}
q.addEventListener("input", autosize);
$("#send").addEventListener("click", () => ask(q.value));
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    ask(q.value);
  }
});

function userBubble(text) {
  const el = document.createElement("div");
  el.className = "flex justify-end";
  el.innerHTML = `<div class="bubble-user max-w-[85%]">${esc(text)}</div>`;
  chat.appendChild(el);
  reveal([el]);
}

function aiRow() {
  const el = document.createElement("div");
  el.className = "flex gap-3 items-start";
  el.innerHTML = `<span class="avatar-ai mt-1">§</span><div class="flex-1 min-w-0"><div class="card p-5 sm:p-6" data-body></div></div>`;
  chat.appendChild(el);
  reveal([el]);
  return $("[data-body]", el);
}

// Joriy suhbat (ro'yxatdan o'tgan foydalanuvchi uchun server tarixga yozadi — saytda ham, botda ham ko'rinadi)
let chatId = null;
const welcomeHTML = $("#welcome")?.outerHTML || "";

async function ask(text) {
  text = (text || "").trim();
  const files = [...(docPicker?.files || [])];
  if ((!text && !files.length) || busy) return;
  busy = true;
  q.value = "";
  autosize();
  $("#welcome")?.remove();
  $("#history-panel")?.classList.add("hidden");
  userBubble(text || "" + files.map((f) => f.name).join(", "));
  const body = aiRow();
  body.scrollIntoView({ behavior: "smooth", block: "center" });
  const steps = progressSteps(body, files.length
    ? ["Hujjat o'qilmoqda", "Tomonlar va huquqlar aniqlanmoqda", "Sodda tilda tushuntirilmoqda"]
    : ["Savol tahlil qilinmoqda", "Qonun bazasidan moddalar qidirilmoqda", "Tegishli moddalar o'rganilmoqda", "Javob yozilmoqda"]);
  $("#send").disabled = true;
  const fd = new FormData();
  fd.append("question", text);
  if (chatId) fd.append("chat_id", chatId);
  files.forEach((f) => fd.append("files", f));
  docPicker?.clear();

  // Javob kelayotgan paytda xulosa va matn jonli ko'rsatiladi
  let json = "", live = null;
  const paint = rafThrottle(() => {
    const xulosa = partialField(json, "xulosa");
    const javob = partialField(json, "javob");
    if (xulosa === null && javob === null) return;
    if (!live) {
      steps.stop();
      body.innerHTML = `<p class="text-lg font-semibold leading-snug text-slate-900" data-x></p><div class="prose-ai mt-3" data-j></div>`;
      live = { x: $("[data-x]", body), j: $("[data-j]", body) };
    }
    live.x.textContent = clean(xulosa || "").replace(/\s*\[[\d,\s–-]+\]/g, "");
    live.j.innerHTML = md((javob || "").replace(/\s?\[[\d,\s–-]*$/, ""), (n) => `<span class="cite">${n}</span>`);
    live.j.classList.add("typing-caret");
  });

  try {
    const r = await streamPost("/api/chat/stream", fd, {
      onDelta: (d) => { json += d; paint(); },
      onReset: () => { json = ""; paint(); },
    });
    steps.stop();
    if (r.chat_id) chatId = r.chat_id;
    history.push({ role: "user", text }, { role: "assistant", text: r.javob || r.qisqacha || "" });
    usage.consume();
    if (r.tur === "hujjat") await renderDoc(body, r);
    else await renderAnswer(body, r, { typed: !!live });
  } catch (e) {
    steps.stop();
    body.innerHTML = `<div class="flex gap-3 text-red-700"><div><p class="font-semibold">Javob olib bo'lmadi</p><p class="text-sm mt-1">${esc(e.message)}</p>
      <button class="btn btn-ghost mt-3 !py-2 text-sm" data-ask="${esc(text)}">Qayta urinish</button></div></div>`;
  } finally {
    busy = false;
    $("#send").disabled = false;
  }
}

async function renderAnswer(body, r, { typed = false } = {}) {
  const src = r.manbalar || [];
  const nums = new Set(src.map((s) => s.raqam));
  const cite = (n) => (nums.has(n) ? `<button type="button" class="cite" data-cite="${n}" title="Manbani ko'rish">${n}</button>` : "");
  const docs = (r.hujjatlar || []).map(clean).filter(Boolean);
  const steps = (r.qadamlar || []).map(clean).filter(Boolean);

  body.innerHTML = `
    ${r.topilmadi ? `<div class="badge bg-amber-50 text-amber-700 mb-3">Bazada aniq javob topilmadi</div>` : ""}
    ${!r.baza_tayyor ? `<div class="badge bg-amber-50 text-amber-700 mb-3">Qonun bazasi yuklanmagan</div>` : ""}
    ${r.ogohlantirish ? `<div class="rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm px-3 py-2 mb-3">${esc(r.ogohlantirish)}</div>` : ""}
    ${r.xulosa ? `<p class="text-lg font-semibold leading-snug text-slate-900" data-type>${esc(clean(r.xulosa).replace(/\s*\[[\d,\s–-]+\]/g, ""))}</p>` : ""}
    <div class="prose-ai mt-3" data-type>${bhmToSom(md(r.javob, cite), r.bhm)}</div>
    <div data-sections class="space-y-6 mt-6">
      ${docs.length ? `<section>
        <div class="flex items-center justify-between"><h4 class="section-title">Kerakli hujjatlar</h4><span class="text-xs text-slate-500" data-progress>0 / ${docs.length} tayyor</span></div>
        <div class="mt-2 -mx-1">${docs.map((h) => `<label class="check-item"><input type="checkbox"><span class="text-sm">${esc(h)}</span></label>`).join("")}</div>
      </section>` : ""}
      ${steps.length ? `<section>
        <h4 class="section-title">Tartib</h4>
        <ol class="timeline mt-3 text-sm text-slate-700">${steps.map((s) => `<li>${md(s, cite).replace(/^<p>|<\/p>$/g, "")}</li>`).join("")}</ol>
      </section>` : ""}
      ${r.narx && !r.ogohlantirish ? `<section class="rounded-2xl p-4 sm:p-5 bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-100">
        <h4 class="section-title !text-emerald-700">To'lov / jarima miqdori</h4>
        <div class="prose-ai mt-2 text-emerald-950">${bhmToSom(md(r.narx, cite), r.bhm)}</div>
        <p class="text-xs text-emerald-700/80 mt-2">BHM — bazaviy hisoblash miqdori: <b>${fmtSom(r.bhm || BHM)}</b>. So'mdagi summa taxminiy.</p>
      </section>` : ""}
      ${(r.aniqlashtirish || []).length ? `<section class="rounded-2xl p-4 sm:p-5 bg-indigo-50/60 border border-indigo-100">
        <h4 class="section-title !text-indigo-700">Aniqroq javob uchun</h4>
        ${r.aniqlashtirish.map((a) => `<div class="mt-3">
          <p class="text-sm font-medium text-slate-800">${esc(clean(a.savol))}</p>
          <div class="flex flex-wrap gap-2 mt-2">${(a.variantlar || []).map((v) =>
            `<button type="button" class="chip !bg-white" data-ask="${esc(`${clean(a.savol)} — ${clean(v)}`)}">${esc(clean(v))}</button>`).join("")}</div>
        </div>`).join("")}
      </section>` : ""}
      ${src.length ? `<section>
        <h4 class="section-title">Manbalar</h4>
        <div class="mt-2 space-y-2">${src.map((s) => `
          <details class="source" data-src="${s.raqam}">
            <summary>
              <span class="cite !cursor-default">${s.raqam}</span>
              <span class="text-sm leading-snug"><b>${esc(s.hujjat)}</b>${s.modda ? `, ${esc(s.modda)}` : ""}${s.sarlavha ? `<span class="text-slate-500"> — ${esc(s.sarlavha)}</span>` : ""}</span>
            </summary>
            <div class="px-4 pb-4">
              <div class="whitespace-pre-line text-slate-600 text-xs leading-relaxed max-h-72 overflow-auto border-l-2 border-indigo-100 pl-3">${esc(s.matn)}</div>
              ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" class="inline-flex mt-3 text-xs font-semibold text-indigo-600 hover:underline">Lex.uz da to'liq o'qish ↗</a>` : ""}
            </div>
          </details>`).join("")}</div>
      </section>` : ""}
      ${followUps(r.keyingi_savollar)}
    </div>`;

  const sections = $("[data-sections]", body);
  const parts = [...sections.children];
  parts.forEach((p) => p.classList.add("reveal"));
  // Oqim bilan kelgan matn qayta "yozilmaydi"
  if (!typed) for (const el of $$("[data-type]", body)) await typewrite(el, { cps: el.tagName === "P" && el.classList.contains("text-lg") ? 90 : 220 });
  reveal(parts, typed ? 90 : 140);

  // Tayyor hujjatlar hisoblagichi
  const boxes = $$(".check-item input", body);
  boxes.forEach((b) => b.addEventListener("change", () => {
    $("[data-progress]", body).textContent = `${boxes.filter((x) => x.checked).length} / ${boxes.length} tayyor`;
  }));
}

// Hujjat (rasm/PDF) tushuntirishi — yagona chatda fayl yuborilganda
async function renderDoc(body, r, { typed = false } = {}) {
  const list = (items) => `<ul class="mt-2 space-y-2">${items.map((x) => `<li class="flex gap-2 text-sm"><span class="text-indigo-400 mt-0.5">●</span><span>${md(clean(x)).replace(/^<p>|<\/p>$/g, "")}</span></li>`).join("")}</ul>`;
  body.innerHTML = `
    <div class="flex flex-wrap items-center gap-2">
      <span class="badge bg-indigo-50 text-indigo-700">${esc(r.hujjat_turi || "Hujjat")}</span>
      ${r.muddat ? `<span class="badge bg-slate-100 text-slate-700">${esc(r.muddat)}</span>` : ""}
    </div>
    ${r.savolga_javob ? `<div class="rounded-2xl bg-indigo-50/60 border border-indigo-100 p-4 mt-4"><h4 class="section-title !text-indigo-700">Savolingizga javob</h4><div class="prose-ai mt-2" data-type>${md(r.savolga_javob)}</div></div>` : ""}
    <div class="prose-ai mt-4 text-[1.02rem]" data-type>${md(r.qisqacha || "")}</div>
    <div class="grid sm:grid-cols-2 gap-4 mt-4" data-more>
      ${r.tomonlar?.length ? `<div class="card p-5"><h4 class="section-title">Tomonlar</h4>${list(r.tomonlar)}</div>` : ""}
      ${r.huquq_majburiyatlar?.length ? `<div class="card p-5"><h4 class="section-title">Huquq va majburiyatlar</h4>${list(r.huquq_majburiyatlar)}</div>` : ""}
      ${r.etibor?.length ? `<div class="card p-5 sm:col-span-2 !bg-amber-50/70 !border-amber-200"><h4 class="section-title !text-amber-700">E'tibor bering</h4>${list(r.etibor)}</div>` : ""}
      ${r.keyingi_qadam ? `<div class="card p-5 sm:col-span-2 flex gap-3 items-start"><div><h4 class="section-title">Keyingi qadam</h4><p class="text-sm mt-1.5 text-slate-700">${esc(clean(r.keyingi_qadam))}</p></div></div>` : ""}
    </div>`;
  const more = [...$("[data-more]", body).children];
  more.forEach((m) => m.classList.add("reveal"));
  if (!typed) for (const el of $$("[data-type]", body)) await typewrite(el, { cps: 200 });
  reveal(more, typed ? 60 : 120);
}

// --- Suhbatlar tarixi va yangi suhbat -------------------------------------------
const fmtTime = (ts) => { const d = new Date(ts * 1000); return `${fmtDate(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

async function loadChats() {
  const box = $("#history-list");
  if (!ME.user) {
    box.innerHTML = `<div class="p-3 text-slate-600">Suhbatlar tarixi ro'yxatdan o'tgan foydalanuvchilar uchun saqlanadi.
      <button type="button" data-login class="btn btn-primary !py-1.5 !px-3 text-sm ml-1">Kirish</button></div>`;
    return;
  }
  box.innerHTML = `<div class="p-3 text-slate-500">Yuklanmoqda…</div>`;
  try {
    const list = await (await api("/api/chats")).json();
    box.innerHTML = list.length ? list.map((c) => `
      <div class="flex items-center gap-2 rounded-xl px-3 py-2 hover:bg-slate-50 ${c.id === chatId ? "bg-indigo-50" : ""}">
        <button type="button" class="flex-1 min-w-0 text-left" data-open-chat="${c.id}">
          <div class="truncate font-medium text-slate-800">${c.source === "telegram" ? "" : ""}${esc(c.title)}</div>
          <div class="text-xs text-slate-500">${fmtTime(c.updated)}${c.source === "telegram" ? " · Telegram" : ""}</div>
        </button>
        <button type="button" class="text-slate-400 hover:text-red-600 px-1" title="O'chirish" data-del-chat="${c.id}">O'chirish</button>
      </div>`).join("") : `<div class="p-3 text-slate-500">Hali suhbatlar yo'q. Savol bering — u shu yerda saqlanadi.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="p-3 text-red-700">${esc(e.message)}</div>`;
  }
}

async function openChat(id) {
  try {
    const { messages } = await (await api(`/api/chats/${id}`)).json();
    chatId = id;
    history.length = 0;
    chat.innerHTML = "";
    $("#welcome")?.remove();
    $("#history-panel").classList.add("hidden");
    for (const m of messages) {
      if (m.role === "user") { userBubble(m.text); history.push({ role: "user", text: m.text }); continue; }
      const body = aiRow();
      const p = m.payload || {};
      if (p.tur === "hujjat") await renderDoc(body, p, { typed: true });
      else if (p.javob || p.xulosa) await renderAnswer(body, p, { typed: true });
      else body.innerHTML = `<div class="prose-ai">${md(m.text || "")}</div>`;
      history.push({ role: "assistant", text: m.text });
    }
    chat.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  } catch (e) {
    toast(e.message, "error");
  }
}

function newChat() {
  chatId = null;
  history.length = 0;
  chat.innerHTML = "";
  if (!$("#welcome") && welcomeHTML) chat.insertAdjacentHTML("beforebegin", welcomeHTML);
  $("#history-panel").classList.add("hidden");
  q.focus();
}

$("#history-btn").addEventListener("click", () => {
  const p = $("#history-panel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden")) loadChats();
});
$("#new-chat").addEventListener("click", newChat);
$("#history-list").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open-chat]");
  if (open) return openChat(+open.dataset.openChat);
  const del = e.target.closest("[data-del-chat]");
  if (del && confirm("Suhbat o'chirilsinmi?")) {
    try {
      await api(`/api/chats/${del.dataset.delChat}`, { method: "DELETE" });
      if (+del.dataset.delChat === chatId) newChat();
      loadChats();
    } catch (err) { toast(err.message, "error"); }
  }
});

// Havola bosilganda manbani ochish
chat.addEventListener("click", (e) => {
  const c = e.target.closest("button.cite[data-cite]");
  if (!c) return;
  const d = c.closest("[data-body]").querySelector(`[data-src="${c.dataset.cite}"]`);
  if (!d) return;
  d.closest(".reveal")?.classList.add("in");
  d.open = true;
  d.classList.add("flash");
  setTimeout(() => d.classList.remove("flash"), 1200);
  d.scrollIntoView({ behavior: "smooth", block: "center" });
});

// --- Hujjatni tushuntirish ---
const docPicker = setupFilePicker({ input: $("#doc-files"), list: $("#doc-previews") });

// Alohida "Tushuntirish" tugmasi yagona chatga birlashtirilgan; tugma bo'lmasa — bu blok o'tkazib yuboriladi
// (aks holda xato skriptni to'xtatib, pastdagi renderHeader() — menyu va «Kirish» — ishlamay qolardi)
$("#explain")?.addEventListener("click", async (e) => {
  if (!docPicker.files.length) return toast("Avval hujjat rasmini yoki PDF ni tanlang.", "error");
  const btn = e.currentTarget;
  const out = $("#explain-result");
  setBusy(btn, true, "Hujjat o'qilmoqda…");
  out.innerHTML = `<div class="card p-6"></div>`;
  const steps = progressSteps(out.firstElementChild, ["Hujjat o'qilmoqda", "Tomonlar va huquqlar aniqlanmoqda", "Sodda tilda tushuntirilmoqda"], { every: 3000 });
  out.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const fd = new FormData();
    docPicker.files.forEach((f) => fd.append("files", f));
    const r = await (await api("/api/notary/explain", { method: "POST", body: fd })).json();
    usage.consume();
    steps.stop();
    const list = (items) => `<ul class="mt-2 space-y-2">${items.map((x) => `<li class="flex gap-2 text-sm"><span class="text-indigo-400 mt-0.5">●</span><span>${md(clean(x)).replace(/^<p>|<\/p>$/g, "")}</span></li>`).join("")}</ul>`;
    out.innerHTML = `
      <div class="card p-6 sm:p-7">
        <div class="flex flex-wrap items-center gap-2">
          <span class="badge bg-indigo-50 text-indigo-700">${esc(r.hujjat_turi)}</span>
          ${r.muddat ? `<span class="badge bg-slate-100 text-slate-700">${esc(r.muddat)}</span>` : ""}
        </div>
        <div class="prose-ai mt-4 text-[1.02rem]" data-type>${md(r.qisqacha)}</div>
      </div>
      <div class="grid sm:grid-cols-2 gap-4 mt-4" data-more>
        ${r.tomonlar?.length ? `<div class="card p-5"><h4 class="section-title">Tomonlar</h4>${list(r.tomonlar)}</div>` : ""}
        ${r.huquq_majburiyatlar?.length ? `<div class="card p-5"><h4 class="section-title">Huquq va majburiyatlar</h4>${list(r.huquq_majburiyatlar)}</div>` : ""}
        ${r.etibor?.length ? `<div class="card p-5 sm:col-span-2 !bg-amber-50/70 !border-amber-200"><h4 class="section-title !text-amber-700">E'tibor bering</h4>${list(r.etibor)}</div>` : ""}
        ${r.keyingi_qadam ? `<div class="card p-5 sm:col-span-2 flex gap-3 items-start"><div><h4 class="section-title">Keyingi qadam</h4><p class="text-sm mt-1.5 text-slate-700">${esc(clean(r.keyingi_qadam))}</p></div></div>` : ""}
      </div>`;
    const more = [...$("[data-more]", out).children];
    more.forEach((m) => m.classList.add("reveal"));
    await typewrite($("[data-type]", out), { cps: 200 });
    reveal(more, 120);
  } catch (err) {
    steps.stop();
    out.innerHTML = `<div class="card p-5 text-red-700">${esc(err.message)}</div>`;
  } finally {
    setBusy(btn, false);
  }
});

renderHeader("notarius");
mountUsage();
showStatus();
