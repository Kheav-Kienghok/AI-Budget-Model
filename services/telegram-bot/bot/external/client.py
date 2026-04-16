from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import EXTERNAL_API_URL

logger = logging.getLogger(__name__)


def build_external_url(path_or_url: str) -> str:
    """Build a full URL from EXTERNAL_API_URL and a relative path."""

    raw = (path_or_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    base = EXTERNAL_API_URL.rstrip("/")
    if not raw:
        return base

    suffix = raw if raw.startswith("/") else f"/{raw}"
    return f"{base}{suffix}"


async def send_json_payload(data: Any, endpoint: str = "") -> dict[str, Any]:
    """Send a JSON payload to the external API.

    If `endpoint` is empty, EXTERNAL_API_URL is treated as the full URL.
    Otherwise we join EXTERNAL_API_URL as a base with the given endpoint
    (for example, "/insights").
    """

    base = EXTERNAL_API_URL.rstrip("/")
    url = (
        base
        if not endpoint
        else f"{base}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    )

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=data)
    response.raise_for_status()
    logger.info(
        "Sent JSON payload to external API at %s: status=%s", url, response.status_code
    )

    return response.json()


async def fetch_binary_from_external(path_or_url: str) -> bytes:
    """Fetch binary content from EXTERNAL_API_URL or an absolute URL."""

    url = build_external_url(path_or_url)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
    response.raise_for_status()
    logger.info("Downloaded binary payload from external API at %s", url)
    return response.content


async def send_file_payload(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    endpoint: str,
) -> dict[str, Any]:
    """Send a CSV file to the external API.

    `endpoint` should be one of: "/classify", "/forecast", "/insights".
    `EXTERNAL_API_URL` is expected to be the base URL, e.g. "http://localhost:8000".
    """

    # Force a sensible MIME type for CSV uploads so the backend
    # does not see `application/json` by mistake.
    if filename.lower().endswith(".csv"):
        content_type = "text/csv"

    base_url = EXTERNAL_API_URL.rstrip("/")
    endpoint_path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{base_url}{endpoint_path}"

    files = {"file": (filename, file_bytes, content_type)}

    try:
        logger.info(
            "Sending file '%s' to external API at %s with content type '%s'",
            filename,
            url,
            content_type,
        )
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, files=files)
        response.raise_for_status()
        logger.info(
            "Sent file '%s' to external API: status=%s", filename, response.status_code
        )
        return response.json()
    except httpx.RequestError:
        logger.exception("Error talking to external API at %s", EXTERNAL_API_URL)
        raise
