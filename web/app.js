// Prat — shared UI: settings, API helpers, playback, word popover, shadowing, summary, lists.
// Loaded first; practice.js and live-ui.js add the two modes. No build step.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  config: null,
  scenario: "kafe",
  level: "A2",
  session: null, // {id, scenario, level, turns: []}
  busy: false,
  prefs: loadPrefs(),
  mode: "live",
  live: null, // LiveConversation while running
};

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("prat.prefs")) || {}; } catch { return {}; }
}
function savePrefs() {
  try { localStorage.setItem("prat.prefs", JSON.stringify(state.prefs)); } catch {}
}

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body || opts.form ? "POST" : "GET"), headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return opts.raw ? r : r.json();
}

// ------------------------------------------------------------------ audio playback
const audioCache = new Map();
let currentAudio = null;

async function speak(text, { slow = false } = {}) {
  const voice = $("#voice").value;
  const speed = +$("#speed").value * (slow ? 0.7 : 1);
  const key = `${voice}|${speed.toFixed(2)}|${text}`;
  let url = audioCache.get(key);
  if (!url) {
    const r = await api("/api/tts", { body: { text, voice, speed }, raw: true });
    url = URL.createObjectURL(await r.blob());
    audioCache.set(key, url);
  }
  if (currentAudio) currentAudio.pause();
  const audio = (currentAudio = new Audio(url));
  // Resolve when playback ends, is interrupted, or fails (e.g. autoplay blocked).
  const done = new Promise((res) => {
    audio.onended = audio.onpause = audio.onerror = res;
  });
  try { await audio.play(); } catch { return; }
  return done;
}

// ------------------------------------------------------------------ setup
async function init() {
  state.config = await api("/api/config");
  const c = state.config;

  // scenarios
  $("#scenarios").innerHTML = c.scenarios
    .map((s) => `<button class="scenario" data-id="${s.id}" title="${esc(s.goal)}"><b>${esc(s.title)}</b><span>${esc(s.title_en)}</span></button>`)
    .join("");
  state.scenario = state.prefs.scenario || "kafe";
  state.level = state.prefs.level || "A2";
  $$(".scenario").forEach((b) =>
    b.addEventListener("click", () => { state.scenario = b.dataset.id; state.prefs.scenario = b.dataset.id; savePrefs(); markSelected(); })
  );
  $("#levels").innerHTML = c.levels.map((l) => `<button data-level="${l}">${l}</button>`).join("");
  $$("#levels button").forEach((b) =>
    b.addEventListener("click", () => { state.level = b.dataset.level; state.prefs.level = b.dataset.level; savePrefs(); markSelected(); })
  );
  markSelected();

  // voices
  $("#voice").innerHTML = c.voices.map((v) => `<option value="${v.id}" title="${esc(v.note)}">${esc(v.label)}</option>`).join("");
  $("#voice").value = state.prefs.voice || c.default_voice;
  $("#voice").addEventListener("change", () => { state.prefs.voice = $("#voice").value; savePrefs(); speak("Hei! Sånn høres jeg ut."); });
  $("#speed").value = state.prefs.speed || 0.9;
  const showSpeed = () => ($("#speedVal").textContent = (+$("#speed").value).toFixed(2).replace(/0$/, "") + "×");
  showSpeed();
  $("#speed").addEventListener("input", () => { showSpeed(); state.prefs.speed = +$("#speed").value; savePrefs(); });

  for (const id of ["showText", "autoSend", "autoFeedback"]) {
    if (state.prefs[id] !== undefined) $("#" + id).checked = state.prefs[id];
    $("#" + id).addEventListener("change", () => {
      state.prefs[id] = $("#" + id).checked; savePrefs();
      if (id === "showText") document.body.classList.toggle("hide-text", !$("#showText").checked);
    });
  }
  document.body.classList.toggle("hide-text", !$("#showText").checked);

  $("#startBtn").addEventListener("click", () => (state.mode === "live" ? startLive() : startSession()));
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendText($("#input").value); });
  $("#helpBtn").addEventListener("click", showSuggestions);
  $("#summaryBtn").addEventListener("click", showSummary);
  $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  setupMic();
  setupWordPopover();
  setupShadow();
  setupLive();
  refreshHistory();
  refreshVocab();

  // Load models in the background so the first turn is quick.
  api("/api/warmup", { method: "POST" })
    .then((r) => {
      setStatus(`Klar · ${partnerLabel(r.partner)}`, "ok");
      $("#orb").disabled = false;
      if (!state.live) setOrb("idle");
    })
    .catch((e) => setStatus("Feil ved lasting: " + e.message, "err"));
}

function partnerLabel(name) {
  return { borealis: "Borealis (lokal)" }[name] || name;
}

function setStatus(text, kind = "") {
  const el = $("#status");
  el.textContent = text;
  el.className = "status " + kind;
}

function markSelected() {
  $$(".scenario").forEach((b) => b.classList.toggle("selected", b.dataset.id === state.scenario));
  $$("#levels button").forEach((b) => b.classList.toggle("selected", b.dataset.level === state.level));
}

function switchTab(tab) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  $("#history").classList.toggle("hidden", tab !== "history");
  $("#vocab").classList.toggle("hidden", tab !== "vocab");
}


// ------------------------------------------------------------------ rendering
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Wrap each word in a span so it can be tapped.
function wordify(text) {
  return esc(text).replace(/(\p{L}[\p{L}\p{N}'’-]*)/gu, '<span class="w">$1</span>');
}


function diffWords(a, b) {
  const norm = (w) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const A = a.split(/\s+/).filter(Boolean), B = b.split(/\s+/).filter(Boolean);
  const dp = Array.from({ length: A.length + 1 }, () => new Array(B.length + 1).fill(0));
  for (let i = A.length - 1; i >= 0; i--)
    for (let j = B.length - 1; j >= 0; j--)
      dp[i][j] = norm(A[i]) === norm(B[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (norm(A[i]) === norm(B[j])) { out.push(esc(B[j])); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(`<del>${esc(A[i++])}</del>`);
    else out.push(`<ins>${esc(B[j++])}</ins>`);
  }
  while (i < A.length) out.push(`<del>${esc(A[i++])}</del>`);
  while (j < B.length) out.push(`<ins>${esc(B[j++])}</ins>`);
  return out.join(" ");
}

// ------------------------------------------------------------------ summary
async function showSummary() {
  if (!state.session) return;
  await showSummaryFor(state.session.id);
}

async function showSummaryFor(sessionId) {
  const body = $("#summaryBody");
  body.innerHTML = '<p class="muted">Går gjennom samtalen…</p>';
  $("#summary").showModal();
  try {
    const r = await api(`/api/sessions/${sessionId}/summary`, { method: "POST" });
    if (!r.turns) { body.innerHTML = '<p class="muted">Du har ikke sagt noe ennå. Sett i gang! 🙂</p>'; return; }
    const stats = `<div class="stats">
        <div><b>${r.turns}</b><span>replikker</span></div>
        <div><b>${r.words}</b><span>ord</span></div>
        <div><b>${Math.round((100 * r.correct) / r.turns)}%</b><span>uten feil</span></div>
      </div>`;
    const list = r.corrections.length
      ? `<h3>Rettelser</h3><ul class="corrections">${r.corrections.map((c, i) => `
          <li>
            <div class="diff">${diffWords(c.text, c.feedback.corrected)}</div>
            ${c.feedback.explanation ? `<div class="why">${esc(c.feedback.explanation)}</div>` : ""}
            <div class="fix-tools"><button type="button" data-i="${i}" data-act="play">▶</button><button type="button" data-i="${i}" data-act="shadow">🎙 Øv</button></div>
          </li>`).join("")}</ul>`
      : '<p class="ok">Ingen rettelser — kjempebra! 🎉</p>';
    body.innerHTML = stats + list;
    $$("[data-act=play]", body).forEach((b) => b.addEventListener("click", () => speak(r.corrections[b.dataset.i].feedback.corrected)));
    $$("[data-act=shadow]", body).forEach((b) => b.addEventListener("click", () => { $("#summary").close(); openShadow(r.corrections[b.dataset.i].feedback.corrected); }));
  } catch (e) { body.innerHTML = `<p class="muted">Feil: ${esc(e.message)}</p>`; }
}


// ------------------------------------------------------------------ word popover
function setupWordPopover() {
  const pop = $("#wordPop");
  let current = null;
  document.addEventListener("click", async (e) => {
    const w = e.target.closest(".msg.partner .w, .line.tutor .w");
    if (!w) {
      if (!e.target.closest("#wordPop")) pop.classList.add("hidden");
      return;
    }
    const msg = w.closest(".msg, .line");
    if (document.body.classList.contains("hide-text") && !msg.classList.contains("revealed")) return;
    const sentence = msg.querySelector(".no, .t").textContent;
    current = { word: w.textContent, sentence };
    $(".pop-word", pop).textContent = current.word;
    $(".pop-gloss", pop).textContent = "…";
    const r = w.getBoundingClientRect();
    pop.style.left = Math.min(window.innerWidth - 240, Math.max(8, r.left)) + "px";
    pop.style.top = Math.min(window.innerHeight - 120, r.bottom + 6) + "px";
    pop.classList.remove("hidden");
    speak(current.word);
    const req = current;
    try {
      const g = await api("/api/gloss", { body: { word: req.word, sentence: req.sentence } });
      if (current === req) { $(".pop-gloss", pop).textContent = g.gloss || "—"; current.gloss = g.gloss; }
    } catch { $(".pop-gloss", pop).textContent = "—"; }
  });
  pop.addEventListener("click", async (e) => {
    const act = e.target.dataset.act;
    if (!act || !current) return;
    if (act === "play") speak(current.word);
    if (act === "slow") speak(current.word, { slow: true });
    if (act === "save") {
      await api("/api/vocab", { body: { word: current.word.toLowerCase(), meaning: current.gloss || "", example: current.sentence } });
      e.target.textContent = "✓ Lagret";
      setTimeout(() => (e.target.textContent = "＋ Ordbok"), 1200);
      refreshVocab();
    }
  });
}

// ------------------------------------------------------------------ shadowing
let shadowText = "";
let shadowRecorder;

function setupShadow() {
  shadowRecorder = new Recorder();
  $("#shadowPlay").addEventListener("click", () => speak(shadowText));
  $("#shadowSlow").addEventListener("click", () => speak(shadowText, { slow: true }));
  $("#shadowRec").addEventListener("click", async () => {
    const btn = $("#shadowRec");
    if (!shadowRecorder.recording) {
      if (currentAudio) currentAudio.pause();
      try { await shadowRecorder.start(); } catch (e) { $("#shadowResult").textContent = "Mikrofon: " + e.message; return; }
      btn.classList.add("recording");
      btn.innerHTML = '<span class="mic-icon">⏹</span> Stopp';
      return;
    }
    const rec = await shadowRecorder.stop();
    btn.classList.remove("recording");
    btn.innerHTML = '<span class="mic-icon">🎙</span> Ta opp';
    $("#shadowResult").innerHTML = '<span class="muted">Sjekker…</span>';
    const form = new FormData();
    form.append("audio", rec.blob, "shadow.wav");
    form.append("target", shadowText);
    try {
      const r = await api("/api/shadow", { form });
      const words = r.words.map((w) => `<span class="sw ${w.status}">${esc(w.word)}</span>`).join(" ");
      const grade = r.score >= 90 ? "Supert! 🎉" : r.score >= 70 ? "Bra! 👍" : "Prøv igjen 💪";
      $("#shadowResult").innerHTML = `
        <div class="score"><b>${r.score}</b>/100 · ${grade}</div>
        <div class="sw-line">${words}</div>
        <div class="muted small">Hørte: «${esc(r.heard || "…")}»</div>`;
    } catch (e) { $("#shadowResult").textContent = "Feil: " + e.message; }
  });
}

function openShadow(text) {
  shadowText = text;
  $("#shadowTarget").textContent = text;
  $("#shadowResult").innerHTML = "";
  $("#shadow").showModal();
  speak(text);
}

// ------------------------------------------------------------------ history & vocab lists
async function refreshHistory() {
  const items = await api("/api/sessions");
  const titles = Object.fromEntries(state.config.scenarios.map((s) => [s.id, s.title]));
  $("#history").innerHTML = items.length
    ? items.map((s) => `
        <li data-id="${s.id}">
          <div><b>${esc(titles[s.scenario] || s.scenario)}</b> · ${s.level}
            <div class="muted small">${new Date(s.created * 1000).toLocaleString("no")} · ${s.n_turns} ${s.n_turns === 1 ? "replikk" : "replikker"}</div>
            ${s.first_user ? `<div class="small trunc">«${esc(s.first_user)}»</div>` : ""}
          </div>
          <button class="del icon-btn" title="Slett">🗑</button>
        </li>`).join("")
    : '<li class="muted small">Ingen samtaler ennå.</li>';
  $$("#history li[data-id]").forEach((li) => {
    li.addEventListener("click", (e) => { if (!e.target.closest(".del")) openSession(li.dataset.id); });
    $(".del", li).addEventListener("click", async () => { await api(`/api/sessions/${li.dataset.id}`, { method: "DELETE" }); refreshHistory(); });
  });
}

async function refreshVocab() {
  const items = await api("/api/vocab");
  $("#vocabCount").textContent = items.length || "";
  $("#vocab").innerHTML = items.length
    ? items.map((v) => `
        <li data-id="${v.id}">
          <div><b>${esc(v.word)}</b> <span class="muted">— ${esc(v.meaning || "?")}</span>
            ${v.example ? `<div class="small trunc">${esc(v.example)}</div>` : ""}</div>
          <span class="actions"><button class="say icon-btn" title="Lytt">🔊</button><button class="del icon-btn" title="Slett">🗑</button></span>
        </li>`).join("")
    : '<li class="muted small">Trykk på et ord i samtalen for å lagre det her.</li>';
  $$("#vocab li[data-id]").forEach((li, i) => {
    $(".say", li).addEventListener("click", () => speak(items[i].word));
    $(".del", li).addEventListener("click", async () => { await api(`/api/vocab/${li.dataset.id}`, { method: "DELETE" }); refreshVocab(); });
  });
}


document.addEventListener("DOMContentLoaded", () => {
  init().catch((e) => setStatus("Kunne ikke koble til serveren: " + e.message, "err"));
});
