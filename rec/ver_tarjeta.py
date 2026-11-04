import asyncio
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
SENAL = "Senal/720ea8991ba9b159"


async def main() -> None:
    async with async_playwright() as pw:
        b = await pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        page = await b.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(5000)

        await page.evaluate("() => document.querySelector('#pregunta').focus()")
        await page.keyboard.type(SENAL, delay=12)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(6000)

        info = await page.evaluate("""() => {
            const t = document.querySelector('#tarjeta');
            const c = document.querySelector('#tarjeta-cuerpo');
            return {
                tarjetaVisible: t ? !t.hidden : null,
                texto: (c?.innerText || '').slice(0, 1800),
                botones: [...document.querySelectorAll('#tarjeta-cuerpo button, #tarjeta-cuerpo a')]
                          .map(x => (x.textContent || '').trim()).filter(Boolean).slice(0, 20),
            };
        }""")
        print("tarjeta visible:", info["tarjetaVisible"])
        print("\n=== TEXTO DE LA TARJETA ===")
        print(info["texto"])
        print("\n=== CONTROLES ===")
        for x in info["botones"]:
            print("  -", x[:70])
        await b.close()


asyncio.run(main())
