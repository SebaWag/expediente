#!/usr/bin/env python3
"""List Compra Agil processes from the public Mercado Publico search API.

What this does, and what it does not
------------------------------------
The documented Mercado Publico API (``api.mercadopublico.cl``, ticket auth) returns
order metadata but **no document URLs**. The public search front-end at
``buscador.mercadopublico.cl/compra-agil`` is a different service, backed by
``api.buscador.mercadopublico.cl``, which needs two things the browser gets for
free at page load:

* a short-lived bearer token from ``servicios-prd.mercadopublico.cl/v1/auth/publico``
* a client key sent as ``x-api-key`` (it is embedded in the public web app; it is
  not an account credential, and it is not committed to this repository)

With both, the listing answers HTTP 200 to a plain request — no browser, no login.

So this script can enumerate the processes. It cannot, yet, download their
attachments: the per-item detail call is made by the SPA from a click handler, and
I have not identified the route. That is stated here instead of papered over,
because a ``--docs`` flag that silently returns nothing is worse than no flag.

Usage
-----
    export MP_BUSCADOR_KEY=...          # see docs/PORTAL_DOCS.md
    python3 scripts/fetch_compra_agil.py --desde 2026-09-28 --hasta 2026-10-28 \
        --out data/compra_agil_listing.json

    # cap the pages while exploring
    python3 scripts/fetch_compra_agil.py --desde 2026-10-27 --hasta 2026-10-28 --max-pages 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URL = "https://servicios-prd.mercadopublico.cl/v1/auth/publico"
SEARCH_URL = "https://api.buscador.mercadopublico.cl/compra-agil"
REFERER = "https://buscador.mercadopublico.cl/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class PortalError(RuntimeError):
    """A failure worth reporting with the URL that produced it."""


def _get(url: str, headers: dict[str, str], *, timeout: int = 30, retries: int = 4) -> bytes:
    """GET with backoff. The search API answers 502 under bursts, so retry is required."""
    last: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read()[:200].decode("utf-8", "replace")
            last = PortalError(f"{url}: HTTP {error.code} {body}")
            if error.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last
        except urllib.error.URLError as error:
            last = PortalError(f"{url}: {error.reason}")
            time.sleep(1.5 * (attempt + 1))
    raise last or PortalError(f"{url}: failed")


def client_key() -> str:
    key = os.environ.get("MP_BUSCADOR_KEY", "").strip()
    if not key:
        raise PortalError(
            "MP_BUSCADOR_KEY is not set. It is the x-api-key the public search app "
            "sends; capture it once from the browser's network tab on "
            f"{REFERER}compra-agil and export it. It is not an account credential."
        )
    return key


def access_token() -> str:
    payload = json.loads(_get(AUTH_URL, {"User-Agent": USER_AGENT}))
    body = payload.get("payload") or {}
    token = body.get("access_token", "") if isinstance(body, dict) else ""
    if not token:
        raise PortalError("the auth endpoint returned no token")
    return token

def headers() -> dict[str, str]:
    return {
        "x-api-key": client_key(),
        "Authorization": f"Bearer {access_token()}",
        "Referer": REFERER,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def list_page(filters: dict, page: int) -> dict:
    query = urllib.parse.urlencode({**filters, "page_number": page})
    payload = json.loads(_get(f"{SEARCH_URL}?{query}", headers()))
    if payload.get("success") != "OK":
        raise PortalError(f"search returned {payload.get('success')!r}: {payload.get('errors')}")
    return payload.get("payload") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desde", required=True, help="date_from, YYYY-MM-DD")
    parser.add_argument("--hasta", required=True, help="date_to, YYYY-MM-DD")
    parser.add_argument("--estado", type=int, default=2, help="status id (2 = publicada)")
    parser.add_argument("--region", default="all")
    parser.add_argument("--max-pages", type=int, default=0, help="0 = every page")
    parser.add_argument("--out", default="", help="write the items to this JSON file")
    args = parser.parse_args()

    filters = {
        "date_from": args.desde,
        "date_to": args.hasta,
        "order_by": "recent",
        "status": args.estado,
    }
    # ``region=all`` is what the web app sends, but the API answers 502 to it when
    # called directly. Omitting the parameter is the same query and it works.
    if args.region and args.region != "all":
        filters["region"] = args.region

    try:
        first = list_page(filters, 1)
    except PortalError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    total = int(first.get("resultCount") or 0)
    page_count = int(first.get("pageCount") or 0)
    limit = args.max_pages or page_count
    print(f"{total} procesos · {page_count} paginas · trayendo {min(limit, page_count)}")

    items: list[dict] = list(first.get("resultados") or [])
    for page in range(2, min(limit, page_count) + 1):
        try:
            payload = list_page(filters, page)
        except PortalError as error:
            print(f"  pagina {page}: {error}", file=sys.stderr)
            continue
        items.extend(payload.get("resultados") or [])
        if page % 10 == 0:
            print(f"  …{page}/{min(limit, page_count)} · {len(items)} items")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {args.out} ({len(items)} items)")
    else:
        print(json.dumps(items[:3], ensure_ascii=False, indent=2))

    print(
        "\nnota: esto entrega el listado (codigo, organismo, montos, fechas, "
        "proveedores cotizando). Los adjuntos siguen sin ruta identificada: "
        "ver docs/PORTAL_DOCS.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
