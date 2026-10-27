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
