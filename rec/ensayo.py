"""Ensayo del camino que se va a grabar, manejando la UI como una persona.

Si esto no arranca la corrida al hacer clic, no hay escena 3.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

PREGUNTA = "¿Por qué la Municipalidad de Pumanque concentra el 82% de su gasto en un solo proveedor?"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars"]
        )
        page = await browser.new_page(viewport={"width": 1920, "height": 1080},
                                      device_scale_factor=1)
        errores = []
        page.on("console", lambda m: errores.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))

        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(5000)

        # 1. escribir la pregunta, como se escribiría en cámara
        # foco por JS + teclado real: el canvas del grafo repinta sin parar y la comprobación
        # de "estabilidad" de Playwright nunca se satisface, aunque el textarea esté libre.
        await page.evaluate("() => document.querySelector('#pregunta').focus()")
        await page.keyboard.type(PREGUNTA, delay=18)
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "10-pregunta-escrita.png"))

        # 2. enviar
        await page.evaluate("() => document.querySelector('#btn-enviar').click()")
        await page.wait_for_timeout(2600)
        await page.screenshot(path=str(OUT / "11-router-responde.png"))

        # 3. el chip tiene que estar
        chips = await page.locator("#transcripcion button.chip").all_inner_texts()
        print("chips en la transcripción:", chips)

        chip = page.locator("#transcripcion button.chip", has_text="Investigar")
        if await chip.count() == 0:
            print("❌ NO apareció el chip de investigar")
            await browser.close()
            return
        await page.evaluate("""() => {
            const b = [...document.querySelectorAll('#transcripcion button.chip')]
                .find(x => x.textContent.includes('Investigar'));
            b.click();
        }""")
        print("clic en «Investigar esto con el agente»")

        # 4. ¿arrancó la corrida? el registro se abre y se llenan eventos
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(OUT / "12-registro-abierto.png"))

        inicial = await page.evaluate("() => document.querySelectorAll('#eventos li').length")
        print("eventos al inicio:", inicial)

        # 5. esperar a que termine
        for segundo in range(1, 13):
            await page.wait_for_timeout(4000)
            n = await page.evaluate("() => document.querySelectorAll('#eventos li').length")
            fin = await page.evaluate("() => document.body.innerText.includes('Investigación cerrada')")
            print(f"  t+{segundo*4:>2}s · eventos: {n}")
            if fin and n > 0:
                print("  ✅ la corrida terminó")
                break

        await page.screenshot(path=str(OUT / "13-corrida-terminada.png"))

        resumen = await page.evaluate("""() => ({
            eventos: document.querySelectorAll('#eventos li').length,
            registroVisible: !document.querySelector('#registro').hidden,
            textoRegistro: (document.querySelector('#eventos')?.innerText || '').slice(0, 700),
            modelos: [...document.querySelectorAll('#eventos *')]
                       .map(e => e.textContent)
                       .filter(t => t && t.includes('muse-spark')).slice(0, 6),
        })""")
        print("\n--- REGISTRO ---")
        print("visible:", resumen["registroVisible"], "| eventos:", resumen["eventos"])
        print(resumen["textoRegistro"])
        print("llamadas al modelo visibles:", resumen["modelos"])
        print("\nERRORES DE CONSOLA:", len(errores))
        for e in errores[:6]:
            print("  ", e[:140])
        await browser.close()


asyncio.run(main())
