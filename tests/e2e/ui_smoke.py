"""Browser smoke test of the full UI against a running server (real models).

Run: ./run.sh --port 8765  &  then  uv run python tests/e2e/ui_smoke.py
The OS microphone is not available to headless Chromium on macOS, so getUserMedia is
stubbed with a stream that plays TTS speech through the page's own audio graph.
"""
import asyncio, os
from playwright.async_api import async_playwright
URL=os.environ.get("PRAT_URL", "http://localhost:8765/")
OUT=os.environ.get("PRAT_SHOTS", "scratch/shots")
async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chromium", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width":1280,"height":820})
        await ctx.add_init_script("""
          // Fake microphone: a stream that plays TTS speech (OS mic permission is unavailable headless).
          navigator.mediaDevices.getUserMedia = async () => {
            const ac = new AudioContext();
            const r = await fetch('/api/tts', {method:'POST', headers:{'content-type':'application/json'},
              body: JSON.stringify({text: window.__fakeSpeech || 'Jeg vil gjerne ha en stor kaffe med melk, takk.', voice:'piper:talesyntese'})});
            const buf = await ac.decodeAudioData(await r.arrayBuffer());
            const dest = ac.createMediaStreamDestination();
            const src = ac.createBufferSource(); src.buffer = buf; src.connect(dest);
            setTimeout(() => src.start(), 300);
            return dest.stream;
          };
        """)
        page = await ctx.new_page()
        errors=[]
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error","warning") else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        await page.goto(URL)
        await page.wait_for_function("document.querySelector('#status').textContent.startsWith('Klar')", timeout=120000)
        print("status:", await page.text_content("#status"))
        await page.screenshot(path=f"{OUT}/1-home.png")
        await page.click(".scenario[data-id=kafe]"); await page.click("#levels button[data-level=A2]")
        await page.click("#startBtn")
        await page.wait_for_selector(".msg.partner:not(.typing)", timeout=60000)
        print("opener:", await page.text_content(".msg.partner .no"))
        await page.fill("#input", "Hei! Jeg vil ha en kanelbolle. Hvor mye koste det?")
        await page.click("#sendBtn")
        await page.wait_for_function("document.querySelectorAll('.msg.partner:not(.typing)').length>=2", timeout=60000)
        await page.wait_for_selector(".msg.user .feedback .fix, .msg.user .feedback .ok", timeout=60000)
        print("feedback:", await page.inner_text(".msg.user .feedback"))
        print("reply2:", await page.inner_text(".msg.partner:nth-of-type(3) .no"))
        # translation
        await page.click(".msg.partner:nth-of-type(3) [data-act=en]")
        await page.wait_for_function("(() => {const e=document.querySelector('.msg.partner:nth-of-type(3) .en'); return e && e.textContent && e.textContent!=='…'})()", timeout=30000)
        print("EN:", await page.inner_text(".msg.partner:nth-of-type(3) .en"))
        # word popover
        await page.click(".msg.partner:nth-of-type(3) .w >> nth=2")
        await page.wait_for_function("document.querySelector('.pop-gloss').textContent!=='…'", timeout=30000)
        print("gloss:", await page.inner_text(".pop-word"), "=", await page.inner_text(".pop-gloss"))
        await page.screenshot(path=f"{OUT}/2-chat-popover.png")
        await page.click("#wordPop [data-act=save]"); await page.wait_for_timeout(500)
        print("vocab count:", await page.text_content("#vocabCount"))
        await page.mouse.click(700, 120)
        # suggestions
        await page.click("#helpBtn")
        await page.wait_for_selector(".sugg", timeout=30000)
        print("suggestions:", await page.eval_on_selector_all(".sugg span", "els=>els.map(e=>e.textContent)"))
        await page.screenshot(path=f"{OUT}/3-suggestions.png")
        # speech via fake mic
        n_user = await page.locator(".msg.user").count()
        await page.evaluate("recorder.stream = null")
        await page.click("#micBtn"); await page.wait_for_timeout(4500); await page.click("#micBtn")
        await page.wait_for_function(f"document.querySelectorAll('.msg.user').length>{n_user}", timeout=60000)
        print("spoken->", await page.inner_text(f".msg.user >> nth={n_user} >> .bubble"))
        await page.wait_for_function("document.querySelectorAll('.msg.partner:not(.typing)').length>=3", timeout=60000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{OUT}/4-after-speech.png")
        # shadowing
        await page.click(".msg.partner >> nth=0 >> [data-act=shadow]")
        await page.wait_for_selector("dialog[open]")
        await page.evaluate("window.__fakeSpeech = document.querySelector('#shadowTarget').textContent; shadowRecorder.stream = null")
        await page.click("#shadowRec"); await page.wait_for_timeout(5500); await page.click("#shadowRec")
        await page.wait_for_selector(".score", timeout=60000)
        print("shadow:", (await page.inner_text("#shadowResult")).replace("\n"," | "))
        await page.screenshot(path=f"{OUT}/5-shadow.png")
        await page.keyboard.press("Escape")
        # hide-text mode + dark + mobile
        await page.click("#showText"); await page.screenshot(path=f"{OUT}/6-hidden-text.png")
        await page.click("#showText")
        await page.set_viewport_size({"width":390,"height":800}); await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT}/7-mobile.png")
        await page.click("#menuBtn"); await page.wait_for_timeout(400); await page.screenshot(path=f"{OUT}/8-mobile-menu.png")
        await page.emulate_media(color_scheme="dark"); await page.click("#menuBtn"); await page.set_viewport_size({"width":1280,"height":820}); await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT}/9-dark.png")
        print("console:", errors or "clean")
        await b.close()
asyncio.run(main())
