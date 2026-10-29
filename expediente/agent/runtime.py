"""The loop: plan, act, observe — and the gate at the end.

This is where natural language becomes a traversal. A person writes *"¿por qué la
municipalidad le compró lo mismo seis veces al mismo proveedor?"* and the loop turns
it into named verbs over the graph, one step at a time, recording each one.

What this loop deliberately is not
----------------------------------

It is not a chat. There is no path here that returns prose as an answer. The model
chooses a verb, deterministic code runs it, the result is fed back, and the only way
a conclusion enters the output is through ``afirmar`` — which checks a citation and
rejects the claim if the citation does not hold. A run that ends with zero accepted
claims is a valid, honest result, and the dossier says so in as many words.

Budget
------

Three hard caps, enforced in code rather than in the prompt: steps, model calls and
wall clock. A prompt that says "be efficient" is a preference; a counter that stops
the loop is a control. When a cap is hit the run closes with what it has and records
``budget.exceeded``, because an investigation that ran out of budget is a fact the
reader is entitled to know.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..research import SourceRegistry
from ..runs import (
    BUDGET,
    DOSSIER_BUILT,
    MODEL_REQUEST,
    MODEL_RESPONSE,
    PLAN,
    RUN_FAILED,
    RUN_FINISHED,
    STEP,
    Run,
    RunStore,
)
from . import tools as toolbox
from .providers import MIN_OUTPUT_TOKENS, ModelReply, Provider, ProviderError

SYSTEM_PROMPT = """Eres un investigador de compras públicas de Chile. Trabajas sobre un \
grafo de 4,9 millones de órdenes de compra reales, con sus proveedores, organismos y \
señales detectadas por reglas.

Tu trabajo es RECORRER ese grafo, no conversar. En cada turno eliges UNA herramienta, \
miras el resultado y decides el siguiente paso.

Reglas que no puedes saltarte:

1. Nunca inventes un identificador. Los nodos se citan como "Proveedor/76.264.138-0" u \
"OrdenCompra/1234-5678-OC26". Si no lo viste en un resultado, no existe.
2. Una conclusión solo entra al expediente por la herramienta `afirmar`. Si no puedes \
citar un registro del grafo o una frase textual de una fuente que descargaste, NO afirmes.
3. Un candidato de búsqueda web no es una fuente. Solo cuenta lo que abriste con `leer_fuente`.
4. Cuando copies una cita, cópiala palabra por palabra del extracto. No la parafrasees ni \
la corrijas.
5. Un domicilio compartido también es lo que parece un edificio de oficinas. Eso es una \
pista para revisar, no una relación afirmada. Si la evidencia no alcanza, dilo.
6. Escribe en español llano, para alguien que no es informático. Sin jerga técnica.
7. Si tras revisar no encuentras nada sostenible, es una respuesta válida: dilo y cierra \
con `terminar`. No rellenes.
8. Afirma en cuanto tengas soporte, no al final. Una afirmación con su cita vale más que \
tres exploraciones más. Si te quedan dos pasos, afirma y cierra.
9. Si una herramienta trae un campo `not_claimed`, esa salvedad es parte de la respuesta: \
no puedes afirmar algo que la contradiga.

Orden de trabajo recomendado: ubicar la entidad en el grafo, ver qué cuelga de ella, leer \
la cadena de evidencia de lo que parezca relevante y, solo si necesitas contexto externo \
(prensa, Contraloría, el municipio), salir a la web. Después afirmas lo que sostengas y \
cierras."""

PLAN_PROMPT = """Pregunta del usuario: {question}

Estas son las ÚNICAS herramientas que existen:
{verbs}

Escribe un plan de investigación de 3 a 5 pasos, uno por línea, sin numeración y sin \
explicaciones. Cada línea debe nombrar una de esas herramientas, con ese nombre exacto, y \
qué esperas encontrar con ella. No inventes herramientas. No respondas la pregunta todavía."""


def verb_catalogue() -> str:
    """The plan prompt names the real verbs, generated from the schemas.

    A hand-written list would drift, and the first live run showed what that costs:
    the plan said "buscar_proveedor" and "ver_vecinos_del_grafo", verbs that do not
    exist, because the plan call had no tool list to look at.
    """
    return "\n".join(
        f"- {schema['function']['name']}: {schema['function']['description'].splitlines()[0]}"
        for schema in toolbox.SCHEMAS
    )


@dataclass
class Settings:
    """Caps enforced by code. Tuned for a demo that has to answer in seconds."""

    max_steps: int = 8
    max_model_calls: int = 12
    max_wall_s: float = 240.0
    step_tokens: int = MIN_OUTPUT_TOKENS
    plan_tokens: int = 600
    #: Two consecutive prose answers with no tool call means the model is not going to
    #: use the verbs; one nudge is fair, a third is a runaway.
    max_nudges: int = 1


@dataclass
class Investigation:
    """The outcome of a run: what was asserted, what was refused, and the trail."""

    run_id: str
    question: str
    plan: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    gaps: list[str] = field(default_factory=list)
    dossier_id: str = ""
    steps: int = 0
    error: str | None = None
    model_calls: int = 0
    wall_s: float = 0.0
    stopped_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "plan": self.plan,
            "claims": self.claims,
            "rejections": self.rejections,
            "sources": self.sources,
            "summary": self.summary,
            "gaps": self.gaps,
            "dossier_id": self.dossier_id,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "wall_s": round(self.wall_s, 2),
            "stopped_by": self.stopped_by,
            "error": self.error,
        }


class Investigator:
    """Runs one investigation. Stateless between runs on purpose: two runs must not
    be able to influence each other through a cached conclusion."""

    def __init__(
        self,
        store: Any,
        provider: Provider,
        *,
        runs: RunStore | None = None,
        run: Run | None = None,
        settings: Settings | None = None,
        fetcher: Any = None,
        searcher: Any = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.runs = runs
        self.settings = settings or Settings()
        self._fetcher = fetcher
        self._searcher = searcher
        self._run = run

    # -- public ------------------------------------------------------------
    def investigate(self, question: str, *, subject: str | None = None) -> Investigation:
        question = (question or "").strip()
        if not question:
            raise ValueError("la pregunta no puede estar vacía")

        run = self._run or (self.runs.create(question=question, subject=subject) if self.runs else Run("/tmp/expediente-runs", "adhoc"))
        result = Investigation(run_id=run.id, question=question)
        registry = SourceRegistry()
        ctx = toolbox.ToolContext(
            store=self.store,
            registry=registry,
            run=run,
            fetcher=self._fetcher,
            searcher=self._searcher,
        )

        started = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        try:
            result.plan = self._plan(run, question, result)
            messages.append(
                {
                    "role": "user",
                    "content": "Este es tu plan:\n" + "\n".join(f"- {step}" for step in result.plan)
                    + "\n\nEmpieza por el primer paso. Una herramienta por turno.",
                }
            )
            self._loop(run, ctx, messages, result, started)
        except ProviderError as exc:
            result.error = str(exc)
            run.events.append(RUN_FAILED, {"error": str(exc), "model_calls": result.model_calls})

        result.wall_s = time.monotonic() - started
        result.claims = list(ctx.claims)
        result.rejections = list(ctx.rejections)
        result.sources = registry.summaries()
        if ctx.finished:
            result.summary = ctx.finished.get("resumen") or ""
            result.gaps = list(ctx.finished.get("vacios") or [])
        self._build_dossier(run, result)
        run.set_meta(
            status="failed" if result.error else "finished",
            steps=result.steps,
            claims=len(result.claims),
            sources=len(result.sources),
            dossier_id=result.dossier_id,
            model_calls=result.model_calls,
        )
        run.events.append(
            RUN_FINISHED,
            {
                "claims": len(result.claims),
                "rejected": len(result.rejections),
                "sources": len(result.sources),
                "steps": result.steps,
                "model_calls": result.model_calls,
                "wall_s": round(result.wall_s, 2),
                "stopped_by": result.stopped_by,
                "dossier_id": result.dossier_id,
            },
        )
        return result

    # -- internals ---------------------------------------------------------
    def _plan(self, run: Run, question: str, result: Investigation) -> list[str]:
        run.events.append(MODEL_REQUEST, {"purpose": "plan", "reasoning_effort": "low"})
        reply = self.provider.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": PLAN_PROMPT.format(question=question, verbs=verb_catalogue())},
            ],
            reasoning_effort="low",
            max_tokens=max(self.settings.plan_tokens, MIN_OUTPUT_TOKENS),
        )
        result.model_calls += 1
        run.events.append(
            MODEL_RESPONSE,
            {
                "purpose": "plan",
                "usage": reply.usage,
                "reasoning_tokens": reply.reasoning_tokens,
                "chars": len(reply.text),
            },
        )
        steps = [
            line.strip(" -*\t")
            for line in reply.text.splitlines()
            if line.strip() and not line.strip().lower().startswith(("plan", "aquí", "aqui"))
        ]
        steps = [step for step in steps if len(step) > 8][:5]
        if not steps:
            steps = ["Ubicar la entidad en el grafo", "Revisar qué cuelga de ella", "Leer la cadena de evidencia"]
        run.events.append(PLAN, {"steps": steps})
        return steps

    def _loop(
        self,
        run: Run,
        ctx: toolbox.ToolContext,
        messages: list[dict[str, Any]],
        result: Investigation,
        started: float,
    ) -> None:
        nudges = 0
        while True:
            if result.steps >= self.settings.max_steps:
                result.stopped_by = f"pasos ({self.settings.max_steps})"
                run.events.append(BUDGET, {"limit": "steps", "max": self.settings.max_steps})
                break
            if result.model_calls >= self.settings.max_model_calls:
                result.stopped_by = f"llamadas al modelo ({self.settings.max_model_calls})"
                run.events.append(BUDGET, {"limit": "model_calls", "max": self.settings.max_model_calls})
                break
            elapsed = time.monotonic() - started
            if elapsed >= self.settings.max_wall_s:
                result.stopped_by = f"tiempo ({int(self.settings.max_wall_s)}s)"
                run.events.append(BUDGET, {"limit": "wall_s", "max": self.settings.max_wall_s})
                break

            run.events.append(MODEL_REQUEST, {"purpose": "step", "reasoning_effort": "low", "tools": list(toolbox.TOOL_ORDER)})
            reply: ModelReply = self.provider.chat(
                messages,
                tools=toolbox.SCHEMAS,
                reasoning_effort="low",
                max_tokens=self.settings.step_tokens,
            )
            result.model_calls += 1
            run.events.append(
                MODEL_RESPONSE,
                {
                    "purpose": "step",
                    "usage": reply.usage,
                    "reasoning_tokens": reply.reasoning_tokens,
                    "tool_calls": [call.name for call in reply.tool_calls],
                    "chars": len(reply.text),
                },
            )

            if not reply.wants_tools:
                if reply.text and nudges < self.settings.max_nudges:
                    nudges += 1
                    # The model answered in prose instead of using a verb. Prose is not
                    # an output of this system: it cannot be cited and therefore cannot
                    # be signed. It gets one correction and then the run closes.
                    messages.append({"role": "assistant", "content": reply.text})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Eso es prosa y no queda registrado: solo entra al expediente lo que "
                            "pasa por `afirmar`. Si tienes algo sostenible, usa `afirmar` con su cita. "
                            "Si ya terminaste, usa `terminar`.",
                        }
                    )
                    continue
                if reply.text and not ctx.finished:
                    ctx.finished = {"resumen": reply.text[:1200], "vacios": ["la investigación no se cerró con `terminar`"]}
                result.stopped_by = result.stopped_by or "el modelo no llamó ninguna herramienta"
                break

            call = reply.tool_calls[0]
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                    ],
                }
            )
            payload = toolbox.execute(ctx, call.name, call.arguments)
            result.steps += 1
            run.events.append(
                STEP,
                {"n": result.steps, "tool": call.name, "summary": toolbox.summarise(call.name, payload),
                 "observation": ctx.observations[-1] if ctx.observations else None},
            )
            remaining = self.settings.max_steps - result.steps
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": toolbox.render_observation(call.name, payload)
                    + f"\n\n[presupuesto] te quedan {remaining} paso(s). "
                    + (
                        "Último paso: usa `afirmar` con lo que puedas sostener, y luego `terminar`."
                        if remaining <= 1
                        else "Si ya tienes soporte para una conclusión, usa `afirmar` antes de seguir explorando."
                    ),
                }
            )

            if call.name == "terminar" or ctx.finished is not None:
                result.stopped_by = "terminar"
                break

    def _build_dossier(self, run: Run, result: Investigation) -> None:
        """Content-derived id, and an explicit statement when there is nothing to say."""
        body = {
            "question": result.question,
            "claims": result.claims,
            "rejections": [rejection.get("motivo") for rejection in result.rejections],
            "sources": [{"id": source["id"], "sha256": source["sha256"], "url": source["url"]} for source in result.sources],
            "summary": result.summary,
            "gaps": result.gaps,
        }
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        result.dossier_id = f"sha256:{digest[:32]}"

        lines: list[str] = [f"# Expediente · {result.question}", ""]
        if result.summary:
            lines += ["## Qué se encontró", "", result.summary, ""]
        if result.claims:
            lines += ["## Afirmaciones sostenidas", ""]
            for claim in result.claims:
                if claim["origen"] == "web":
                    lines.append(f"- {claim['texto']}")
                    lines.append(f"  > «{claim['cita']}» — {claim['dominio']} · {claim['url']} · sha256:{claim['sha256'][:12]}")
                else:
                    lines.append(f"- {claim['texto']}")
                    lines.append(f"  > registros: {', '.join(claim.get('nodos') or [])}")
            lines.append("")
        else:
            lines += [
                "## Qué se encontró",
                "",
                "**Ninguna afirmación sobrevivió al control de evidencia.** No es que no se haya buscado: "
                "es que nada de lo encontrado pudo sostenerse con un registro o una cita textual.",
                "",
            ]
        if result.rejections:
            lines += ["## Qué se descartó, y por qué", ""]
            for rejection in result.rejections:
                lines.append(f"- {rejection.get('texto', '')[:160]} → *{rejection.get('motivo')}*")
            lines.append("")
        if result.gaps:
            lines += ["## Preguntas abiertas", ""] + [f"- {gap}" for gap in result.gaps] + [""]
        lines += ["---", f"id del expediente: `{result.dossier_id}`", f"run: `{result.run_id}`"]

        run.write_artifact("expediente.md", "\n".join(lines))
        run.events.append(
            DOSSIER_BUILT,
            {"id": result.dossier_id, "claims": len(result.claims), "rejected": len(result.rejections), "sources": len(result.sources)},
        )
