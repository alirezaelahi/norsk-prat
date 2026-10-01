// Live hands-free conversation: streams mic audio to /ws/live and plays the tutor's
// audio chunks back-to-back. Turn-taking, barge-in decisions and all models run on the
// server; the browser provides echo-cancelled audio I/O and the UI.
(function () {
  const TARGET_SR = 16000;
  const SEND_SAMPLES = 512; // 32 ms packets

  const WORKLET = `
    class LiveCapture extends AudioWorkletProcessor {
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (ch) this.port.postMessage(ch.slice());
        return true;
      }
    }
    registerProcessor("live-capture", LiveCapture);`;

  // Streaming fractional resampler (input rate -> 16 kHz) with box-filter averaging.
  class Resampler {
    constructor(inRate) { this.ratio = inRate / TARGET_SR; this.pos = 0; this.acc = 0; this.n = 0; }
    push(input) {
      const out = [];
      for (let i = 0; i < input.length; i++) {
        this.acc += input[i]; this.n++;
        this.pos += 1;
        if (this.pos >= this.ratio) { out.push(this.acc / this.n); this.acc = 0; this.n = 0; this.pos -= this.ratio; }
      }
      return out;
    }
  }

  class LiveConversation {
    constructor(ui) {
      this.ui = ui; // callbacks: state, user, partial, tutorStart, tutorDone, metrics, waiting, error, level, settings, scenario, closed, session
      this.ws = null;
      this.ctx = null;
      this.muted = false;
      this.sources = []; // {id, src, start, duration}
      this.nextTime = 0;
      this.pendingMeta = new Map();
      this.chunkText = new Map();
    }

    async start(opts) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "interactive" });
      if (this.ctx.state === "suspended") await this.ctx.resume();
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await this.ctx.audioWorklet.addModule(url);
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "live-capture");
      const mute = this.ctx.createGain(); mute.gain.value = 0; // keep the graph pulling without echoing the mic
      src.connect(this.node); this.node.connect(mute); mute.connect(this.ctx.destination);
      const rs = new Resampler(this.ctx.sampleRate);
      let buf = [];
      this.node.port.onmessage = (e) => {
        const data = e.data;
        let peak = 0;
        for (let i = 0; i < data.length; i += 8) peak = Math.max(peak, Math.abs(data[i]));
        this.ui.level?.(this.muted ? 0 : peak);
        if (!this.ws || this.ws.readyState !== 1) return;
        const out = rs.push(this.muted ? new Float32Array(data.length) : data);
        for (const v of out) buf.push(v);
        while (buf.length >= SEND_SAMPLES) {
          const pcm = new Int16Array(SEND_SAMPLES);
          for (let i = 0; i < SEND_SAMPLES; i++) {
            const s = Math.max(-1, Math.min(1, buf[i]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          buf = buf.slice(SEND_SAMPLES);
          this.ws.send(pcm.buffer);
        }
      };

      const proto = location.protocol === "https:" ? "wss" : "ws";
      this.ws = new WebSocket(`${proto}://${location.host}/ws/live`);
      this.ws.binaryType = "arraybuffer";
      this.ws.onmessage = (e) => this.onMessage(e.data);
      this.ws.onclose = () => { this.ui.closed?.(); };
      this.ws.onerror = () => this.ui.error?.("Mistet forbindelsen til serveren.");
      this.startOpts = opts;
    }

    send(obj) { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj)); }
    settings(obj) { this.send({ type: "settings", ...obj }); }
    sendText(text) { this.send({ type: "text", text }); }
    setMuted(m) { this.muted = m; }

    onMessage(data) {
      if (data instanceof ArrayBuffer) {
        const id = new DataView(data).getUint32(0, true);
        const meta = this.pendingMeta.get(id);
        this.pendingMeta.delete(id);
        if (meta) this.play(id, new Int16Array(data, 4), meta);
        return;
      }
      const m = JSON.parse(data);
      switch (m.type) {
        case "ready": this.send({ type: "start", ...this.startOpts }); break;
        case "chunk": this.pendingMeta.set(m.id, m); break;
        case "stop": this.stopPlayback(true); break;
        case "state": this.ui.state?.(m.state); break;
        case "partial": this.ui.partial?.(m.text); break;
        case "user": this.ui.user?.(m.text, m.turn); break;
        case "tutor_done": this.ui.tutorDone?.(m.turn, m.text, m.interrupted); break;
        case "metrics": this.ui.metrics?.(m); break;
        case "waiting": this.ui.waiting?.(m.text); break;
        case "settings": this.ui.settings?.(m); break;
        case "scenario": this.ui.scenario?.(m.scenario); break;
        case "session": this.ui.session?.(m); break;
      }
    }

    play(id, pcm, meta) {
      const ctx = this.ctx;
      const f = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768;
      const buffer = ctx.createBuffer(1, f.length, meta.sr);
      buffer.copyToChannel(f, 0);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      const start = Math.max(ctx.currentTime + 0.01, this.nextTime);
      this.nextTime = start + buffer.duration;
      const entry = { id, src, start, duration: buffer.duration, text: meta.text, turn: meta.turn, stopped: false };
      this.sources.push(entry);
      src.start(start);
      const delay = Math.max(0, (start - ctx.currentTime) * 1000);
      entry.timer = setTimeout(() => {
        if (entry.stopped) return;
        this.send({ type: "playing", id });
        this.ui.tutorStart?.(meta.turn, meta.text);
      }, delay);
      src.onended = () => {
        this.sources = this.sources.filter((s) => s !== entry);
        if (!entry.stopped) this.send({ type: "played", id });
      };
    }

    stopPlayback(report) {
      const now = this.ctx ? this.ctx.currentTime : 0;
      let current = null;
      for (const s of this.sources) {
        if (s.start <= now && now < s.start + s.duration) current = s;
        s.stopped = true;
        clearTimeout(s.timer);
        try { s.src.stop(); } catch {}
      }
      this.sources = [];
      this.nextTime = 0;
      if (report) {
        this.send({ type: "stopped", id: current ? current.id : null,
                    played: current ? now - current.start : 0, duration: current ? current.duration : 0 });
      }
    }

    async stop() {
      this.stopPlayback(false);
      this.send({ type: "stop" });
      try { this.ws && this.ws.close(); } catch {}
      this.ws = null;
      try { this.node && this.node.disconnect(); } catch {}
      this.stream && this.stream.getTracks().forEach((t) => t.stop());
      try { this.ctx && (await this.ctx.close()); } catch {}
      this.ctx = null;
    }
  }

  window.LiveConversation = LiveConversation;
})();
