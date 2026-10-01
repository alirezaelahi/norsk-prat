// Microphone capture -> 16 kHz mono 16-bit WAV, done in the browser so the server
// needs no ffmpeg. Exposes window.Recorder.
(function () {
  const TARGET_SR = 16000;

  function downsample(buf, inRate) {
    if (inRate === TARGET_SR) return buf;
    const ratio = inRate / TARGET_SR;
    const out = new Float32Array(Math.floor(buf.length / ratio));
    // Average the input samples that fall in each output slot (cheap low-pass).
    for (let i = 0; i < out.length; i++) {
      const start = Math.floor(i * ratio), end = Math.min(buf.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = start; j < end; j++) sum += buf[j];
      out[i] = sum / Math.max(1, end - start);
    }
    return out;
  }

  function encodeWav(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buffer);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  // Audio-thread processor: batches 128-sample render quanta into ~2k-sample chunks.
  const WORKLET = `
    class PratCapture extends AudioWorkletProcessor {
      constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (ch) {
          for (let i = 0; i < ch.length; i++) {
            this.buf[this.n++] = ch[i];
            if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice()); this.n = 0; }
          }
        }
        return true;
      }
    }
    registerProcessor("prat-capture", PratCapture);`;
  let _workletUrl;
  const workletUrl = () => (_workletUrl ||= URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));

  class Recorder {
    constructor(onLevel) {
      this.onLevel = onLevel || (() => {});
      this.recording = false;
    }

    async start() {
      if (this.recording) return;
      if (!this.stream) {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      }
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (this.ctx.state === "suspended") await this.ctx.resume().catch(() => {});
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.chunks = [];
      const onData = (data) => {
        this.chunks.push(data);
        let peak = 0;
        for (let i = 0; i < data.length; i += 16) peak = Math.max(peak, Math.abs(data[i]));
        this.onLevel(peak);
      };
      if (this.ctx.audioWorklet) {
        await this.ctx.audioWorklet.addModule(workletUrl());
        this.proc = new AudioWorkletNode(this.ctx, "prat-capture");
        this.proc.port.onmessage = (e) => onData(e.data);
      } else {
        // Fallback for old browsers: deprecated ScriptProcessor.
        this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
        this.proc.onaudioprocess = (e) => onData(new Float32Array(e.inputBuffer.getChannelData(0)));
      }
      src.connect(this.proc);
      this.proc.connect(this.ctx.destination);
      this.recording = true;
    }

    async stop() {
      if (!this.recording) return null;
      this.recording = false;
      this.proc.disconnect();
      const rate = this.ctx.sampleRate;
      await this.ctx.close();
      this.onLevel(0);
      const len = this.chunks.reduce((n, c) => n + c.length, 0);
      const all = new Float32Array(len);
      let off = 0;
      for (const c of this.chunks) { all.set(c, off); off += c.length; }
      return { blob: encodeWav(downsample(all, rate), TARGET_SR), seconds: len / rate };
    }
  }

  window.Recorder = Recorder;
})();
