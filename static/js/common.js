// Umumiy yordamchi funksiyalar

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// --- BHM (bazaviy hisoblash miqdori) -----------------------------------------
let BHM = 440000; // server /api/status orqali yangilaydi
const fmtSom = (n) => Math.round(n).toLocaleString("ru-RU").replace(/[ ,]/g, " ") + " so'm";

// So'z bilan yozilgan sonlar: "uch", "o'n besh", "ikki yuz", "ikkidan bir" (0,5) ...
const UZ_NUM = {
  bir: 1, ikki: 2, uch: 3, "to'rt": 4, besh: 5, olti: 6, yetti: 7, sakkiz: 8, "to'qqiz": 9, "o'n": 10,
  yigirma: 20, "o'ttiz": 30, qirq: 40, ellik: 50, oltmish: 60, yetmish: 70, sakson: 80, "to'qson": 90,
};
function uzNumber(s) {
  s = s.toLowerCase().replace(/[ʻʼ‘’`]/g, "'").trim();
  const frac = /^(\S+)dan\s+bir$/.exec(s); // "ikkidan bir" = 1/2
  if (frac && UZ_NUM[frac[1]]) return 1 / UZ_NUM[frac[1]];
  if (/^\d+(?:[.,]\d+)?$/.test(s)) return parseFloat(s.replace(",", "."));
  let total = 0, cur = 0, ok = false;
  for (const w of s.split(/\s+/)) {
    if (w === "yuz") { cur = (cur || 1) * 100; ok = true; }
    else if (w === "ming") { total += (cur || 1) * 1000; cur = 0; ok = true; }
    else if (UZ_NUM[w] !== undefined) { cur += UZ_NUM[w]; ok = true; }
    else return null;
  }
  return ok ? total + cur : null;
}

// "BHMning 3 baravari", "bazaviy hisoblash miqdorining uch baravari", "BHMning ikkidan bir baravari",
// "BHMning 2 dan 5 baravarigacha", "BHMning 10 foizi" — yoniga so'mdagi summa qo'shiladi.
// Summa allaqachon yozilgan bo'lsa (AI o'zi hisoblagan), qayta qo'shilmaydi.
function bhmToSom(html, bhm = BHM) {
  const NUM = "([\\d.,]+|[a-zʻʼ‘’'`]+(?:\\s+[a-zʻʼ‘’'`]+){0,2})";
  const re = new RegExp(`(?:BHM(?:ning)?|bazaviy hisoblash miqdori(?:ning)?)\\s+${NUM}(?:\\s*(?:dan|-)\\s*${NUM})?\\s*(foizi|baravari)(\\w*)`, "gi");
  return html.replace(re, (m, a, b, unit, ...rest) => {
    const offset = rest[rest.length - 2];
    if (/so(?:'|‘|ʻ|&#39;)m/.test(html.slice(Math.max(0, offset - 80), offset))) return m; // HTMLda ' = &#39;
    const x = uzNumber(a), y = b ? uzNumber(b) : null;
    if (x === null || (b && y === null)) return m;
    const k = /foiz/i.test(unit) ? 0.01 : 1;
    const sum = y !== null ? `${fmtSom(x * k * bhm)} – ${fmtSom(y * k * bhm)}` : fmtSom(x * k * bhm);
    return `${m} <span class="whitespace-nowrap font-semibold text-emerald-700">(≈ ${sum})</span>`;
  });
}

async function api(url, opts = {}) {
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error("Serverga ulanib bo'lmadi. Internet aloqasini tekshiring.");
  }
  if (!res.ok) {
    let msg = `Xatolik (${res.status})`;
    if (res.status === 401 && !url.startsWith("/api/auth")) setTimeout(() => loginWithTelegram(), 50);
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg;
    } catch {}
    throw new Error(msg);
  }
  return res;
}

function toast(msg, type = "info") {
  let box = $("#toasts");
  if (!box) {
    box = document.createElement("div");
    box.id = "toasts";
    box.className = "fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex flex-col gap-2 w-[min(92vw,420px)]";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  const color = type === "error" ? "bg-red-600" : type === "ok" ? "bg-emerald-600" : "bg-slate-800";
  el.className = `${color} text-white text-sm rounded-xl px-4 py-3 shadow-lg`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function setBusy(btn, busy, text) {
  if (busy) {
    btn.dataset.label = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${esc(text || "Kuting...")}`;
  } else {
    btn.disabled = false;
    if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
  }
}

// --- Sarlavha va holat ----------------------------------------------------
function renderHeader(active) {
  const link = (href, label, key) =>
    `<a href="${href}" class="nav-link" ${active === key ? 'aria-current="page"' : ""}>${label}</a>`;
  const TG_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.9 4.3 18.7 19.4c-.2 1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-5 9.1-8.2c.4-.4-.1-.6-.6-.2L6.2 13 1.4 11.5c-1-.3-1-1 .2-1.5L20.6 2.8c.9-.3 1.6.2 1.3 1.5z"/></svg>`;
  const el = document.createElement("header");
  el.className = "app-header";
  el.innerHTML = `<div class="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between gap-2">
      <a href="/" class="flex items-center gap-2.5 font-extrabold text-lg tracking-tight shrink-0"><span class="brand-name">Huquqiy yordamchi</span></a>
      <div class="flex items-center gap-1.5 sm:gap-2.5 min-w-0">
        <nav class="flex gap-0.5 sm:gap-1">${link("/murojaat", "Murojaat", "murojaat")}${link("/notarius", "Savol-javob", "notarius")}${link("/narxlar", "Narxlar", "narxlar")}</nav>
        <div class="flex items-center gap-1.5 ml-1">
          <a id="header-tg-bot" href="https://t.me/HuquqYordamchiUzBot" target="_blank" rel="noopener" class="btn-tg-bot" title="Telegram botga o'tish">
            ${TG_ICON}
            <span class="hidden md:inline">Telegram bot</span>
          </a>
          <span id="auth-slot"></span>
        </div>
      </div>
    </div>`;
  document.body.prepend(el);
  renderFooter();
  initAuth();
}

// --- Ro'yxatdan o'tish / kirish (Telegram orqali) ------------------------------
let ME = { user: null, require_login: false, bot: null };
async function initAuth() {
  try { ME = await (await api("/api/me")).json(); } catch {}
  const tb = $("#topbar-bot");
  if (tb && ME.bot) tb.innerHTML = `Telegram bot: <a href="https://t.me/${esc(ME.bot)}" target="_blank" rel="noopener">@${esc(ME.bot)}</a>`;
  const hb = $("#header-tg-bot");
  if (hb && ME.bot) hb.href = `https://t.me/${esc(ME.bot)}`;
  const slot = $("#auth-slot");
  const USER_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`;
  if (slot) slot.innerHTML = ME.user
    ? `<button type="button" id="logout" class="profile-badge" title="Hisobdan chiqish">${USER_ICON}<span>${esc(ME.user.name || ME.user.username || "Profil")}</span></button>`
    : `<button type="button" data-login class="btn btn-primary !py-1.5 !px-3 text-sm">Kirish</button>`;
  $("#logout")?.addEventListener("click", async () => {
    if (!confirm("Hisobdan chiqasizmi?")) return;
    await api("/api/auth/logout", { method: "POST" });
    location.reload();
  });
  document.dispatchEvent(new CustomEvent("auth", { detail: ME }));
  return ME;
}

let loginPoll = null;
async function loginWithTelegram() {
  if ($("#login-modal")) return;
  const m = document.createElement("div");
  m.id = "login-modal";
  m.className = "fixed inset-0 z-50 flex items-center justify-center p-4 auth-overlay";
  const TG_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="#229ED9" d="M21.9 4.3 18.7 19.4c-.2 1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-5 9.1-8.2c.4-.4-.1-.6-.6-.2L6.2 13 1.4 11.5c-1-.3-1-1 .2-1.5L20.6 2.8c.9-.3 1.6.2 1.3 1.5z"/></svg>`;
  m.innerHTML = `<div class="card auth-card w-full" role="dialog" aria-labelledby="auth-title">
      <h3 id="auth-title">Telegram orqali kirish</h3>
      <p class="auth-sub">Suhbatlar tarixi va murojaatlaringiz saqlanishi uchun Telegram orqali kiring.</p>
      <a data-tg class="btn btn-primary btn-auth" target="_blank" rel="noopener"><span class="tg-dot">${TG_ICON}</span>Telegram bilan davom etish</a>
      <div class="auth-or">YOKI</div>
      <a data-tg-manual class="auth-link" target="_blank" rel="noopener">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="2" width="14" height="20" rx="2.5"/><path d="M11 18h2"/></svg>
        <span>Botni telefonda ochish${ME.bot ? ` — @${esc(ME.bot)}` : ""}</span></a>
      <p data-st class="auth-status">Havola tayyorlanmoqda…</p>
      <p class="auth-status !mt-1">Birinchi marta bo'lsa — botda telefon raqamingizni ulashasiz. Shu sahifa o'zi yangilanadi.</p>
      <button type="button" data-close class="auth-cancel">Bekor qilish</button>
    </div>`;
  document.body.appendChild(m);
  const close = () => { clearInterval(loginPoll); m.remove(); };
  m.addEventListener("click", (e) => { if (e.target === m || e.target.closest("[data-close]")) close(); });
  try {
    const r = await (await api("/api/auth/telegram/start", { method: "POST" })).json();
    $("[data-tg]", m).href = r.url;
    $("[data-tg-manual]", m).href = r.url.replace("https://t.me/", "tg://resolve?domain=").replace("?start=", "&start=");
    $("[data-st]", m).textContent = "Telegram'da tasdiqlashingiz kutilmoqda…";
    const until = Date.now() + 10 * 60 * 1000;
    loginPoll = setInterval(async () => {
      if (Date.now() > until) { $("[data-st]", m).textContent = "Vaqt tugadi. Qayta urinib ko'ring."; return clearInterval(loginPoll); }
      try {
        const p = await (await api(`/api/auth/telegram/poll?code=${encodeURIComponent(r.code)}`)).json();
        if (p.ok) { close(); toast("Xush kelibsiz! Kirdingiz.", "ok"); setTimeout(() => location.reload(), 600); }
      } catch {}
    }, 2000);
  } catch (e) {
    $("[data-st]", m).textContent = e.message;
  }
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-login]")) loginWithTelegram(); });

function renderFooter() {
  const f = document.createElement("footer");
  f.className = "border-t border-slate-200/70 mt-10";
  f.innerHTML = `<div class="max-w-6xl mx-auto px-4 py-8 flex flex-col sm:flex-row gap-4 sm:items-center sm:justify-between text-sm text-slate-500">
      <div><b class="text-slate-700">Huquqiy yordamchi</b></div>
      <nav class="flex flex-wrap gap-x-5 gap-y-2"><a href="/murojaat" class="hover:text-slate-800">Murojaat</a><a href="/notarius" class="hover:text-slate-800">Savol-javob</a><a href="/narxlar" class="hover:text-slate-800">Narxlar</a><a href="https://lex.uz" target="_blank" rel="noopener" class="hover:text-slate-800">Lex.uz ↗</a></nav>
    </div>
    `;
  document.body.appendChild(f);
}

// --- Bepul limit (faqat ko'rsatiladi, hech narsani bloklamaydi) ---------
const FREE_DAILY = 3;
const usage = {
  _read() {
    const today = new Date().toDateString();
    try {
      const u = JSON.parse(localStorage.getItem("infinity.usage")) || {};
      return u.date === today ? u : { date: today, used: 0 };
    } catch { return { date: today, used: 0 }; }
  },
  left() { return Math.max(0, FREE_DAILY - this._read().used); },
  consume() {
    const u = this._read();
    u.used++;
    try { localStorage.setItem("infinity.usage", JSON.stringify(u)); } catch {}
    $$("[data-usage]").forEach(renderUsage);
  },
};

function renderUsage(el) {
  const left = usage.left();
  const dots = Array.from({ length: FREE_DAILY }, (_, i) => `<span class="w-1.5 h-1.5 rounded-full ${i < left ? "bg-indigo-500" : "bg-slate-300"}"></span>`).join("");
  el.innerHTML = left > 0
    ? `<span class="inline-flex gap-1 items-center">${dots}</span><span>Bugun <b>${left}/${FREE_DAILY}</b> bepul so'rov</span><a href="/narxlar" class="font-semibold text-indigo-600 hover:underline">Cheksiz →</a>`
    : `<span class="inline-flex gap-1 items-center">${dots}</span><span>Bepul limit tugadi<span class="text-slate-400 hidden sm:inline"> (demoda cheklanmaydi)</span></span><a href="/narxlar" class="font-semibold text-indigo-600 hover:underline">Premium →</a>`;
}
function mountUsage() { $$("[data-usage]").forEach(renderUsage); }

async function showStatus() {
  const pill = $("#status-pill");
  try {
    const s = await (await api("/api/status")).json();
    if (s.bhm) BHM = s.bhm;
    if (pill) {
      pill.className = `status-pill hidden md:inline-flex ${s.mock ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`;
      pill.innerHTML = `<span class="status-dot"></span>${s.mock ? "Demo rejim" : "AI ulangan"}`;
      pill.title = s.mock ? "AI kaliti ulanmagan: namunaviy javoblar" : `Qonun bazasi: ${s.rag_chunks} ta bo'lak`;
    }
    return s;
  } catch {
    return null;
  }
}

// --- AI matnini formatlash (xavfsiz mini-Markdown) --------------------------
// Qo'llanadi: **qalin**, *kursiv*, "- " / "• " ro'yxat, "1. " raqamli ro'yxat, xatboshilar.
// cite(n) berilsa, [n] havolalari tugmaga aylanadi.
function md(text, cite) {
  const inline = (s) => {
    let h = esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*(?!\s)(.+?)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");
    // [1], [1, 3], [2-4] ko'rinishidagi havolalar
    if (cite) h = h.replace(/\s?\[(\d+(?:\s*[,–-]\s*\d+)*)\]/g, (m, list) =>
      list.split(/\s*,\s*/).flatMap((part) => {
        const [a, b] = part.split(/\s*[–-]\s*/).map(Number);
        return b && b > a && b - a < 10 ? Array.from({ length: b - a + 1 }, (_, i) => a + i) : [a];
      }).map((n) => cite(n) ?? "").join(""));
    return h;
  };
  const blocks = String(text ?? "").replace(/\r/g, "").trim().split(/\n{2,}/);
  return blocks.map((block) => {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length && lines.every((l) => /^[-•*]\s+/.test(l)))
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^[-•*]\s+/, ""))}</li>`).join("")}</ul>`;
    if (lines.length && lines.every((l) => /^\d+[.)]\s+/.test(l)))
      return `<ol>${lines.map((l) => `<li>${inline(l.replace(/^\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`;
    return `<p>${lines.map(inline).join("<br>")}</p>`;
  }).join("");
}

// Ro'yxat elementlaridagi ortiqcha nuqta va **belgilarni tozalash
const clean = (s) => String(s ?? "").replace(/\*\*/g, "").replace(/[.;]\s*$/, "").trim();

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// --- Yozilish effekti: element ichidagi matnni harfma-harf ko'rsatadi -------
// HTML tuzilmasi (qalin so'z, havolalar) saqlanadi. Promise qaytaradi.
function typewrite(root, { cps = 140 } = {}) {
  if (reduceMotion) return Promise.resolve();
  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const full = nodes.map((n) => n.textContent);
  const total = full.reduce((a, s) => a + s.length, 0);
  if (!total) return Promise.resolve();

  // Hali yozilmagan bloklar (xatboshi, ro'yxat) va havola tugmalari ko'rinmaydi
  const hidden = new Set(root.querySelectorAll("p, ul, ol, li, .cite, br"));
  hidden.forEach((el) => (el.style.display = "none"));
  nodes.forEach((n) => (n.textContent = ""));
  const show = (node) => {
    let caretAt = null;
    for (let p = node.parentElement; p && p !== root; p = p.parentElement) {
      if (hidden.has(p)) { p.style.display = ""; hidden.delete(p); }
      if (!caretAt && /^(P|LI)$/.test(p.tagName)) caretAt = p;
    }
    return caretAt || root;
  };
  let caretEl = null;
  const moveCaret = (el) => {
    if (el === caretEl) return;
    caretEl?.classList.remove("typing-caret");
    el.classList.add("typing-caret");
    caretEl = el;
  };

  return new Promise((resolve) => {
    let shown = 0, last = performance.now();
    const step = (now) => {
      shown = Math.min(total, shown + Math.max(1, Math.round(((now - last) / 1000) * cps)));
      last = now;
      let left = shown;
      nodes.forEach((n, i) => {
        const take = Math.max(0, Math.min(full[i].length, left));
        if (take > 0 && n.textContent.length !== take) {
          n.textContent = full[i].slice(0, take);
          moveCaret(show(n));
        }
        left -= full[i].length;
      });
      if (shown < total) setTimeout(() => step(performance.now()), 16); // fon rejimida ham davom etadi
      else {
        hidden.forEach((el) => (el.style.display = ""));
        caretEl?.classList.remove("typing-caret");
        resolve();
      }
    };
    setTimeout(() => step(performance.now()), 16);
  });
}

// Elementlarni ketma-ket ohista paydo qilish
function reveal(elements, gap = 90) {
  elements.forEach((el, i) => {
    el.classList.add("reveal");
    setTimeout(() => el.classList.add("in"), reduceMotion ? 0 : 30 + i * gap);
  });
}

// --- Kutish bosqichlari -----------------------------------------------------
// Server jarayoni haqida xabar bermaydi, shuning uchun bosqichlar vaqt bo'yicha almashadi,
// oxirgisida esa javob kelguncha turadi.
function progressSteps(container, steps, { every = 2600 } = {}) {
  container.innerHTML = `
    <div class="steps">${steps.map((s) => `<div class="step" data-state="wait"><span class="step-icon"></span><span>${esc(s)}</span></div>`).join("")}</div>
    <p class="hint text-xs text-slate-500 mt-4 hidden"></p>
    <div class="mt-5 space-y-2.5"><div class="shimmer h-3 w-11/12"></div><div class="shimmer h-3 w-4/5"></div><div class="shimmer h-3 w-2/3"></div></div>`;
  const els = $$(".step", container);
  const hint = $(".hint", container);
  const started = Date.now();
  let i = 0;
  const set = (k) => els.forEach((el, j) => {
    el.dataset.state = j < k ? "done" : j === k ? "active" : "wait";
    $(".step-icon", el).innerHTML = j < k ? "✓" : j === k ? '<span class="spinner" style="width:.8rem;height:.8rem"></span>' : "";
  });
  set(0);
  const timer = setInterval(() => {
    if (i < els.length - 1) set(++i);
    const sec = Math.round((Date.now() - started) / 1000);
    if (sec >= 20) {
      hint.classList.remove("hidden");
      hint.textContent = `${sec} soniya… Tekin AI modeli band bo'lishi mumkin, zaxira modelga o'tilmoqda. Iltimos, kuting.`;
    }
  }, every);
  return { stop() { clearInterval(timer); set(els.length); } };
}

// --- Ovoz yozish -----------------------------------------------------------
// button bosilganda yozishni boshlaydi/to'xtatadi, natijani onText(text) ga beradi
function setupRecorder(button, onText) {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    button.disabled = true;
    button.title = "Brauzeringiz ovoz yozishni qo'llab-quvvatlamaydi";
    return;
  }
  let rec = null, chunks = [], timer = null, seconds = 0;
  const label = button.querySelector("[data-label]");
  const idleText = label ? label.textContent : "";

  const setState = (state) => {
    button.dataset.state = state;
    if (label) label.textContent = state === "rec" ? `To'xtatish (${seconds}s)` : state === "busy" ? "Matnga aylantirilmoqda..." : idleText;
    button.disabled = state === "busy";
  };

  button.addEventListener("click", async () => {
    if (rec && rec.state === "recording") return rec.stop();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return toast("Mikrofonga ruxsat berilmadi.", "error");
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    seconds = 0;
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      setState("busy");
      try {
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        if (blob.size < 2000) throw new Error("Yozuv juda qisqa. Qaytadan urinib ko'ring.");
        const fd = new FormData();
        fd.append("audio", blob, "ovoz.webm");
        const { text } = await (await api("/api/stt", { method: "POST", body: fd })).json();
        if (!text) throw new Error("Nutq aniqlanmadi. Balandroq gapirib ko'ring.");
        onText(text);
        toast("Ovoz matnga aylantirildi. Kerak bo'lsa, tahrirlang.", "ok");
      } catch (e) {
        toast(e.message, "error");
      } finally {
        setState("idle");
      }
    };
    rec.start();
    setState("rec");
    timer = setInterval(() => {
      seconds++;
      setState("rec");
      if (seconds >= 120) rec.stop(); // 2 daqiqa chegarasi
    }, 1000);
  });
}

// --- Fayl tanlash ----------------------------------------------------------
function setupFilePicker({ input, list, max = 5, maxMb = 10, onChange }) {
  const files = [];
  const render = () => {
    list.innerHTML = files
      .map((f, i) => {
        const isImg = f.type.startsWith("image/");
        const thumb = isImg ? `<img src="${URL.createObjectURL(f)}" class="w-full h-full object-cover" alt="">` : `<span class="text-xs font-semibold text-slate-500">PDF</span>`;
        return `<div class="relative w-20 h-20 rounded-xl border border-slate-200 bg-slate-50 overflow-hidden flex items-center justify-center" title="${esc(f.name)}">
          ${thumb}
          <button type="button" data-rm="${i}" class="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/60 text-white text-xs" aria-label="O'chirish">✕</button>
        </div>`;
      })
      .join("");
    onChange?.(files);
  };
  list.addEventListener("click", (e) => {
    const i = e.target.closest("[data-rm]")?.dataset.rm;
    if (i !== undefined) {
      files.splice(+i, 1);
      render();
    }
  });
  input.addEventListener("change", () => {
    for (const f of input.files) {
      if (files.length >= max) {
        toast(`Ko'pi bilan ${max} ta fayl.`, "error");
        break;
      }
      if (!(f.type.startsWith("image/") || f.type === "application/pdf")) {
        toast(`${f.name}: faqat rasm yoki PDF.`, "error");
        continue;
      }
      if (f.size > maxMb * 1024 * 1024) {
        toast(`${f.name}: ${maxMb} MB dan katta.`, "error");
        continue;
      }
      files.push(f);
    }
    input.value = "";
    render();
  });
  return { files, clear: () => { files.length = 0; render(); } };
}

function fmtDate(iso) {
  const d = iso instanceof Date ? iso : new Date(iso);
  if (isNaN(d)) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
}

// --- Oqimli (streaming) javoblar --------------------------------------------
// Server-Sent Events: "delta" (JSON matn bo'lagi), "done" (yakuniy natija), "error".
async function streamPost(url, body, { onDelta, onDone, onReset }) {
  const opts = { method: "POST", body };
  if (!(body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await api(url, opts);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", result = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = /^event: (\w+)/m.exec(raw)?.[1];
      const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || "{}");
      if (ev === "delta") onDelta?.(data.text);
      else if (ev === "reset") onReset?.(); // model almashdi — javob qaytadan keladi
      else if (ev === "done") { result = data.data; onDone?.(result); }
      else if (ev === "error") throw new Error(data.message || "Xatolik");
    }
  }
  if (!result) throw new Error("Javob to'liq kelmadi. Qayta urinib ko'ring.");
  return result;
}

// Hali tugamagan JSON matnidan "key" maydonining (string) joriy qiymatini ajratadi.
function partialField(json, key) {
  const m = new RegExp(`"${key}"\s*:\s*"`).exec(json);
  if (!m) return null;
  let out = "", i = m.index + m[0].length;
  while (i < json.length) {
    const ch = json[i];
    if (ch === '"') return out;
    if (ch === "\\") {
      const nx = json[i + 1];
      if (nx === undefined) break;
      if (nx === "u") {
        const hex = json.slice(i + 2, i + 6);
        if (hex.length < 4) break;
        out += String.fromCharCode(parseInt(hex, 16));
        i += 6;
        continue;
      }
      out += { n: "\n", t: "\t", r: "", '"': '"', "\\": "\\", "/": "/" }[nx] ?? nx;
      i += 2;
      continue;
    }
    out += ch;
    i++;
  }
  return out;
}

// Ekranni ko'p qayta chizmaslik uchun: bir kadrda bir marta
function rafThrottle(fn) {
  let queued = false;
  return () => {
    if (queued) return;
    queued = true;
    setTimeout(() => { queued = false; fn(); }, 40); // fon rejimida ham ishlaydi
  };
}
