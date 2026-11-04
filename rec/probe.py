import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--hide-scrollbars", "--force-device-scale-factor=1"]
        )
        page = await browser.new_page(viewport={"width": 1920, "height": 1080},
                                      device_scale_factor=1)
        errores = []
        page.on("console", lambda m: errores.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))

        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(7000)

        info = await page.evaluate("""() => {
            const q = (s) => document.querySelector(s);
            const box = (e) => e ? [e.offsetWidth, e.offsetHeight] : null;
            const canvases = [...document.querySelectorAll('canvas')].map(c => ({
                w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight,
                parent: c.parentElement?.id || c.parentElement?.className
            }));
            return {
                cifras: {
                    ordenes: q('#n-ordenes')?.textContent,
                    proveedores: q('#n-proveedores')?.textContent,
                    organismos: q('#n-organismos')?.textContent,
                    documentos: q('#n-documentos')?.textContent,
                },
                letra: q('#letra-chica')?.textContent,
                mapa: box(q('#mapa')),
                mapa3d: box(q('#mapa-3d')),
                mapa3dHidden: q('#mapa-3d')?.hasAttribute('hidden'),
                canvases,
                sugerencias: [...document.querySelectorAll('#sugerencias .chip')].map(b => b.textContent),
                titulo: q('h1')?.textContent,
            };
        }""")
        for k, v in info.items():
            print(f"{k}: {v}")

        await page.screenshot(path=str(OUT / "01-portada.png"), full_page=False)
        print("captura:", OUT / "01-portada.png")
        print("ERRORES:", len(errores))
        for e in errores[:8]:
            print("  ", e[:150])
        await browser.close()


asyncio.run(main())
