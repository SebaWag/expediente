"""Levanta el portal real contra un grafo en memoria y lo captura con Playwright.

No es el banco de pruebas: es la página de verdad, con app.js, los contadores, el chat y la
tarjeta. Si algo se rompió al cambiar el motor del lienzo, se rompe acá.
"""
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path("/home/seba/expediente")))
sys.path.insert(0, str(Path("/home/seba/expediente/tests")))

from expediente.graph.store import InMemoryGraphStore          # noqa: E402
import expediente.portal.server as portal_mod                 # noqa: E402
from expediente.portal.server import build_server             # noqa: E402
from expediente.runs import RunStore                          # noqa: E402
from expediente.agent import Settings                         # noqa: E402

OUT = Path("/tmp/qa_portal")
ERR = []


def make_store():
    store = InMemoryGraphStore()
    store.upsert_node("Organismo", "69.260.000-1", nombre="Municipalidad de Renca")
    store.upsert_node("Organismo", "69.260.000-2", nombre="Hospital Puerto Montt")
    store.upsert_node("Proveedor", "76.264.138-0", razon_social="CARDIOTEC VASCULAR LIMITADA")
    store.upsert_node("Proveedor", "77.290.194-1", razon_social="Cardiotec Vascularis SpA")
    store.upsert_node("Proveedor", "96.555.222-4", razon_social="BAXTER CHILE")
    store.upsert_node("Domicilio", "geo-1", direccion="VALLE DE CASABLANCA NORTE 8219")
    store.upsert_node("Domicilio", "geo-2", direccion="AV. LOS CARRERA 1234")
    store.upsert_node("Persona", "rep-1", nombre="REPRESENTANTE LEGAL COMUN")
    store.upsert_node("Senal", "sig-1", claim="73 órdenes bajo el umbral")
    for index in range(8):
        store.upsert_node("OrdenCompra", f"OC-{index}", monto_clp=833_000, fecha="2021-11-02")
        store.upsert_edge(("Organismo", "69.260.000-2"), "EMITE", ("OrdenCompra", f"OC-{index}"))
        store.upsert_edge(("OrdenCompra", f"OC-{index}"), "ADJUDICADA_A", ("Proveedor", "96.555.222-4"))
    for index in range(3):
        store.upsert_node("OrdenCompra", f"RC-{index}", monto_clp=900_000, fecha="2026-08-01")
        store.upsert_edge(("Organismo", "69.260.000-1"), "EMITE", ("OrdenCompra", f"RC-{index}"))
        store.upsert_edge(("OrdenCompra", f"RC-{index}"), "ADJUDICADA_A", ("Proveedor", "76.264.138-0"))
    store.upsert_edge(("Proveedor", "76.264.138-0"), "TIENE_DOMICILIO", ("Domicilio", "geo-1"))
    store.upsert_edge(("Proveedor", "77.290.194-1"), "TIENE_DOMICILIO", ("Domicilio", "geo-1"))
    store.upsert_edge(("Proveedor", "96.555.222-4"), "TIENE_DOMICILIO", ("Domicilio", "geo-2"))
    store.upsert_edge(("Proveedor", "96.555.222-4"), "REPRESENTADA_POR", ("Persona", "rep-1"))
    store.upsert_edge(("Senal", "sig-1"), "OBSERVA", ("Proveedor", "96.555.222-4"))
    return store


class FakeApi:
    def signals(self, params):
        return {"count": 1, "signals": [{
            "id": "sig-1", "detector": "fraccionamiento", "score": 900, "severity": "alta",
            "organismo": "Hospital Puerto Montt", "proveedor": "BAXTER CHILE",
            "monto_clp": 6_664_000, "ordenes": 8, "periodo": "2021-11", "clase": "por_caso",
        }]}

    def stats(self, params):
        return {"nodes": {"OrdenCompra": 11, "Proveedor": 3, "Organismo": 2, "Documento": 0},
                "relations": {}}


def synthetic_overview():
    """El landing real pide el motor; acá se sustituye por la misma forma de respuesta.

    Lo que se valida es el render y el cableado con app.js, no la consulta SPARQL (que en
    producción ya está probada y no se tocó).
    """
    nodes, edges = [], []

    def add(i, label, name):
        nodes.append({"id": i, "label": label, "name": name, "key": i.split("/", 1)[-1]})

    def link(a, rel, b):
        edges.append({"source": a, "target": b, "rel": rel})

    add("Detector/fraccionamiento", "Detector", "Fraccionamiento de trato directo")
    add("Detector/concentracion", "Detector", "Concentración de proveedores")
    add("Senal/sig-93efd38a", "Senal", "73 órdenes bajo el umbral · CLP 54.582.378")
    add("Senal/sig-aa11", "Senal", "HHI 0,71 en insumos peritoneales")
    add("Organismo/69.260.000-2", "Organismo", "HOSPITAL PUERTO MONTT")
    add("Organismo/69.260.000-1", "Organismo", "MUNICIPALIDAD DE RENCA")
    add("Organismo/CENABAST", "Organismo", "CENABAST")
    add("Proveedor/96.555.222-4", "Proveedor", "BAXTER CHILE")
    add("Proveedor/76.264.138-0", "Proveedor", "CARDIOTEC VASCULAR LIMITADA")
    add("Proveedor/77.290.194-1", "Proveedor", "CARDIOTEC VASCULARIS SPA")
    add("Proveedor/79.111.333-9", "Proveedor", "TECNOMEDICAL DEL SUR")
    add("Domicilio/157d5c86", "Domicilio", "VALLE DE CASABLANCA NORTE 8219")
    add("Domicilio/geo-2", "Domicilio", "AV. LOS CARRERA 1234")
    add("Persona/rep-1", "Persona", "REPRESENTANTE LEGAL COMUN")
    add("Documento/doc-1", "Documento", "Resolución de adjudicación 2021-11")
    add("Documento/doc-2", "Documento", "Orden de compra firmada")
    add("Licitacion/lic-1", "Licitacion", "LE 1234-56-LR21")
    add("CompraAgil/ca-1", "CompraAgil", "CA 9988-221")

    for index in range(14):
        oc = f"OrdenCompra/OC-{index}"
        add(oc, "OrdenCompra", f"OC 41{1000 + index}")
        link("Organismo/69.260.000-2", "EMITE", oc)
        link(oc, "ADJUDICADA_A", "Proveedor/96.555.222-4")
        if index % 4 == 0:
            link("Senal/sig-93efd38a", "OBSERVA", oc)
        if index == 0:
            link(oc, "ACREDITA", "Documento/doc-1")
    for index in range(4):
        oc = f"OrdenCompra/RC-{index}"
        add(oc, "OrdenCompra", f"OC 43{1000 + index}")
        link("Organismo/69.260.000-1", "EMITE", oc)
        link(oc, "ADJUDICADA_A", "Proveedor/76.264.138-0")

    link("Detector/fraccionamiento", "GENERA", "Senal/sig-93efd38a")
    link("Detector/concentracion", "GENERA", "Senal/sig-aa11")
    link("Senal/sig-aa11", "OBSERVA", "Proveedor/96.555.222-4")
    link("Senal/sig-aa11", "OBSERVA", "Proveedor/79.111.333-9")
    link("Proveedor/96.555.222-4", "TIENE_DOMICILIO", "Domicilio/geo-2")
    link("Proveedor/96.555.222-4", "REPRESENTADA_POR", "Persona/rep-1")
    link("Proveedor/79.111.333-9", "REPRESENTADA_POR", "Persona/rep-1")
    link("Proveedor/76.264.138-0", "TIENE_DOMICILIO", "Domicilio/157d5c86")
    link("Proveedor/77.290.194-1", "TIENE_DOMICILIO", "Domicilio/157d5c86")
    link("Documento/doc-1", "EN", "Licitacion/lic-1")
    link("Organismo/CENABAST", "EMITE", "CompraAgil/ca-1")
    link("CompraAgil/ca-1", "ADJUDICADA_A", "Proveedor/79.111.333-9")

    return {"nodes": nodes, "edges": edges, "truncated": True, "total": 64_792_053}


def main():
    OUT.mkdir(exist_ok=True)
    store = make_store()
    portal_mod.Portal.overview = lambda self: synthetic_overview()
    runs = RunStore(OUT / "runs")
    server = build_server(store, runs, Settings(max_steps=4), "127.0.0.1", 0,
                          provider_factory=lambda: None, api=FakeApi())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print("url:", url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                device_scale_factor=2)
        page.on("console", lambda m: ERR.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: ERR.append(f"pageerror: {e}"))
        page.goto(url)
        page.wait_for_timeout(3200)
        page.screenshot(path=str(OUT / "1_inicio.png"))

        info = page.evaluate("""() => {
          const cy = window.expedienteGrafo && window.expedienteGrafo.cy;
          return {
            cy: !!cy,
            nodos: cy ? cy.nodes().length : 0,
            aristas: cy ? cy.edges().length : 0,
            leyenda: !!document.querySelector('.leyenda-grafo'),
            filasLeyenda: document.querySelectorAll('.leyenda-fila').length,
            contadores: {
              ordenes: document.getElementById('n-ordenes').textContent,
              proveedores: document.getElementById('n-proveedores').textContent
            }
          };
        }""")
        print("info:", json.dumps(info, ensure_ascii=False))

        # clic en un nodo -> debe abrir la tarjeta lateral
        pos = page.evaluate("""() => {
          const cy = window.expedienteGrafo.cy;
          const n = cy.nodes('[kind = "Proveedor"]').first();
          if (!n.length) return null;
          const p = n.renderedPosition();
          return {x: p.x, y: p.y, id: n.id()};
        }""")
        if pos:
            page.mouse.click(pos["x"], pos["y"])
            page.wait_for_timeout(1200)
            print("tarjeta visible:", page.evaluate(
                "() => { const t = document.getElementById('tarjeta');"
                " return t ? !t.hidden && t.textContent.slice(0,80) : null; }"))
            page.screenshot(path=str(OUT / "2_tarjeta.png"))

        # capas: apagar un tipo desde la leyenda y comprobar que se oculta de verdad
        hidden = page.evaluate("""() => {
          const btn = [...document.querySelectorAll('.leyenda-fila')]
            .find(b => b.textContent.includes('orden de compra'));
          if (!btn) return 'sin fila';
          btn.click();
          const cy = window.expedienteGrafo.cy;
          return cy.nodes('[kind = "OrdenCompra"]').filter(n => n.hasClass('oculto')).length;
        }""")
        print("ordenes ocultas tras clic en leyenda:", hidden)
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "3_capas.png"))

        print("errors:", json.dumps(ERR, ensure_ascii=False, indent=1))
        browser.close()

    server.shutdown()
    return 1 if ERR else 0


if __name__ == "__main__":
    sys.exit(main())
