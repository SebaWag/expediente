"""Runtime configuration.

Reads a local ``.env`` when present. Nothing here ever writes, logs or
serialises a secret: the ticket is read, used and forgotten.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env")


def load_dotenv(path: Path | None = None) -> None:
    """Populate ``os.environ`` from a .env file. Real environment variables win."""
    env_path = path or DEFAULT_ENV_FILE
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def mercadopublico_ticket() -> str:
    """Return the Mercado Público ticket, or explain how to get one."""
    load_dotenv()
    ticket = os.environ.get("MP_TICKET", "").strip()
    if not ticket or ticket.startswith("replace"):
        raise RuntimeError(
            "MP_TICKET is not set. Copy .env.example to .env and paste the ticket "
            "Mercado Público sends by email (request it at api.mercadopublico.cl)."
        )
    return ticket


def fireworks_api_key() -> str:
    """Return the Fireworks key, or explain what is missing.

    Written the same way as the ticket above: read, used, forgotten. The key is
    never logged, never written into a run report, and never included in an error
    message — a cost report that leaks a credential is a worse bug than the one it
    was reporting.
    """
    load_dotenv()
    key = os.environ.get("FIREWORKS_API_KEY", "").strip()
    if not key or key.startswith("replace"):
        raise RuntimeError(
            "FIREWORKS_API_KEY is not set. Put it in .env (see .env.example); the key "
            "is in the Fireworks dashboard under API keys."
        )
    return key


def fireworks_base_url() -> str:
    """OpenAI-compatible endpoint root. Regional variants exist; we take the global one."""
    load_dotenv()
    return os.environ.get("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1").strip()


def fireworks_account_id() -> str:
    """Account id, needed to address an on-demand deployment by resource path."""
    load_dotenv()
    return os.environ.get("FIREWORKS_ACCOUNT_ID", "").strip()


def extract_deployment() -> str | None:
    """Deployment id of the tuned extractor, if one was deployed.

    Unset is a valid, expected state: the LoRA is a nice-to-have, so the serverless
    backend answers whenever this is empty.
    """
    load_dotenv()
    value = os.environ.get("EXPEDIENTE_EXTRACT_DEPLOYMENT", "").strip()
    return value or None
