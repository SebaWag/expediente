"""The verbs an agent is allowed to use, and the reason there are so few.

The requirement is "navigate the graph in natural language". The way that goes
wrong is well known: give a model a query language and it writes a query that
returns something plausible. So the model here does not write queries. It **picks a
verb**, with typed arguments, and deterministic code turns that verb into a
traversal. Three consequences, all of them the point:

* every step is a named operation a human can read back (*"miró los vecinos de
  este proveedor"*, not ``SELECT ?p ?o WHERE …``);
* the tool cannot escalate: there is no verb that writes to the graph;
* the schema is fixed and ordered, so the cached prompt prefix stays hot across
  turns — the README's second design rule.

The verbs mirror the three in the README — **navigate, relate, document** — plus the
two that were added when the agent was allowed to leave the graph: search and read
the open web. ``afirmar`` is the important one and the reason this file exists:
it is the only way the model can state a conclusion, it returns *data*, and
whatever it cannot support is rejected by :func:`verify_finding` rather than
softened.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..mcp.server import (
    citation_trail,
    find_paths,
    neighbours,
    network,
    search,
)
from ..research import FetchError, SourceRegistry, fetch, search as web_search
from ..research.sources import verify_claim

#: Fixed order. It is hashed into the cached prompt prefix, so reordering this list
#: silently invalidates every cache hit — see docs/ARCHITECTURE.md.
TOOL_ORDER: tuple[str, ...] = (
    "buscar_en_grafo",
    "vecinos",
    "caminos",
    "red_proveedor",
    "citas",
    "senal_detalle",
    "senales_de",
    "perfil_de_gasto",
    "documento_de_orden",
    "buscar_en_web",
    "leer_fuente",
    "afirmar",
    "terminar",
)

MAX_WEB_FETCHES = 4
MAX_CLAIMS = 12


# --------------------------------------------------------------------- schemas
def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


SCHEMAS: list[dict[str, Any]] = [
    _fn(
        "buscar_en_grafo",
        "Encuentra un nodo del grafo por nombre. Usa label='Proveedor' para empresas, "
        "label='Organismo' para municipalidades y servicios públicos. Devuelve el id del nodo, "
        "que necesitas para las demás herramientas.",
        {
            "label": {"type": "string", "enum": ["Proveedor", "Organismo"]},
            "texto": {"type": "string", "description": "Nombre o RUT. Ej: 'Cardiotec', '76.264.138-0', 'Municipalidad de Renca'"},
        },
        ["label", "texto"],
    ),
    _fn(
        "vecinos",
        "Devuelve los nodos conectados a uno dado, y con qué relación. Es el paso de navegación "
        "básico: úsalo para ver qué compró un organismo, a quién se adjudicó una orden, o qué "
        "cuelga de un proveedor.",
        {
            "nodo": {"type": "string", "description": "id del nodo, como 'Proveedor/76.264.138-0'"},
            "direccion": {"type": "string", "enum": ["out", "in", "both"]},
            "limite": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        ["nodo"],
    ),
    _fn(
        "caminos",
        "Busca por qué camino se conectan dos nodos. Sirve para responder '¿qué relación hay "
        "entre estos dos?' con las aristas que efectivamente existen.",
        {
            "desde": {"type": "string", "description": "id del nodo de origen"},
            "hasta": {"type": "string", "description": "id del nodo de destino"},
            "saltos": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        ["desde", "hasta"],
    ),
    _fn(
        "red_proveedor",
        "Dada una empresa, devuelve su red: domicilios compartidos y otras empresas en ese "
        "domicilio. Ojo: un domicilio compartido también es lo que parece un edificio de "
        "oficinas. Es una pista para revisar, no una relación afirmada.",
        {"rut": {"type": "string", "description": "RUT en cualquier formato"}},
        ["rut"],
    ),
    _fn(
        "citas",
        "Devuelve la cadena de evidencia de un nodo: afirmación → evidencia → documento, con "
        "número de página. Úsalo antes de afirmar algo sobre una señal.",
        {"nodo": {"type": "string", "description": "id del nodo, ej. 'Senal/<id>' o 'OrdenCompra/<codigo>'"},
         "limite": {"type": "integer", "minimum": 1, "maximum": 25}},
        ["nodo"],
    ),
    _fn(
        "senal_detalle",
        "Abre una señal completa: la afirmación, las órdenes con su monto, fecha y descripción, "
        "y el documento oficial de cada orden con su huella digital. Es la herramienta principal "
        "para validar o refutar. Devuelve 'completo' = si todas las órdenes tienen documento.",
        {"senal_id": {"type": "string", "description": "id de la señal, sin el prefijo 'Senal/'"},
         "limite": {"type": "integer", "minimum": 1, "maximum": 120}},
        ["senal_id"],
    ),
    _fn(
        "senales_de",
        "Busca las señales que nombran a una empresa o a un organismo. El vínculo es por nombre "
        "exacto, no por RUT: la respuesta lo dice, porque un nombre es una coincidencia más débil "
        "que un identificador.",
        {"label": {"type": "string", "enum": ["Proveedor", "Organismo"]},
         "clave": {"type": "string", "description": "RUT del proveedor u código del organismo"}},
        ["label", "clave"],
    ),
    _fn(
        "perfil_de_gasto",
        "Cuánto gasta un organismo (o cuánto recibe un proveedor): total, número de órdenes, "
        "reparto por procedimiento (licitación, trato directo, convenio marco) y con quién. "
        "Los conteos son del grafo completo.",
        {"label": {"type": "string", "enum": ["Proveedor", "Organismo"]},
         "clave": {"type": "string", "description": "RUT del proveedor u código del organismo"}},
        ["label", "clave"],
    ),
    _fn(
        "documento_de_orden",
        "El documento detrás de una orden de compra: dirección oficial, huella sha256, cuándo se "
        "descargó y el extracto del registro. Si no tiene documento, lo dice.",
        {"codigo": {"type": "string", "description": "código de la orden, ej. '1417966-39-SE25'"}},
        ["codigo"],
    ),
    _fn(
        "buscar_en_web",
        "Busca en internet fuentes externas (prensa, Contraloría, municipios, diario oficial). "
        "Devuelve candidatos con URL. Un candidato NO es una fuente: hay que leerlo con "
        "leer_fuente para poder citarlo.",
        {"consulta": {"type": "string"}, "limite": {"type": "integer", "minimum": 1, "maximum": 8}},
        ["consulta"],
    ),
    _fn(
        "leer_fuente",
        "Descarga una URL y la registra como fuente citable. Devuelve un id de fuente y un "
        "extracto del texto. Solo lo que descargues aquí puede citarse después.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _fn(
        "afirmar",
        "Registra UNA conclusión. Es la única forma de que quede en el expediente. Toda "
        "afirmación sobre el grafo debe traer los identificadores de los nodos que la "
        "sostienen (nodos como 'Proveedor/…', o bien ordenes, senales); toda afirmación sobre la web debe traer source_id y una "
        "cita textual copiada palabra por palabra del documento. Si no puedes citar, no afirmes.",
        {
            "texto": {"type": "string", "description": "La conclusión, en una frase, en español llano"},
            "nodos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nodos del grafo que sostienen la afirmación, como 'Proveedor/76.264.138-0' o "
                "'Domicilio/<geo_hash>'. Deben existir: se verifican uno por uno.",
            },
            "ordenes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Códigos de orden de compra del grafo que sostienen la afirmación",
            },
            "senales": {"type": "array", "items": {"type": "string"}, "description": "ids de :Senal en el grafo"},
            "documentos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "huellas sha256 de :Documento (las devuelve senal_detalle o documento_de_orden). "
                "Es la forma más fuerte de sostener una afirmación: apunta al archivo descargado.",
            },
            "source_id": {"type": "string", "description": "id devuelto por leer_fuente, si la afirmación es sobre la web"},
            "cita": {"type": "string", "description": "frase copiada textualmente del documento descargado"},
        },
        ["texto"],
    ),
    _fn(
        "terminar",
        "Cierra la investigación. Escribe qué se pudo sostener y qué no, y por qué.",
        {
            "resumen": {"type": "string", "description": "2 o 3 frases para una persona que no conoce el sistema"},
            "vacios": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Preguntas que quedaron sin responder y por qué",
            },
        },
        ["resumen"],
    ),
]

TOOL_NAMES = tuple(schema["function"]["name"] for schema in SCHEMAS)
assert TOOL_NAMES == TOOL_ORDER, "the schema order is part of the cache prefix"


# ---------------------------------------------------------------------- context
@dataclass
class ToolContext:
    """Everything a verb needs, and the budget it is not allowed to exceed."""

    store: Any
    registry: SourceRegistry
    run: Any | None = None
    #: Injectable seams. They live on the context and not on the module because the
    #: portal runs several investigations concurrently: a module-level override is a
    #: data race waiting for a demo to expose it.
    fetcher: Any = None
    searcher: Any = None
    web_fetches: int = 0
    claims: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    finished: dict[str, Any] | None = None
    seen_urls: set[str] = field(default_factory=set)
    #: Observaciones legibles, para que la UI muestre qué pasó en cada paso.
    observations: list[str] = field(default_factory=list)

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.run is not None:
            self.run.events.append(event_type, payload)


def _split_node(value: str) -> tuple[str, str]:
    """``"Proveedor/76.264.138-0"`` -> ``("Proveedor", "76.264.138-0")``."""
    raw = (value or "").strip()
    if "/" not in raw:
        raise ValueError(f"nodo debe ser 'Label/id', recibí {value!r}")
    label, key = raw.split("/", 1)
    if not label or not key:
        raise ValueError(f"nodo incompleto: {value!r}")
    return label, key


# ----------------------------------------------------------------------- verbs
def _buscar_en_grafo(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    result = search(ctx.store, {"label": args["label"], "text": args["texto"], "limit": 10})
    nodes = []
    for node in result.get("nodes", []) if isinstance(result, dict) else []:
        nodes.append(node)
    if isinstance(result, dict) and "results" in result:
        nodes = result["results"]
    ctx.observations.append(f"busqué «{args['texto']}» en {args['label']}: {len(nodes)} resultado(s)")
    return {"nodos": nodes, "label": args["label"], "texto": args["texto"]}


def _vecinos(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    label, key = _split_node(args["nodo"])
    result = neighbours(
        ctx.store,
        {"node": f"{label}/{key}", "direction": args.get("direccion", "both"), "limit": args.get("limite", 25)},
    )
    count = len(result.get("neighbours", result.get("nodes", []))) if isinstance(result, dict) else 0
    ctx.observations.append(f"miré los vecinos de {label}/{key}: {count} nodo(s)")
    return result


def _caminos(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    result = find_paths(
        ctx.store,
        {"src": args["desde"], "dst": args["hasta"], "max_hops": args.get("saltos", 3), "limit": 5},
    )
    paths = result.get("paths", []) if isinstance(result, dict) else []
    ctx.observations.append(f"busqué caminos entre {args['desde']} y {args['hasta']}: {len(paths)} encontrado(s)")
    return result


def _red(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Wraps the ``network`` tool. The keys read here are the ones it actually returns.

    An earlier version counted ``shared_address``, a key this tool has never had, so
    a supplier with a real shared address was reported to the model as "0 domicilios
    compartidos". The agent then concluded there was nothing to see and asserted
    nothing. A silent zero in a tool summary is the most expensive kind of bug in this
    system: it does not fail, it changes the conclusion.
    """
    result = network(ctx.store, {"rut": args["rut"]})
    peers = result.get("peers") or []
    domicilio = result.get("domicilio") or {}
    if peers:
        where = domicilio.get("direccion") or "domicilio no registrado"
        ctx.observations.append(
            f"la red de {args['rut']} tiene {len(peers)} empresa(s) en el mismo domicilio ({where})"
        )
    else:
        ctx.observations.append(f"la red de {args['rut']} no muestra domicilios compartidos")
    return result


def _citas(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    result = citation_trail(ctx.store, {"node": args["nodo"], "limit": args.get("limite", 25)})
    trail = result.get("trail", []) if isinstance(result, dict) else []
    ctx.observations.append(f"leí la cadena de evidencia de {args['nodo']}: {len(trail)} cita(s)")
    return result


def _senal_detalle(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from ..audit import signal_pack

    signal_id = str(args["senal_id"]).replace("Senal/", "").strip()
    pack = signal_pack(ctx.store, signal_id, order_limit=int(args.get("limite") or 40))
    if pack.get("error"):
        ctx.observations.append(f"no pude abrir la señal {signal_id}: {pack['error']}")
        return pack
    counts = pack.get("counts") or {}
    ctx.observations.append(
        f"abrir la señal {signal_id}: {counts.get('orders_cited')} orden(es), "
        f"{counts.get('orders_with_document')} con documento, "
        f"{'cadena completa' if pack.get('complete') else 'CADENA INCOMPLETA'}"
    )
    return pack


def _senales_de(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from ..audit import signals_for

    label = args.get("label") or "Proveedor"
    found = signals_for(ctx.store, label, args["clave"])
    ctx.observations.append(
        f"busqué señales de {label}/{args['clave']}: {len(found)} encontrada(s)"
    )
    return {
        "label": label,
        "clave": args["clave"],
        "count": len(found),
        "senales": found,
        "nota": "El vínculo es por nombre exacto, no por identificador. Un resultado vacío no "
        "significa que no haya nada: significa que el nombre no coincide literalmente.",
    }


def _perfil_de_gasto(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from ..audit import spend_profile

    profile = spend_profile(ctx.store, args.get("label") or "Organismo", args["clave"])
    if profile.get("error"):
        ctx.observations.append(f"no pude perfilar {args['clave']}: {profile['error']}")
        return profile
    ctx.observations.append(
        f"perfil de {profile.get('name')}: {profile.get('orders')} órdenes, "
        f"${profile.get('total_clp'):,} en total, "
        f"{profile.get('orders_below_threshold')} bajo el umbral"
    )
    return profile


def _documento_de_orden(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from ..audit import order_document

    document = order_document(ctx.store, args["codigo"])
    if document.get("has_document"):
        ctx.observations.append(
            f"la orden {args['codigo']} tiene documento: {document.get('url')} "
            f"(sha256 {str(document.get('hash_sha256'))[:12]}…)"
        )
    else:
        ctx.observations.append(f"la orden {args['codigo']} NO tiene documento adjunto")
    return document


def _buscar_web(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    hits = (ctx.searcher or web_search)(args["consulta"], limit=args.get("limite", 6))
    ctx.observations.append(f"busqué en la web «{args['consulta']}»: {len(hits)} candidato(s)")
    return {
        "candidatos": [hit.summary() for hit in hits],
        "recordatorio": "Un candidato no es una fuente. Hay que leerlo con leer_fuente para poder citarlo.",
    }


def _leer_fuente(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = args["url"]
    if url in ctx.seen_urls:
        existing = ctx.registry.get(url)
        if existing is not None:
            return {
                "source_id": existing.id,
                "ya_leida": True,
                "extracto": existing.text[:1500],
                "nota": "ya estaba en el registro; no se descarga dos veces",
            }
    if ctx.web_fetches >= MAX_WEB_FETCHES:
        ctx.log("budget.exceeded", {"limit": "web_fetches", "max": MAX_WEB_FETCHES})
        return {"error": f"límite de {MAX_WEB_FETCHES} descargas alcanzado; usa lo que ya tienes"}

    ctx.web_fetches += 1
    ctx.seen_urls.add(url)
    try:
        source = (ctx.fetcher or fetch)(url)
    except FetchError as exc:
        ctx.log("source.rejected", {"url": url, "reason": str(exc)})
        ctx.observations.append(f"no pude leer {url}: {exc}")
        return {"error": str(exc)}

    stored = ctx.registry.add(source)
    ctx.log(
        "source.added",
        {"id": stored.id, "url": stored.url, "sha256": stored.sha256, "title": stored.title, "official": stored.official},
    )
    ctx.observations.append(f"leí {stored.domain}: «{stored.title[:70]}»")
    return {
        "source_id": stored.id,
        "url": stored.url,
        "sha256": stored.sha256,
        "titulo": stored.title,
        "oficial": stored.official,
        "caracteres": len(stored.text),
        "extracto": stored.text[:3000],
        "regla": "Para citar esta fuente, copia una frase exacta del extracto en el campo 'cita' de afirmar.",
    }


def _afirmar(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """The gate for a single claim. Returns accepted or rejected, never softened."""
    texto = (args.get("texto") or "").strip()
    ordenes = [code for code in (args.get("ordenes") or []) if str(code).strip()]
    senales = [sid for sid in (args.get("senales") or []) if str(sid).strip()]
    nodos = [ref for ref in (args.get("nodos") or []) if str(ref).strip()]
    documentos = [h for h in (args.get("documentos") or []) if str(h).strip()]
    source_id = (args.get("source_id") or "").strip()
    cita = (args.get("cita") or "").strip()

    if not texto:
        return {"aceptada": False, "motivo": "la afirmación está vacía"}
    if len(ctx.claims) >= MAX_CLAIMS:
        ctx.log("budget.exceeded", {"limit": "claims", "max": MAX_CLAIMS})
        return {"aceptada": False, "motivo": f"límite de {MAX_CLAIMS} afirmaciones alcanzado"}

    # A claim needs support from one of the two worlds, and the two are checked
    # differently on purpose: the graph is authoritative for what it contains, the
    # web is authoritative only for what it literally said.
    if source_id:
        claim, reason = verify_claim(ctx.registry, text=texto, source_id=source_id, quotes=[cita])
        if claim is None:
            ctx.log("claim.rejected", {"text": texto, "reason": reason, "source_id": source_id})
            ctx.rejections.append({"texto": texto, "motivo": reason})
            ctx.observations.append(f"rechacé una afirmación: {reason}")
            return {"aceptada": False, "motivo": reason}
        entry = {"texto": texto, "origen": "web", "cita": claim.quotes[0].text,
                 "url": claim.url, "source_id": claim.source_id, "sha256": claim.sha256,
                 "dominio": claim.domain, "oficial": claim.official,
                 "offset": claim.quotes[0].offset}
    elif documentos and not (ordenes or senales or nodos):
        verified = _verify_documents(ctx.store, documentos)
    elif ordenes or senales or nodos:
        verified = _verify_graph_reference(ctx.store, ordenes, senales, nodos)
        if documentos and verified["ok"]:
            extra = _verify_documents(ctx.store, documentos)
            if not extra["ok"]:
                verified = extra
            else:
                verified["nodos"] = verified["nodos"] + extra["nodos"]
        if not verified["ok"]:
            ctx.log("claim.rejected", {"text": texto, "reason": verified["motivo"]})
            ctx.rejections.append({"texto": texto, "motivo": verified["motivo"]})
            return {"aceptada": False, "motivo": verified["motivo"]}
        entry = {"texto": texto, "origen": "grafo", "ordenes": ordenes, "senales": senales,
                 "nodos": verified["nodos"]}
    else:
        motivo = "sin soporte: ni registros del grafo ni cita de una fuente descargada"
        ctx.log("claim.rejected", {"text": texto, "reason": motivo})
        ctx.rejections.append({"texto": texto, "motivo": motivo})
        return {"aceptada": False, "motivo": motivo}

    entry["id"] = "claim:" + hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    ctx.claims.append(entry)
    ctx.log("claim.accepted", entry)
    ctx.observations.append(f"afirmación aceptada ({entry['origen']}): {texto[:80]}")
    return {"aceptada": True, "id": entry["id"], "origen": entry["origen"],
            "total": len(ctx.claims)}


def _verify_graph_reference(
    store, ordenes: list[str], senales: list[str], nodos: list[str] | None = None
) -> dict[str, Any]:
    """A graph claim must point at nodes that exist. Cheap, deterministic, no model.

    Every referenced node is looked up individually, so a claim that mixes three real
    orders with one invented code is rejected rather than partly credited.
    """
    found: list[str] = []
    missing: list[str] = []
    for code in ordenes:
        node = store.node("OrdenCompra", code)
        (found if node is not None else missing).append(f"OrdenCompra/{code}")
    for signal_id in senales:
        node = store.node("Senal", signal_id)
        (found if node is not None else missing).append(f"Senal/{signal_id}")
    for reference in nodos or []:
        try:
            label, key = _split_node(reference)
        except ValueError:
            missing.append(reference)
            continue
        node = store.node(label, key)
        (found if node is not None else missing).append(f"{label}/{key}")
    if missing and not found:
        return {"ok": False, "motivo": f"los registros citados no existen en el grafo: {', '.join(missing[:3])}", "nodos": []}
    if missing:
        return {"ok": False, "motivo": f"algunos registros citados no existen: {', '.join(missing[:3])}", "nodos": found}
    return {"ok": True, "motivo": None, "nodos": found}


def _verify_documents(store, hashes: list[str]) -> dict[str, Any]:
    """A claim backed by a document must name a hash that is in the graph.

    This is the strongest citation the system has: the hash is over the bytes we
    received from the official API, so a reader can re-fetch and compare. A hash that
    does not exist in the graph is either a typo or an invention, and both are refused.
    """
    client = getattr(store, "client", None)
    if client is None:
        return {"ok": False, "motivo": "esta verificación necesita el motor MillenniumDB", "nodos": []}
    from ..graph.schema import ONTO

    found: list[str] = []
    missing: list[str] = []
    for digest in hashes:
        rows = client.select(
            f"SELECT ?s WHERE {{ ?s <{ONTO}hash_sha256> \"{digest}\" }} LIMIT 1"
        )
        if rows:
            found.append(f"Documento/{digest[:12]}")
        else:
            missing.append(digest)
    if missing:
        return {
            "ok": False,
            "motivo": f"documento(s) no encontrados en el grafo: {', '.join(m[:12] for m in missing[:3])}",
            "nodos": found,
        }
    return {"ok": True, "motivo": None, "nodos": found}


def _terminar(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ctx.finished = {"resumen": (args.get("resumen") or "").strip(), "vacios": list(args.get("vacios") or [])}
    ctx.observations.append("cerré la investigación")
    return {"cerrada": True, "resumen": ctx.finished["resumen"], "vacios": ctx.finished["vacios"]}


HANDLERS: dict[str, Callable[[ToolContext, dict[str, Any]], dict[str, Any]]] = {
    "buscar_en_grafo": _buscar_en_grafo,
    "vecinos": _vecinos,
    "caminos": _caminos,
    "red_proveedor": _red,
    "citas": _citas,
    "senal_detalle": _senal_detalle,
    "senales_de": _senales_de,
    "perfil_de_gasto": _perfil_de_gasto,
    "documento_de_orden": _documento_de_orden,
    "buscar_en_web": _buscar_web,
    "leer_fuente": _leer_fuente,
    "afirmar": _afirmar,
    "terminar": _terminar,
}


def execute(ctx: ToolContext, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
    """Run one verb, logging the call and its result. Never raises: an error is a result.

    The agent loop must be able to continue after a tool fails — a broken URL is not
    a reason to lose an investigation — but the failure has to be *visible* in the
    log, because "the tool returned nothing" and "the tool broke" are different
    facts and only one of them is the agent's fault.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError as exc:
            return {"error": f"argumentos no son JSON válido: {exc}"}
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"herramienta desconocida: {name}"}

    started = time.monotonic()
    ctx.log("tool.call", {"tool": name, "arguments": arguments})
    try:
        result = handler(ctx, arguments)
    except Exception as exc:  # a tool bug must not end the run
        elapsed = int((time.monotonic() - started) * 1000)
        message = f"{type(exc).__name__}: {exc}"
        ctx.log("tool.error", {"tool": name, "error": message, "duration_ms": elapsed})
        return {"error": message}
    elapsed = int((time.monotonic() - started) * 1000)
    ctx.log("tool.result", {"tool": name, "duration_ms": elapsed, "summary": summarise(name, result)})
    return result


def summarise(name: str, result: dict[str, Any]) -> str:
    """One line for the log panel. Written for a person, not for a parser."""
    if not isinstance(result, dict):
        return str(result)[:120]
    if "error" in result:
        return f"error: {result['error']}"[:160]
    if name == "buscar_en_grafo":
        return f"{len(result.get('nodos') or [])} nodo(s)"
    if name == "vecinos":
        for key in ("neighbours", "nodes", "vecinos"):
            if key in result:
                return f"{len(result[key])} vecino(s)"
        return "sin vecinos"
    if name == "caminos":
        return f"{len(result.get('paths') or [])} camino(s)"
    if name == "red_proveedor":
        peers = result.get("peers") or []
        if not result.get("found"):
            return "RUT no encontrado"
        if not peers:
            return "sin domicilio compartido"
        names = ", ".join(peer.get("razon_social", "?") for peer in peers[:2])
        return f"{len(peers)} empresa(s) en el mismo domicilio: {names}"
    if name == "citas":
        return f"{len(result.get('trail') or [])} cita(s)"
    if name == "senal_detalle":
        counts = result.get("counts") or {}
        return (
            f"{counts.get('orders_cited', 0)} orden(es) · "
            f"{counts.get('orders_with_document', 0)} con documento · "
            + ("cadena completa" if result.get("complete") else "cadena incompleta")
        )
    if name == "senales_de":
        return f"{result.get('count', 0)} señal(es)"
    if name == "perfil_de_gasto":
        return f"{result.get('orders', 0)} órdenes · ${result.get('total_clp', 0):,}"
    if name == "documento_de_orden":
        return "con documento" if result.get("has_document") else "sin documento"
    if name == "buscar_en_web":
        return f"{len(result.get('candidatos') or [])} candidato(s)"
    if name == "leer_fuente":
        return f"fuente {result.get('source_id')} · {result.get('titulo') or result.get('url')}"
    if name == "afirmar":
        return ("aceptada" if result.get("aceptada") else f"rechazada: {result.get('motivo')}")[:160]
    if name == "terminar":
        return "investigación cerrada"
    return "ok"


def render_observation(name: str, result: dict[str, Any]) -> str:
    """How a tool result is shown back to the model. Truncated, never paraphrased."""
    trimmed = json.dumps(result, ensure_ascii=False, default=str)
    if len(trimmed) > 6000:
        trimmed = trimmed[:6000] + ' …(truncado)"}'
    return f"[{name}] {trimmed}"

