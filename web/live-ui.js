// Live mode ("Samtale"): orb, live transcript, latency panel. Audio/WebSocket client is live-audio.js.

// ------------------------------------------------------------------ live conversation
const ORB_LABEL = {
  idle: "Trykk for å starte",
  connecting: "Laster modellene…",
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
    error: (msg) => addLiveNotice(msg),
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
    addLiveNotice("Fikk ikke tilgang til mikrofonen: " + e.message);
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

function addLiveNotice(text) {
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

