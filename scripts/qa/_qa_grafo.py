"""QA visual del grafo: abre el banco de pruebas, interactúa y saca capturas.

No es un test de unidad: es el mismo control que la vez pasada cambió el veredicto de
"hairball" a "producto". Si algo no dibuja, esto lo dice con una captura, no con una
opinión.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
QA = HERE / "_qa_grafo.html"
OUT = Path("/tmp/qa_grafo")

errors = []


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                device_scale_factor=2)
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(QA.as_uri())
        page.wait_for_timeout(2600)  # layout fcose + transición de opacidad

        stats = page.evaluate("window.__stats")
        print("stats:", json.dumps(stats, ensure_ascii=False))

        page.screenshot(path=str(OUT / "1_inicial.png"))

        # hover sobre un hub: la lente tiene que apagar el resto
        box = page.locator("#mapa").bounding_box()
        page.mouse.move(box["width"] * 0.5, box["height"] * 0.5)
        page.wait_for_timeout(200)
        pos = page.evaluate("""
          (() => {
            const cy = window.grafo.cy;
            const n = cy.nodes('[kind = "Organismo"]').first();
            if (!n.length) return null;
            const p = n.renderedPosition();
            return {x: p.x, y: p.y, id: n.id()};
          })()
        """)
        print("hover node:", pos)
        if pos:
            page.mouse.move(pos["x"], pos["y"])
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "2_hover.png"))
            # click -> panel / callback
            page.mouse.click(pos["x"], pos["y"])
            page.wait_for_timeout(400)
            print("onSelect:", page.evaluate("window.__sel"))
            page.screenshot(path=str(OUT / "3_click.png"))

        # ruta: los dos Cardiotec unidos por el domicilio compartido
        page.evaluate("""
          window.grafo.showPath([
            "Proveedor/76.264.138-0","Domicilio/157d5c86c514b86c","Proveedor/77.290.194-1"
          ]);
          window.grafo.frameNodes([
            "Proveedor/76.264.138-0","Domicilio/157d5c86c514b86c","Proveedor/77.290.194-1"
          ], 1.6);
        """)
        page.wait_for_timeout(700)
        page.screenshot(path=str(OUT / "4_ruta.png"))

        # capas: apagar las órdenes de compra
        page.evaluate("window.grafo.setLabelVisible('OrdenCompra', false)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "5_sin_ordenes.png"))
        page.evaluate("window.grafo.setLabelVisible('OrdenCompra', true)")

        print("errors:", json.dumps(errors, ensure_ascii=False, indent=1))
        browser.close()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
