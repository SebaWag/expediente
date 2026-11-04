"""Clip A — escena 3: la pregunta, el router y la corrida en vivo.

Se graba sin voz y sin cursor suelto. La narración de Wagner se monta encima después, así
que lo que manda acá es el tiempo real del run y que el waterfall quede legible.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
OUT = Path(__file__).parent / "clips"
OUT.mkdir(exist_ok=True)
PREGUNTA = "¿Por qué la Municipalidad de Pumanque concentra el 82% de su gasto en un solo proveedor?"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(OUT),
            record_video_size={"width": 1920, "height": 1080},
            device_scale_factor=1,
        )
        page = await ctx.new_page()
        errores = []
        page.on("pageerror", lambda e: errores.append(str(e)))

        # 1. la portada: el grafo se acomoda
        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(7000)
        await page.mouse.move(1500, 300)

        # 2. la pregunta, tecleada a velocidad humana
        await page.evaluate("() => document.querySelector('#pregunta').focus()")
        await page.keyboard.type(PREGUNTA, delay=32)
        await page.wait_for_timeout(900)

        # 3. enviar
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3200)          # el router responde y vuela al nodo

        # 4. el chip: acá arranca la corrida de verdad
        chips = await page.evaluate("""() => [...document.querySelectorAll('#transcripcion button.chip')]
                                           .map(b => b.textContent)""")
        print("chips:", chips)
        await page.evaluate("""() => {
            const b = [...document.querySelectorAll('#transcripcion button.chip')]
                .find(x => x.textContent.includes('Investigar'));
            if (b) b.click();
        }""")
        print("clic en Investigar")

        # 5. esperar el cierre, sin cortar nada
        for t in range(1, 30):
            await page.wait_for_timeout(3000)
            estado = await page.evaluate("""() => ({
                fin: document.querySelector('#eventos')?.innerText.includes('Investigación cerrada') || false,
                n: document.querySelectorAll('#eventos li').length
            })""")
            print(f"  t+{t*3:>2}s · {estado['n']} eventos · cerrada={estado['fin']}")
            if estado["fin"]:
                break

        # 6. dejar leer el cierre
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "A-final.png"))
        print("errores:", errores[:3])
        await ctx.close()   # acá se escribe el video
        await browser.close()

    for f in sorted(OUT.glob("*.webm")):
        print("video:", f, f.stat().st_size // 1024, "KB")


asyncio.run(main())
