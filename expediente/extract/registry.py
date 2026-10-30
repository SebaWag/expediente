"""Which backend answers, and the record of that choice.

The rule: the tuned extractor is optional, so its absence must never be an error.
``choose`` returns a backend *and* says whether it degraded, and why. That note is
not decoration — it travels into the run report, because "this dossier was produced
by the serverless model at low effort" is a fact a reviewer is entitled to.

A system that silently swaps one model for another is a system whose numbers
cannot be compared across runs. So the swap is loud in the record and quiet in the
code path: same interface, same grading, one line of configuration apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import Backend, NotConfigured, TunedBackend, ZeroShotBackend


@dataclass(frozen=True, slots=True)
class BackendChoice:
    """A backend plus the honest note about why it is the one that answered."""

    backend: Backend
    degraded: bool
    motivo: str = ""

    def to_dict(self) -> dict:
        return {"backend": getattr(self.backend, "name", "?"), "degraded": self.degraded, "motivo": self.motivo}


def choose(prefer: str = "auto", effort: str | None = None) -> BackendChoice:
    """Pick a backend.

    ``prefer='auto'`` (default) uses the tuned extractor when one is deployed and
    falls back to serverless when it is not. ``prefer='tuned'`` raises instead of
    falling back, which is what a benchmark needs when it means to compare two
    backends rather than to get an answer.
    """
    if prefer not in {"auto", "tuned", "zero_shot"}:
        raise ValueError(f"unknown preference: {prefer!r}")

    tuned = TunedBackend(effort=effort or "low") if effort else TunedBackend()
    if prefer in {"auto", "tuned"}:
        try:
            tuned.resolve_model()
        except NotConfigured as error:
            if prefer == "tuned":
                raise
            zero_shot = ZeroShotBackend(effort=effort) if effort else ZeroShotBackend()
            return BackendChoice(
                backend=zero_shot,
                degraded=True,
                motivo=f"tuned extractor not deployed ({error}); serverless answered",
            )
        return BackendChoice(backend=tuned, degraded=False, motivo="tuned extractor deployed")

    return BackendChoice(backend=ZeroShotBackend(effort=effort) if effort else ZeroShotBackend(), degraded=False)
