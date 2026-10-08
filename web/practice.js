// Practice mode ("Øving"): push-to-talk / typed chat with corrections, translation and suggestions.

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
  if (state.mode !== "practice") setMode("practice"); // past conversations are shown as chat
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

