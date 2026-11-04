import asyncio, json
from playwright.async_api import async_playwright

URL = "https://investiga.wagnersolutionsai.com/"


async def main() -> None:
    async with async_playwright() as pw:
        b = await pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        page = await b.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="load", timeout=60000)
        await page.wait_for_timeout(4500)

        # abrir el registro de la última corrida directamente
        runs = await page.evaluate("""async () => {
            const r = await fetch('/api/runs?limit=3');
            return await r.json();
        }""")
        print("runs recientes:", json.dumps(runs, ensure_ascii=False)[:400])

        await page.evaluate("""async () => {
            const r = await fetch('/api/runs?limit=1');
            const d = await r.json();
            const id = (d.runs || [])[0]?.id;
            document.querySelector('#btn-registro').click();
            if (window.state) { window.state.runId = id; }
            console.log('run', id);
        }""")
        await page.wait_for_timeout(3000)

        texto = await page.evaluate("() => document.querySelector('#eventos')?.innerText || ''")
        print("\n=== TEXTO DEL REGISTRO ===")
        print(texto[:1500])
        print("\n¿menciona el modelo?:", "muse-spark" in texto.lower())
        print("¿tokens?:", "tokens" in texto.lower())
        await b.close()


asyncio.run(main())
