from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import EXTERNAL_API_URL


logger = logging.getLogger(__name__)


async def send_json_payload(data: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(EXTERNAL_API_URL, json=data)
    response.raise_for_status()
    logger.info("Sent JSON payload to external API: status=%s", response.status_code)

    return response.json()


async def send_file_payload(file_bytes: bytes, filename: str, content_type: str) -> dict[str, Any]:
    files = {"file": (filename, file_bytes, content_type)}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(EXTERNAL_API_URL, files=files)
    response.raise_for_status()
    logger.info("Sent file '%s' to external API: status=%s", filename, response.status_code)

    return response.json()
