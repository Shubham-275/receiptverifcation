"""
OpenRouter API client for LLM inference (Google Gemma 3 12B IT, etc.).
Uses httpx.AsyncClient with connection pooling, structured error handling, and multimodal vision support.
"""
import base64
import time
from typing import Any

import httpx

from app.config import get_settings
from app.exceptions import AIServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

_http_client: httpx.AsyncClient | None = None


def get_openrouter_http_client() -> httpx.AsyncClient:
    """Return a shared singleton httpx.AsyncClient."""
    global _http_client
    if _http_client is None or getattr(_http_client, "is_closed", False):
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=10.0),
            headers={
                "HTTP-Referer": "https://receipt-intelligence.local",
                "X-Title": "Receipt Intelligence",
            },
        )
    return _http_client


async def generate_chat_completion(
    prompt: str,
    image_bytes: bytes | None = None,
    mime_type: str = "image/jpeg",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """
    Generate completion using OpenRouter OpenAI-compatible API.
    Supports multimodal inputs with base64 data URLs for models like google/gemma-3-12b-it.

    Args:
        prompt: Text prompt / instruction.
        image_bytes: Optional raw image bytes for vision input.
        mime_type: Image MIME type (e.g. image/jpeg, image/png).
        temperature: Sampling temperature (default 0.1 for deterministic extraction).
        max_tokens: Maximum output tokens.
        model: Optional model override (defaults to settings.openrouter_model).

    Returns:
        Raw text response from the model.
    """
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise AIServiceError(
            "OpenRouter API key not configured.",
            detail="Set OPENROUTER_API_KEY in your .env file.",
        )

    target_model = model or settings.openrouter_model
    client = get_openrouter_http_client()

    content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt}
    ]

    if image_bytes:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url}
        })

    payload = {
        "model": target_model,
        "messages": [
            {"role": "user", "content": content}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.perf_counter()
    try:
        response = await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        raise AIServiceError(
            f"OpenRouter network request failed for model {target_model}.",
            detail=str(exc),
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    if response.status_code != 200:
        error_detail = response.text
        try:
            err_json = response.json()
            if "error" in err_json:
                error_detail = err_json["error"].get("message", error_detail)
        except Exception:
            pass
        raise AIServiceError(
            f"OpenRouter API call failed ({response.status_code}): {error_detail}",
            detail=f"Model: {target_model}, HTTP {response.status_code}",
        )

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise AIServiceError(
            f"OpenRouter returned empty choices for model {target_model}.",
            detail=response.text[:300],
        )

    raw_text = choices[0].get("message", {}).get("content", "")
    log.info(
        "openrouter_completion_success",
        model=target_model,
        latency_ms=round(latency_ms, 1),
        text_length=len(raw_text),
    )
    return raw_text


async def generate_openrouter_embedding(
    text: str,
    dimensions: int = 768,
    model: str | None = None,
) -> list[float]:
    """
    Generate vector embeddings using OpenRouter's /embeddings endpoint.
    """
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise AIServiceError(
            "OpenRouter API key not configured.",
            detail="Set OPENROUTER_API_KEY in your .env file.",
        )

    target_model = model or settings.openrouter_embedding_model
    client = get_openrouter_http_client()
    url = f"{settings.openrouter_base_url.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": target_model,
        "input": text,
        "dimensions": dimensions,
    }

    t0 = time.perf_counter()
    try:
        response = await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        raise AIServiceError(
            f"OpenRouter embedding request failed for model {target_model}.",
            detail=str(exc),
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    if response.status_code != 200:
        raise AIServiceError(
            f"OpenRouter embedding error ({response.status_code}): {response.text}",
            detail=f"Model: {target_model}",
        )

    data = response.json()
    emb_data = data.get("data", [])
    if not emb_data or "embedding" not in emb_data[0]:
        raise AIServiceError("OpenRouter returned empty embedding data.")

    vec = emb_data[0]["embedding"]
    log.debug("openrouter_embedding_generated", model=target_model, latency_ms=round(latency_ms, 1))
    return vec
