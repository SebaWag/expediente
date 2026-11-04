import asyncio
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(5000)

        diag = await page.evaluate("""() => {
            const el = document.querySelector('#pregunta');
            const r = el.getBoundingClientRect();
            const cx = r.left + r.width/2, cy = r.top + r.height/2;
            const top = document.elementFromPoint(cx, cy);
            const cs = getComputedStyle(el);
            const v = document.querySelector('.vineta');
            const vcs = v ? getComputedStyle(v) : null;
            return {
                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                elementoArriba: top ? (top.tagName + '#' + (top.id||'') + '.' + (top.className||'')) : null,
                esElMismo: top === el,
                display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
                pointerEvents: cs.pointerEvents,
                disabled: el.disabled, readOnly: el.readOnly,
                vineta: vcs ? {pointerEvents: vcs.pointerEvents, zIndex: vcs.zIndex, inset: vcs.inset} : null,
                chatZ: getComputedStyle(document.querySelector('.chat')).zIndex,
            };
        }""")
        for k, v in diag.items():
            print(f"{k}: {v}")
        await browser.close()


asyncio.run(main())
