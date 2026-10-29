"""Investigation runs: the recording of what the agent did.

See :mod:`expediente.runs.events` — an append-only log, a replay that needs no
model, and a run directory that a browser can stream.
"""

from .events import (
    BUDGET,
    CLAIM_ACCEPTED,
    CLAIM_REJECTED,
    DOSSIER_BUILT,
    MODEL_REQUEST,
    MODEL_RESPONSE,
    PLAN,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    SOURCE_ADDED,
    SOURCE_REJECTED,
    STEP,
    TOOL_CALL,
    TOOL_ERROR,
    TOOL_RESULT,
    Event,
    EventLog,
    Run,
    RunStore,
    new_run_id,
    replay,
)

__all__ = [
    "BUDGET",
    "CLAIM_ACCEPTED",
    "CLAIM_REJECTED",
    "DOSSIER_BUILT",
    "MODEL_REQUEST",
    "MODEL_RESPONSE",
    "PLAN",
    "RUN_FAILED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "SOURCE_ADDED",
    "SOURCE_REJECTED",
    "STEP",
    "TOOL_CALL",
    "TOOL_ERROR",
    "TOOL_RESULT",
    "Event",
    "EventLog",
    "Run",
    "RunStore",
    "new_run_id",
    "replay",
]
