"""Browser test of live mode: getUserMedia is replaced by a stream that plays scripted
learner utterances (TTS) with pauses, so the full browser audio path is exercised."""

import asyncio
import json
import os

from playwright.async_api import async_playwright

OUT = os.environ.get("PRAT_SHOTS", "scratch/shots")
SCRIPT = """
(() => {
  const lines = window.__lines;
  navigator.mediaDevices.getUserMedia = async () => {
    const ac = new AudioContext();
    const dest = ac.createMediaStreamDestination();
    (async () => {
      await new Promise(r => setTimeout(r, 9000)); // let the tutor open
      for (const [text, waitAfter] of lines) {
        const r = await fetch('/api/tts', {method:'POST', headers:{'content-type':'application/json'},
          body: JSON.stringify({text, voice:'piper:nvcc:KNN', speed: 1.0})});
        const buf = await ac.decodeAudioData(await r.arrayBuffer());
        const src = ac.createBufferSource(); src.buffer = buf; src.connect(dest); src.start();
        window.__said = (window.__said || []).concat([text]);
        await new Promise(r => setTimeout(r, buf.duration * 1000 + waitAfter));
      }
    })();
    return dest.stream;
  };
})();
"""


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chromium", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 1280, "height": 860})
        lines = [
            ["Hei! Jeg heter Ali, og jeg bor i Oslo.", 7000],
            ["Jeg liker å gå på tur i marka.", 7000],
            ["Kan du snakke saktere?", 7000],
        ]
        await ctx.add_init_script(f"window.__lines = {json.dumps(lines)};" + SCRIPT)
        page = await ctx.new_page()
        errs = []
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        await page.goto("http://localhost:8765/")
        await page.wait_for_function("document.querySelector('#status').textContent.startsWith('Klar')", timeout=180000)
        await page.screenshot(path=f"{OUT}/L1-idle.png")
        await page.click("#orb")
        await page.wait_for_selector(".line.tutor", timeout=60000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=f"{OUT}/L2-opening.png")
        await page.wait_for_function("document.querySelectorAll('.line.user').length >= 3", timeout=90000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{OUT}/L3-convo.png")
        lines_txt = await page.eval_on_selector_all(".line", "els => els.map(e => e.innerText.replace(/\\n/g,' : '))")
        print("\n".join(lines_txt))
        print("LATENCY:", (await page.inner_text("#latBody")).replace("\n", " | "))
        print("speed now:", await page.input_value("#speed"))
        await page.click("#endBtn")
        await page.wait_for_timeout(500)
        await page.click("#liveSummaryBtn")
        await page.wait_for_selector("#summaryBody .stats", timeout=60000)
        await page.screenshot(path=f"{OUT}/L4-summary.png")
        print("summary:", (await page.inner_text("#summaryBody")).replace("\n", " | ")[:300])
        print("console:", errs or "clean")
        await b.close()


asyncio.run(main())
