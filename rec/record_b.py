"""Clip B — escenas 4 y 5: la señal, la cadena de evidencia y la verificación.

El momento que el guion llama "la parte que importa": por cada orden citada, el monto, la
fecha y la huella del registro oficial; y un botón que vuelve al origen a comprobarlo.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
OUT = Path(__file__).parent / "clips"
OUT.mkdir(exist_ok=True)
SENAL = "Senal/720ea8991ba9b159"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(OUT),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(6000)

        # abrir la señal
        await page.evaluate("() => document.querySelector('#pregunta').focus()")
        await page.keyboard.type(SENAL, delay=26)
        await page.wait_for_timeout(700)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(7000)

        visible = await page.evaluate("() => !document.querySelector('#tarjeta').hidden")
        print("tarjeta abierta:", visible)
        await page.screenshot(path=str(OUT / "B1-tarjeta.png"))

        # recorrer la cadena despacio, como se leería
        for i in range(10):
            await page.mouse.wheel(0, 130)
            await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "B2-cadena.png"))

        # volver al origen y comprobar la huella: acá se ve que la cita no es un adorno
        pulsado = await page.evaluate("""() => {
            const b = [...document.querySelectorAll('#tarjeta-cuerpo button')]
                .find(x => (x.textContent || '').toLowerCase().includes('verificar'));
            if (!b) return null;
            b.click();
            return b.textContent.trim();
        }""")
        print("verificar ahora:", pulsado)

        for t in range(1, 8):
            await page.wait_for_timeout(2500)
            estado = await page.evaluate("""() => {
                const t = (document.querySelector('#tarjeta-cuerpo')?.innerText || '');
                return {
                    verificado: /coincide|idéntic|identic|verificad/i.test(t),
                    contra: /no coincide|difiere|distint/i.test(t),
                    cola: t.slice(-220),
                };
            }""")
            print(f"  t+{t*2.5:.0f}s · verificado={estado['verificado']} difiere={estado['contra']}")
            if estado["verificado"] or estado["contra"]:
                print("   final:", estado["cola"][-140:].replace("\n", " "))
                break

        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(OUT / "B3-verificado.png"))
        await ctx.close()

    for f in OUT.glob("page@*.webm"):
        destino = OUT / "B.webm"
        if destino.exists():
            destino.unlink()
        f.rename(destino)
        print(f"-> B.webm {destino.stat().st_size // 1024} KB")
    await_proxy = None


asyncio.run(main())
