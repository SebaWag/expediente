"""QA a escala real: sirve el overview de producción (957 nodos) desde un archivo local.

El portal local no alcanza MillenniumDB, así que se sustituye la respuesta por la real
capturada hoy. Lo que se valida es el dibujo: que NO falte ningún nodo, que la cámara
arranque en el núcleo y que "encuadrar todo" funcione.
"""
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path("/home/seba/expediente")))
import expediente.portal.server as pm                        # noqa: E402
from expediente.portal.server import build_server           # noqa: E402
from expediente.runs import RunStore                        # noqa: E402
from expediente.agent import Settings                       # noqa: E402

import importlib.util
spec = importlib.util.spec_from_file_location("qa", Path(__file__).parent / "_qa_portal_real.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

OV = Path("/tmp/ov_live.json")
OUT = Path("/tmp/qa_escala")
ERR = []


def main():
    OUT.mkdir(exist_ok=True)
    real = json.loads(OV.read_text())
    pm.Portal.overview = lambda self: real

    srv = build_server(qa.make_store(), RunStore(OUT / "runs"), Settings(max_steps=2),
                       "127.0.0.1", 0, provider_factory=lambda: None, api=qa.FakeApi())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        pg.on("pageerror", lambda e: ERR.append(f"pageerror: {e}"))
        pg.on("console", lambda m: ERR.append(f"console.error: {m.text}") if m.type == "error" else None)

        pg.goto(url)
        pg.evaluate("() => { window.__t0 = performance.now(); }")
        pg.wait_for_function("() => window.expedienteGrafo && window.expedienteGrafo.cy.nodes().length > 900",
                             timeout=60000)
        pg.wait_for_timeout(1500)

        info = pg.evaluate("""() => {
          const cy = window.expedienteGrafo.cy;
          const drawn = cy.nodes().filter(n => n.style('display') !== 'none').length;
          return {
            enLienzo: cy.nodes().length,
            dibujados: drawn,
            aristas: cy.edges().length,
            zoomInicial: Number(cy.zoom().toFixed(2)),
            pie: (document.querySelector('.leyenda-pie-texto') || {}).textContent || '',
            tiempoMs: Math.round(performance.now() - window.__t0)
          };
        }""")
        print("arranque:", json.dumps(info, ensure_ascii=False))
        pg.screenshot(path=str(OUT / "1_nucleo.png"))

        todo = pg.evaluate("""() => {
          const cy = window.expedienteGrafo.cy;
          window.expedienteGrafo.showAll(0);
          return {zoom: Number(cy.zoom().toFixed(3))};
        }""")
        pg.wait_for_timeout(700)
        print("encuadrar todo:", json.dumps(todo))
        pg.screenshot(path=str(OUT / "2_todo.png"))

        print("errors:", json.dumps(ERR[:5], ensure_ascii=False))
        b.close()
    srv.shutdown()
    return 1 if ERR else 0


if __name__ == "__main__":
    sys.exit(main())
