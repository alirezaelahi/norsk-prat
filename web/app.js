// Prat — front-end. Plain JS, no build step.
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
  $("#speed").value = state.prefs.speed || 1;
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
    .then((r) => setStatus(`Klar · ${partnerLabel(r.partner)}`, "ok"))
    .catch((e) => setStatus("Feil ved lasting: " + e.message, "err"));
}

function partnerLabel(name) {
  return { claude: "Claude", borealis: "Borealis (lokal)", scripted: "Manus (offline)" }[name] || name;
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

function setComposerEnabled(on) {
  for (const id of ["input", "sendBtn", "micBtn", "helpBtn"]) $("#" + id).disabled = !on;
}

// ------------------------------------------------------------------ sessions
async function startSession() {
  if (state.busy) return;
  $("#sidebar").classList.remove("open");
  state.busy = true;
  $("#chat").innerHTML = "";
  hideSuggestions();
  const typing = addTyping();
  try {
    const r = await api("/api/sessions", { body: { scenario: state.scenario, level: state.level } });
    state.session = { ...r.session, turns: [r.turn] };
    typing.remove();
    showGoal();
    const el = renderTurn(r.turn);
    setComposerEnabled(true);
    $("#input").focus();
    refreshHistory();
    playTurn(el, r.turn.text);
  } catch (e) {
    typing.remove();
    addNotice("Kunne ikke starte samtalen: " + e.message);
  } finally {
    state.busy = false;
  }
}

async function openSession(id) {
  const s = await api(`/api/sessions/${id}`);
  state.session = s;
  state.scenario = s.scenario; state.level = s.level; markSelected();
  $("#chat").innerHTML = "";
  hideSuggestions();
  showGoal();
  s.turns.forEach((t) => renderTurn(t, { restore: true }));
  setComposerEnabled(true);
  $("#sidebar").classList.remove("open");
}

function showGoal() {
  const sc = state.config.scenarios.find((s) => s.id === state.session.scenario);
  $("#goal").innerHTML = `<b>${esc(sc.title)}</b> · ${state.session.level} — <span class="muted">${esc(sc.goal)}</span>`;
  $("#goalBar").classList.remove("hidden");
}

async function sendText(text) {
  text = text.trim();
  if (!text || state.busy || !state.session) return;
  state.busy = true;
  $("#input").value = "";
  hideSuggestions();
  const pending = renderTurn({ role: "user", text, id: null });
  const typing = addTyping();
  try {
    const r = await api(`/api/sessions/${state.session.id}/turn`, { body: { text } });
    state.session.turns.push(r.user_turn, r.partner_turn);
    pending.dataset.id = r.user_turn.id;
    typing.remove();
    if ($("#autoFeedback").checked) loadFeedback(pending, r.user_turn.id);
    const el = renderTurn(r.partner_turn);
    playTurn(el, r.partner_turn.text);
  } catch (e) {
    typing.remove();
    addNotice("Noe gikk galt: " + e.message);
  } finally {
    state.busy = false;
    $("#input").focus();
  }
}

// ------------------------------------------------------------------ rendering
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Wrap each word in a span so it can be tapped.
function wordify(text) {
  return esc(text).replace(/(\p{L}[\p{L}\p{N}'’-]*)/gu, '<span class="w">$1</span>');
}

function renderTurn(turn, { restore = false } = {}) {
  const el = document.createElement("div");
  el.className = `msg ${turn.role}`;
  if (turn.id) el.dataset.id = turn.id;
  if (turn.role === "partner") {
    el.innerHTML = `
      <div class="bubble">
        <div class="no">${wordify(turn.text)}</div>
        <div class="reveal">Trykk for å vise teksten</div>
        <div class="en hidden"></div>
      </div>
      <div class="tools">
        <button data-act="play" title="Spill av">▶</button>
        <button data-act="slow" title="Sakte">🐢</button>
        <button data-act="en" title="Oversett">EN</button>
        <button data-act="shadow" title="Øv på uttalen">🎙 Øv</button>
      </div>`;
    el.querySelector(".reveal").addEventListener("click", () => el.classList.add("revealed"));
    el.querySelector('[data-act="play"]').addEventListener("click", () => speak(turn.text));
    el.querySelector('[data-act="slow"]').addEventListener("click", () => speak(turn.text, { slow: true }));
    el.querySelector('[data-act="en"]').addEventListener("click", () => toggleTranslation(el, el.dataset.id));
    el.querySelector('[data-act="shadow"]').addEventListener("click", () => openShadow(turn.text));
    if (restore && turn.extra?.translation) {
      const en = el.querySelector(".en");
      en.textContent = turn.extra.translation;
    }
  } else {
    el.innerHTML = `<div class="bubble">${esc(turn.text)}</div><div class="feedback"></div>`;
    if (restore && turn.extra && "feedback" in turn.extra) showFeedback(el, turn.extra.feedback);
  }
  const empty = $("#chat .empty");
  if (empty) empty.remove();
  $("#chat").appendChild(el);
  scrollDown();
  return el;
}

async function playTurn(el, text) {
  el.classList.add("speaking");
  try { await speak(text); } catch (e) { addNotice("Lyd feilet: " + e.message); }
  el.classList.remove("speaking");
}

function addTyping() {
  const el = document.createElement("div");
  el.className = "msg partner typing";
  el.innerHTML = '<div class="bubble"><span></span><span></span><span></span></div>';
  $("#chat").appendChild(el);
  scrollDown();
  return el;
}

function addNotice(text) {
  const el = document.createElement("div");
  el.className = "notice";
  el.textContent = text;
  $("#chat").appendChild(el);
  scrollDown();
}

function scrollDown() {
  const c = $("#chat");
  c.scrollTop = c.scrollHeight;
}

async function toggleTranslation(el, id) {
  const en = el.querySelector(".en");
  if (!en.classList.contains("hidden")) return en.classList.add("hidden");
  en.classList.remove("hidden");
  if (!en.textContent) {
    en.textContent = "…";
    try {
      const r = await api(`/api/turns/${id}/translation`, { method: "POST" });
      en.textContent = r.translation || "(ingen oversettelse tilgjengelig)";
    } catch (e) { en.textContent = "Feil: " + e.message; }
  }
}

// ------------------------------------------------------------------ feedback
async function loadFeedback(el, id) {
  const box = el.querySelector(".feedback");
  box.innerHTML = '<span class="muted small">sjekker…</span>';
  try {
    const r = await api(`/api/turns/${id}/feedback`, { method: "POST" });
    showFeedback(el, r.feedback);
  } catch { box.innerHTML = ""; }
}

function showFeedback(el, fb) {
  const box = el.querySelector(".feedback");
  const original = el.querySelector(".bubble").textContent;
  if (!fb) { box.innerHTML = '<span class="ok">✓ Bra!</span>'; return; }
  box.innerHTML = `
    <div class="fix">
      <div class="diff">${diffWords(original, fb.corrected)}</div>
      ${fb.explanation ? `<div class="why">${esc(fb.explanation)}</div>` : ""}
      <div class="fix-tools">
        <button data-act="play">▶</button>
        <button data-act="shadow">🎙 Øv</button>
      </div>
    </div>`;
  box.querySelector('[data-act="play"]').addEventListener("click", () => speak(fb.corrected));
  box.querySelector('[data-act="shadow"]').addEventListener("click", () => openShadow(fb.corrected));
  scrollDown();
}

// Word-level LCS diff: removed words struck through, added words highlighted.
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

// ------------------------------------------------------------------ suggestions
async function showSuggestions() {
  if (!state.session) return;
  const box = $("#suggestions");
  box.classList.remove("hidden");
  box.innerHTML = '<span class="muted small">Tenker på forslag…</span>';
  try {
    const r = await api(`/api/sessions/${state.session.id}/suggestions`, { method: "POST" });
    if (!r.suggestions.length) { box.innerHTML = '<span class="muted small">Ingen forslag tilgjengelig.</span>'; return; }
    box.innerHTML = r.suggestions
      .map((s, i) => `<div class="sugg"><button data-i="${i}" class="say" title="Lytt">🔊</button><span>${esc(s)}</span><button data-i="${i}" class="use" title="Bruk">↵</button><button data-i="${i}" class="practice" title="Øv">🎙</button></div>`)
      .join("") + '<button class="close-sugg icon-btn" title="Lukk">✕</button>';
    $$(".say", box).forEach((b) => b.addEventListener("click", () => speak(r.suggestions[b.dataset.i])));
    $$(".use", box).forEach((b) => b.addEventListener("click", () => { $("#input").value = r.suggestions[b.dataset.i]; $("#input").focus(); }));
    $$(".practice", box).forEach((b) => b.addEventListener("click", () => openShadow(r.suggestions[b.dataset.i])));
    $(".close-sugg", box).addEventListener("click", hideSuggestions);
  } catch (e) { box.innerHTML = `<span class="muted small">Feil: ${esc(e.message)}</span>`; }
}

function hideSuggestions() {
  $("#suggestions").classList.add("hidden");
  $("#suggestions").innerHTML = "";
}

// ------------------------------------------------------------------ microphone
let recorder;

function setupMic() {
  recorder = new Recorder((lvl) => ($("#micLevel").style.transform = `scale(${1 + Math.min(lvl * 3, 1.2)})`));
  const btn = $("#micBtn");
  btn.addEventListener("click", () => (recorder.recording ? stopMic() : startMic()));

  // Hold space to talk (when not typing in the input).
  let spaceDown = false;
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.repeat || spaceDown || btn.disabled) return;
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(document.activeElement.tagName) || $("#shadow").open) return;
    e.preventDefault();
    spaceDown = true;
    startMic();
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && spaceDown) { spaceDown = false; e.preventDefault(); stopMic(); }
  });
}

async function startMic() {
  if (currentAudio) currentAudio.pause();
  try {
    await recorder.start();
    $("#micBtn").classList.add("recording");
    $("#input").placeholder = "Lytter… (trykk igjen eller slipp mellomrom for å stoppe)";
  } catch (e) {
    addNotice("Fikk ikke tilgang til mikrofonen: " + e.message);
  }
}

async function stopMic() {
  const rec = await recorder.stop();
  $("#micBtn").classList.remove("recording");
  $("#input").placeholder = "Skriv på norsk…";
  if (!rec || rec.seconds < 0.4) return;
  $("#input").placeholder = "Transkriberer…";
  const form = new FormData();
  form.append("audio", rec.blob, "speech.wav");
  try {
    const r = await api("/api/stt", { form });
    $("#input").placeholder = "Skriv på norsk…";
    if (!r.text) { addNotice("Jeg hørte ikke noe. Prøv igjen, litt nærmere mikrofonen."); return; }
    if ($("#autoSend").checked) sendText(r.text);
    else { $("#input").value = r.text; $("#input").focus(); }
  } catch (e) {
    $("#input").placeholder = "Skriv på norsk…";
    addNotice("Talegjenkjenning feilet: " + e.message);
  }
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

// ------------------------------------------------------------------ live conversation
const ORB_LABEL = {
  idle: "Trykk for å starte",
  connecting: "Kobler til…",
  listening: "Jeg lytter…",
  user: "Du snakker",
  thinking: "Tenker…",
  speaking: "Snakker",
};

function setupLive() {
  $$(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  setMode(state.prefs.mode || "live");
  $("#orb").addEventListener("click", () => (state.live ? stopLive() : startLive()));
  $("#endBtn").addEventListener("click", stopLive);
  $("#muteBtn").addEventListener("click", () => {
    if (!state.live) return;
    const m = !state.live.muted;
    state.live.setMuted(m);
    $("#muteBtn").textContent = m ? "🎙 Slå på mikrofon" : "🔇 Demp";
    $("#orb").classList.toggle("muted", m);
  });
  $("#liveTextForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("#liveText").value.trim();
    if (t && state.live) { state.live.sendText(t); $("#liveText").value = ""; }
  });
  $("#liveSummaryBtn").addEventListener("click", () => state.liveSessionId && showSummaryFor(state.liveSessionId));
  if (state.prefs.hintClosed) $("#hint").classList.add("hidden");
  $("#hintClose").addEventListener("click", () => { $("#hint").classList.add("hidden"); state.prefs.hintClosed = true; savePrefs(); });
  const endMs = $("#endMs");
  endMs.value = state.prefs.endMs || 800;
  const showEnd = () => ($("#endMsVal").textContent = `${endMs.value} ms`);
  showEnd();
  endMs.addEventListener("input", () => {
    showEnd(); state.prefs.endMs = +endMs.value; savePrefs();
    state.live?.settings({ end_ms: +endMs.value });
  });
  // Live-adjust speed and voice during a conversation.
  $("#speed").addEventListener("change", () => state.live?.settings({ speed: +$("#speed").value }));
  $("#voice").addEventListener("change", () => state.live?.settings({ voice: $("#voice").value }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.live) stopLive(); });
}

function setMode(mode) {
  if (mode !== "live" && state.live) stopLive();
  state.mode = mode; state.prefs.mode = mode; savePrefs();
  $$(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("#liveView").classList.toggle("hidden", mode !== "live");
  $("#practiceView").classList.toggle("hidden", mode === "live");
  document.body.classList.toggle("mode-live", mode === "live");
  $("#startBtn").textContent = mode === "live" ? "Start samtale 🎧" : "Start øving";
}

function setOrb(st) {
  const orb = $("#orb");
  orb.className = `orb ${st}${state.live?.muted ? " muted" : ""}`;
  $("#orbLabel").textContent = ORB_LABEL[st] || st;
}

async function startLive() {
  if (state.live) await stopLive();
  $("#sidebar").classList.remove("open");
  $("#transcript").innerHTML = "";
  $("#liveCaption").textContent = "";
  setOrb("connecting");
  const sc = state.config.scenarios.find((s) => s.id === state.scenario);
  $("#liveGoalText").innerHTML = `<b>${esc(sc.title)}</b> · ${state.level} — <span class="muted">${esc(sc.goal)}</span>`;
  $("#liveGoal").classList.remove("hidden");
  const tutorLines = new Map(); // turn -> element
  const conv = new LiveConversation({
    state: (st) => { setOrb(st); if (st !== "user") $("#liveCaption").textContent = ""; },
    level: (lvl) => ($("#orbRing").style.transform = `scale(${1 + Math.min(lvl * 2.5, 0.6)})`),
    partial: (text) => ($("#liveCaption").textContent = text),
    waiting: () => ($("#liveCaption").textContent = "Jeg venter — fortsett bare…"),
    user: (text) => { $("#liveCaption").textContent = ""; addLine("user", text); },
    tutorStart: (turn, text) => {
      let el = tutorLines.get(turn);
      if (!el) { el = addLine("tutor", ""); tutorLines.set(turn, el); }
      const t = el.querySelector(".t");
      t.innerHTML = wordify((t.textContent + " " + text).trim());
      scrollTranscript();
    },
    tutorDone: (turn, text, interrupted) => {
      let el = tutorLines.get(turn);
      if (!el && text) { el = addLine("tutor", ""); tutorLines.set(turn, el); }
      if (el && interrupted) {
        el.querySelector(".t").innerHTML = wordify(text || "");
        el.classList.add("interrupted");
      }
    },
    metrics: showLatency,
    settings: (m) => { if (m.speed) { $("#speed").value = m.speed; $("#speed").dispatchEvent(new Event("input")); } },
    scenario: (id) => {
      state.scenario = id; markSelected();
      const s2 = state.config.scenarios.find((s) => s.id === id);
      $("#liveGoalText").innerHTML = `<b>${esc(s2.title)}</b> · ${state.level} — <span class="muted">${esc(s2.goal)}</span>`;
    },
    session: (m) => { state.liveSessionId = m.id; refreshHistory(); },
    error: (msg) => addNotice2(msg),
    closed: () => { if (state.live === conv) { state.live = null; liveControls(false); setOrb("idle"); } },
  });
  state.live = conv;
  try {
    await conv.start({
      scenario: state.scenario, level: state.level, voice: $("#voice").value,
      speed: +$("#speed").value, end_ms: +$("#endMs").value,
    });
    liveControls(true);
  } catch (e) {
    state.live = null;
    setOrb("idle");
    addNotice2("Fikk ikke tilgang til mikrofonen: " + e.message);
  }
}

async function stopLive() {
  const conv = state.live;
  state.live = null;
  liveControls(false);
  setOrb("idle");
  if (conv) await conv.stop();
  refreshHistory();
}

function liveControls(on) {
  $("#muteBtn").disabled = $("#endBtn").disabled = $("#liveText").disabled = !on;
  $("#muteBtn").textContent = "🔇 Demp";
}

function addLine(who, text) {
  const el = document.createElement("div");
  el.className = `line ${who}`;
  el.innerHTML = `<span class="who">${who === "user" ? "Du" : "Tutor"}</span><span class="t">${who === "user" ? esc(text) : wordify(text)}</span>`;
  $("#transcript").appendChild(el);
  scrollTranscript();
  return el;
}

function addNotice2(text) {
  const el = document.createElement("div");
  el.className = "notice";
  el.textContent = text;
  $("#transcript").appendChild(el);
  scrollTranscript();
}

function scrollTranscript() {
  const t = $("#transcript");
  t.scrollTop = t.scrollHeight;
}

const LAT_ROWS = [
  ["speech_end_to_audio_ms", "Fra du slutter → lyd", 1000],
  ["gap_after_window_ms", "Etter ventetiden", 700],
  ["stt_ms", "Talegjenkjenning", 300],
  ["llm_first_token_ms", "Språkmodell, første ord", 300],
  ["llm_first_sentence_ms", "Første setning", 600],
  ["tts_first_ms", "Tale (TTS)", 150],
];
const latHistory = [];

function showLatency(m) {
  if (m.speech_end_to_audio_ms != null) latHistory.push(m.speech_end_to_audio_ms);
  const avg = latHistory.length ? Math.round(latHistory.reduce((a, b) => a + b, 0) / latHistory.length) : null;
  $("#latBody").innerHTML =
    LAT_ROWS.filter(([k]) => m[k] != null)
      .map(([k, label, budget]) => `<div class="lat-row ${m[k] <= budget ? "good" : m[k] <= budget * 1.5 ? "ok" : "bad"}"><span>${label}</span><b>${m[k]} ms</b></div>`)
      .join("") +
    (avg != null ? `<div class="lat-row avg"><span>Snitt (${latHistory.length} turer)</span><b>${avg} ms</b></div>` : "") +
    (m.speculative ? '<div class="muted small">⚡ forberedt under pausen</div>' : "");
}

init().catch((e) => setStatus("Kunne ikke koble til serveren: " + e.message, "err"));
