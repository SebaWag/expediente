"""The fields the extractor is asked for, and what a value of each kind looks like.

A field earns a place here only if deterministic code downstream can consume it.
That is the whole selection rule: the extractor exists to fill cells in a table the
detectors already know how to read, so adding a field is a promise that something
will consume it, not a wish for a richer description.

Keeping the list short is also what makes the benchmark meaningful. Nine fields
across a few hundred documents can be graded by hand; ninety cannot, and an
ungraded field is an unchecked claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import Page


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One requested cell: its name, how it is typed, and how it must be earned."""

    nombre: str
    tipo: str
    descripcion: str
    choices: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = {"nombre": self.nombre, "tipo": self.tipo, "descripcion": self.descripcion}
        if self.choices:
            payload["choices"] = list(self.choices)
        return payload


#: Kinds a value can have. Each one has a matching, dependency-free comparison in
#: ``grading``; adding a kind without adding its comparison breaks the benchmark
#: silently, which is why ``grading.grade`` raises on an unknown kind.
TIPOS = ("texto", "numero", "fecha", "rut", "enum")


PROCUREMENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        nombre="orden_compra_id",
        tipo="texto",
        descripcion="Código de la orden de compra, tal como aparece impreso (ej. 4101-26-SE26).",
    ),
    FieldSpec(
        nombre="tipo_documento",
        tipo="enum",
        descripcion="Qué documento es.",
        choices=("orden_compra", "resolucion_adjudicacion", "bases_licitacion", "contrato", "otro"),
    ),
    FieldSpec(
        nombre="procedencia",
        tipo="enum",
        descripcion="Mecanismo de compra declarado en el documento.",
        choices=("compra_agil", "licitacion_publica", "trato_directo", "convenio_marco", "otro"),
    ),
    FieldSpec(
        nombre="monto_total_clp",
        tipo="numero",
        descripcion="Monto total del documento en pesos chilenos, con impuestos.",
    ),
    FieldSpec(
        nombre="monto_neto_clp",
        tipo="numero",
        descripcion="Monto neto en pesos chilenos, sin impuestos. Si el documento no lo separa, omitir.",
    ),
    FieldSpec(
        nombre="rut_proveedor",
        tipo="rut",
        descripcion="RUT del proveedor adjudicado, con dígito verificador.",
    ),
    FieldSpec(
        nombre="unidad_compradora",
        tipo="texto",
        descripcion="Nombre de la unidad u organismo que compra.",
    ),
    FieldSpec(
        nombre="fecha_orden",
        tipo="fecha",
        descripcion="Fecha de emisión del documento.",
    ),
)

REQUIRED_FIELDS: tuple[str, ...] = tuple(spec.nombre for spec in PROCUREMENT_FIELDS)

_INSTRUCCIONES = """Eres un extractor de campos de documentos de compra pública chilena.

Devuelves SOLO un objeto JSON, sin texto antes ni después, con esta forma:
{{"campos": {{"<nombre>": {{"valor": "<texto literal>", "pagina": <entero>, "extracto": "<cita literal de la página>"}}}}}}

Reglas que no puedes romper:
1. Solo extraes lo que está escrito. Si un campo no aparece en el documento, NO lo incluyes. No lo inventas, no lo deduces, no lo completas con lo que suele ser.
2. Cada campo que incluyes lleva la página donde aparece el valor y la cita literal de esa página. Si no puedes dar la página, no incluyas el campo.
3. El valor va como está escrito en el documento, salvo los números, que van como dígitos sin puntos ni símbolos (940000, no $940.000).
4. Si el documento contradice lo que se espera de su tipo, describe lo que dice el documento.

Campos solicitados:
{campos}"""


def by_name() -> dict[str, FieldSpec]:
    return {spec.nombre: spec for spec in PROCUREMENT_FIELDS}


def system_prompt(specs: Sequence[FieldSpec] | None = None) -> str:
    """Build the instruction. Schema goes in the prompt, values never do."""
    selected = tuple(specs) if specs is not None else PROCUREMENT_FIELDS
    lines = []
    for spec in selected:
        suffix = f" Valores posibles: {', '.join(spec.choices)}." if spec.choices else ""
        lines.append(f"- {spec.nombre} ({spec.tipo}): {spec.descripcion}{suffix}")
    return _INSTRUCCIONES.format(campos="\n".join(lines))


def user_prompt(pages: Sequence[Page], specs: Sequence[FieldSpec] | None = None) -> str:
    """Render the pages, numbers included, so every answer can cite one."""
    selected = tuple(specs) if specs is not None else PROCUREMENT_FIELDS
    names = ", ".join(spec.nombre for spec in selected)
    blocks = [f"--- PÁGINA {page.numero} ---\n{page.texto}" for page in pages]
    return f"Extrae: {names}.\n\n" + "\n\n".join(blocks)
