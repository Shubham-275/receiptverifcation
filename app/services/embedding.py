"""
Embedding service — generates text embeddings for semantic product search.

Uses Google's Gemini embedding model via the google-genai SDK.
Embeddings are used:
  - When creating new Product records (embed canonical_name)
  - During resolution cascade step 4 (embed raw receipt name, search pgvector)
  - When creating Receipt content embeddings for receipt search

Lazy client singleton shared with classifier/extractor.
"""
import time
from typing import Sequence

from google import genai
from google.genai import types as genai_types

from app.config import get_settings
from app.exceptions import AIServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

# Output dimension kept at 768 to match the pgvector columns in the schema.
EMBEDDING_DIM = 768

_client: genai.Client | None = None
_cached_gemini_key: str | None = None


def _get_client() -> genai.Client:
    global _client, _cached_gemini_key
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AIServiceError(
            "Gemini API key not configured.",
            detail="Set GEMINI_API_KEY in your .env file.",
        )
    if _client is None or _cached_gemini_key != settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
        _cached_gemini_key = settings.gemini_api_key
        log.info("embedding_client_initialized", model=settings.gemini_embedding_model)
    return _client


async def embed_text(text: str) -> list[float]:
    """
    Generate a 768-dim embedding for a single text string.

    Args:
        text: The text to embed (product name, receipt content, etc.)

    Returns:
        List of 768 floats.

    Raises:
        AIServiceError: If the embedding call fails.
    """
    settings = get_settings()

    if settings.openrouter_api_key and not settings.gemini_api_key:
        from app.services.openrouter_client import generate_openrouter_embedding

        return await generate_openrouter_embedding(text=text, dimensions=EMBEDDING_DIM)

    client = _get_client()
    t0 = time.perf_counter()

    try:
        response = client.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=text,
            config=genai_types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
        )
    except Exception as exc:
        err_str = str(exc)
        detail = err_str
        if "not allowed by policy" in err_str or "Forbidden" in err_str:
            detail = (
                "Google rejected the request (403 Policy Forbidden): The API key policy does not allow access "
                "to 'generativelanguage.googleapis.com'. Please use a free API key generated from Google AI Studio "
                "(https://aistudio.google.com/app/apikey) or enable 'Generative Language API' in Google Cloud Console > Credentials."
            )
        raise AIServiceError(
            "Gemini embedding call failed.",
            detail=detail,
        )

    latency_ms = (time.perf_counter() - t0) * 1000

    if not response.embeddings:
        raise AIServiceError("Gemini returned empty embedding response.")

    embedding = response.embeddings[0].values
    log.debug(
        "embedding_generated",
        text_preview=text[:50],
        dim=len(embedding),
        latency_ms=round(latency_ms, 1),
    )
    return list(embedding)


async def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """
    Generate embeddings for multiple texts (batched where possible).
    Falls back to sequential calls if batch fails.
    """
    settings = get_settings()

    if settings.openrouter_api_key and not settings.gemini_api_key:
        return [await embed_text(text) for text in texts]

    client = _get_client()

    try:
        response = client.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=list(texts),
            config=genai_types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
        )
        return [emb.values for emb in response.embeddings]
    except Exception:
        # Sequential fallback
        results = []
        for text in texts:
            results.append(await embed_text(text))
        return results
