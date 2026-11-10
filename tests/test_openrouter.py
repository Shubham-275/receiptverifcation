"""
Unit tests for OpenRouter provider integration (google/gemma-3-12b-it).
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.exceptions import AIServiceError
from app.services.openrouter_client import (
    generate_chat_completion,
    generate_openrouter_embedding,
)


@pytest.mark.asyncio
async def test_generate_chat_completion_text_only():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {"message": {"content": '{"test": "ok"}'}}
        ]
    }

    with patch("app.services.openrouter_client.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.get_openrouter_http_client") as mock_client_factory:
        settings = MagicMock()
        settings.openrouter_api_key = "test-sk-key"
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.openrouter_model = "google/gemma-3-12b-it"
        mock_settings.return_value = settings

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_factory.return_value = mock_http

        result = await generate_chat_completion(prompt="Hello")
        assert result == '{"test": "ok"}'

        # Verify POST payload
        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["model"] == "google/gemma-3-12b-it"
        assert payload["messages"][0]["content"] == [{"type": "text", "text": "Hello"}]


@pytest.mark.asyncio
async def test_generate_chat_completion_multimodal():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {"message": {"content": '{"is_receipt": true}'}}
        ]
    }

    with patch("app.services.openrouter_client.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.get_openrouter_http_client") as mock_client_factory:
        settings = MagicMock()
        settings.openrouter_api_key = "test-sk-key"
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.openrouter_model = "google/gemma-3-12b-it"
        mock_settings.return_value = settings

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_factory.return_value = mock_http

        fake_img = b"fake-jpeg-data"
        result = await generate_chat_completion(
            prompt="Analyze receipt",
            image_bytes=fake_img,
            mime_type="image/jpeg",
        )
        assert result == '{"is_receipt": true}'

        payload = mock_http.post.call_args[1]["json"]
        content = payload["messages"][0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_generate_chat_completion_error_handling():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    mock_resp.json.return_value = {"error": {"message": "Invalid API key"}}

    with patch("app.services.openrouter_client.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.get_openrouter_http_client") as mock_client_factory:
        settings = MagicMock()
        settings.openrouter_api_key = "invalid-key"
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.openrouter_model = "google/gemma-3-12b-it"
        mock_settings.return_value = settings

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_factory.return_value = mock_http

        with pytest.raises(AIServiceError) as exc_info:
            await generate_chat_completion(prompt="Hello")
        assert "Invalid API key" in str(exc_info.value)


@pytest.mark.asyncio
async def test_generate_openrouter_embedding():
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"embedding": [0.1] * 768}
        ]
    }

    with patch("app.services.openrouter_client.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.get_openrouter_http_client") as mock_client_factory:
        settings = MagicMock()
        settings.openrouter_api_key = "test-sk-key"
        settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        settings.openrouter_embedding_model = "openai/text-embedding-3-small"
        mock_settings.return_value = settings

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_factory.return_value = mock_http

        emb = await generate_openrouter_embedding("Atta Flour", dimensions=768)
        assert len(emb) == 768
        assert emb[0] == 0.1


@pytest.mark.asyncio
async def test_classifier_routes_to_openrouter():
    from app.services.classifier import classify_receipt_image

    mock_resp_json = {
        "is_receipt": True,
        "confidence": 0.95,
        "document_type": "supplier_purchase_receipt",
        "reason": "Classified by gemma-3-12b-it",
    }

    with patch("app.services.classifier.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.generate_chat_completion", new_callable=AsyncMock) as mock_llm:
        settings = MagicMock()
        settings.get_effective_provider.return_value = "openrouter"
        settings.receipt_confidence_threshold = 0.75
        mock_settings.return_value = settings
        mock_llm.return_value = json.dumps(mock_resp_json)

        result = await classify_receipt_image(b"fake-bytes", "image/jpeg")
        assert result.is_receipt is True
        assert result.confidence == 0.95
        assert result.accepted is True
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_extractor_routes_to_openrouter():
    from app.services.extractor import extract_receipt

    mock_resp_json = {
        "supplier": "Sharma Traders",
        "date": "2026-09-29",
        "invoice_number": "ST-001",
        "receipt_total": 1850.0,
        "currency": "INR",
        "extraction_confidence": 0.95,
        "items": [
            {
                "raw_name": "Rice 25kg",
                "quantity": 25.0,
                "unit": "kg",
                "unit_price": 74.0,
                "line_total": 1850.0,
            }
        ],
    }

    with patch("app.services.extractor.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.generate_chat_completion", new_callable=AsyncMock) as mock_llm:
        settings = MagicMock()
        settings.get_effective_provider.return_value = "openrouter"
        mock_settings.return_value = settings
        mock_llm.return_value = json.dumps(mock_resp_json)

        result = await extract_receipt(b"fake-bytes", "image/jpeg")
        assert result.supplier == "Sharma Traders"
        assert result.receipt_total == 1850.0
        assert len(result.items) == 1
        assert result.items[0].raw_name == "Rice 25kg"
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_resolver_step5_routes_to_openrouter():
    from app.models.product import Product
    from app.services.resolver import _llm_disambiguate

    mock_prod = Product(canonical_name="Atta", category="grocery")
    mock_resp_json = {
        "best_match_name": "Atta",
        "confidence": 0.92,
        "reason": "Matches Atta flour directly",
    }

    with patch("app.services.resolver.get_settings") as mock_settings, \
         patch("app.services.openrouter_client.generate_chat_completion", new_callable=AsyncMock) as mock_llm:
        settings = MagicMock()
        settings.get_effective_provider.return_value = "openrouter"
        settings.openrouter_api_key = "test-sk-key"
        mock_settings.return_value = settings
        mock_llm.return_value = json.dumps(mock_resp_json)

        prod, conf, reason = await _llm_disambiguate(
            raw_name="Aashirvaad Atta",
            candidates=[mock_prod],
            supplier_name="Sharma Traders",
            unit="kg",
        )
        assert prod is not None
        assert prod.canonical_name == "Atta"
        assert conf == 0.92
        mock_llm.assert_called_once()

