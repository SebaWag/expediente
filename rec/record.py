"""Los tres clips visuales del pitch, sin voz, para montar la narración encima.

A · escena 3  la pregunta, el router y la corrida en vivo (waterfall con el modelo visible)
B · escenas 4-5  la señal, la cadena de evidencia, la orden y el documento
C · escena 6  la escala: la portada con las 565 señales

Se graba a 1920x1080, 1:1 con el video final. Nada de cursor suelto ni notificaciones.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
OUT = Path(__file__).parent / "clips"
OUT.mkdir(exist_ok=True)

PREGUNTA = "¿Por qué la Municipalidad de Pumanque concentra el 82% de su gasto en un solo proveedor?"
PROVEEDOR = "JOSE LUIS PAVEZ VARGAS"
SENAL = "Senal/720ea8991ba9b159"

ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars"]


async def abrir(browser, nombre):
    ctx = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(OUT),
        record_video_size={"width": 1920, "height": 1080},
        device_scale_factor=1,
    )
    page = await ctx.new_page()
    page._nombre = nombre
    return ctx, page


async def escribir(page, texto, delay=32):
    await page.evaluate("() => document.querySelector('#pregunta').focus()")
    await page.keyboard.type(texto, delay=delay)
    await page.wait_for_timeout(800)
    await page.keyboard.press("Enter")


async def cerrar(ctx, page, nombre):
    await ctx.close()
    for f in OUT.glob("page@*.webm"):
        destino = OUT / f"{nombre}.webm"
        if destino.exists():
            destino.unlink()
        f.rename(destino)
        print(f"  -> {destino.name}  {destino.stat().st_size // 1024} KB")
        return


async def clip_a(browser):
    print("=== A · escena 3: la corrida en vivo ===")
    ctx, page = await abrir(browser, "A")
    await page.goto(URL, wait_until="load", timeout=60000)
    await page.wait_for_timeout(7000)
    await page.mouse.move(1520, 320)

    await escribir(page, PREGUNTA)
    await page.wait_for_timeout(3200)          # el router responde y vuela al nodo

    await page.evaluate("""() => {
        const b = [...document.querySelectorAll('#transcripcion button.chip')]
            .find(x => x.textContent.includes('Investigar'));
        if (b) b.click();
    }""")

    for t in range(1, 34):
        await page.wait_for_timeout(3000)
        estado = await page.evaluate("""() => {
            const txt = (document.querySelector('#eventos')?.innerText || '').toUpperCase();
            return {fin: txt.includes('CERRADA'), n: document.querySelectorAll('#eventos li').length};
        }""")
        print(f"  t+{t*3:>2}s · {estado['n']} eventos · cerrada={estado['fin']}")
        if estado["fin"]:
            break

    await page.wait_for_timeout(6000)          # dejar leer el cierre
    await page.screenshot(path=str(OUT / "A-final.png"))
    await cerrar(ctx, page, "A")


async def clip_b(browser):
    print("=== B · escenas 4-5: la cadena de evidencia ===")
    ctx, page = await abrir(browser, "B")
    await page.goto(URL, wait_until="load", timeout=60000)
    await page.wait_for_timeout(6000)

    # Del proveedor a la señal: el camino que haría un auditor, no un id tecleado.
    await escribir(page, PROVEEDOR)
    await page.wait_for_timeout(4500)

    tiene_senales = await page.evaluate("""() => {
        const t = document.querySelector('#tarjeta-cuerpo')?.innerText || '';
        return t.includes('Señales que la mencionan');
    }""")
    print("  la tarjeta del proveedor trae señales:", tiene_senales)

    if tiene_senales:
        await page.evaluate("""() => {
            const b = [...document.querySelectorAll('#tarjeta-cuerpo button.fila-item')]
                .find(x => (x.innerText || '').toLowerCase().includes('concentra'));
            if (b) b.click();
        }""")
    else:
        await escribir(page, SENAL)

    await page.wait_for_timeout(5000)
    await page.screenshot(path=str(OUT / "B-tarjeta.png"))

    # La cadena: bajar por la tarjeta para que se lea orden por orden
    for _ in range(6):
        await page.mouse.wheel(0, 180)
        await page.wait_for_timeout(700)

    await page.screenshot(path=str(OUT / "B-cadena.png"))

    # Abrir el documento de una orden citada
    abierto = await page.evaluate("""() => {
        const b = [...document.querySelectorAll('#tarjeta-cuerpo a, #tarjeta-cuerpo button')]
            .find(x => /documento|ver registro|abrir/i.test(x.textContent || ''));
        if (b) { b.click(); return b.textContent.trim().slice(0, 60); }
        return null;
    }""")
    print("  abrir documento:", abierto)
    await page.wait_for_timeout(6000)
    await page.screenshot(path=str(OUT / "B-documento.png"))
    await cerrar(ctx, page, "B")


async def clip_c(browser):
    print("=== C · escena 6: la escala ===")
    ctx, page = await abrir(browser, "C")
    await page.goto(URL, wait_until="load", timeout=60000)
    await page.wait_for_timeout(8000)

    # girar el grafo despacio, con el mouse, para que se vea que es un grafo y no un fondo
    await page.mouse.move(960, 540)
    for paso in range(1, 4):
        await page.mouse.down()
        await page.mouse.move(960 + paso * 70, 540 + paso * 12, steps=24)
        await page.mouse.up()
        await page.wait_for_timeout(1400)

    await page.wait_for_timeout(2000)
    # acercar
    await page.evaluate("() => document.querySelector('[data-mapa=\"in\"]')?.click()")
    await page.wait_for_timeout(2500)
    await page.screenshot(path=str(OUT / "C-escala.png"))
    await cerrar(ctx, page, "C")


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=ARGS)
        await clip_a(browser)
        await clip_b(browser)
        await clip_c(browser)
        await browser.close()
    print("\n=== CLIPS ===")
    for f in sorted(OUT.glob("*.webm")):
        print(f"  {f.name}  {f.stat().st_size // 1024} KB")


asyncio.run(main())
