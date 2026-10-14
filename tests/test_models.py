import json

import pytest

from gemini_webapi_mcp.models import (
    FALLBACKS,
    ModelCatalogCache,
    ModelSelectionError,
    capabilities_json,
    discovery_metadata,
    discover_models,
    resolve_model,
)


def test_fallback_models_are_stable_without_client(monkeypatch):
    monkeypatch.delenv("GEMINI_MODELS", raising=False)
    assert discover_models(None) == list(FALLBACKS)


def test_configured_models_are_deduplicated(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", "alpha, beta,alpha")
    assert [x["id"] for x in capabilities_json()] == ["alpha", "beta"]


def test_observed_models_take_precedence():
    class Client:
        models = [{"name": "rotated-model"}, {"id": "rotated-model"}]

    found = discover_models(Client())
    assert [x.id for x in found] == ["rotated-model"]


def test_static_legacy_model_has_a_safe_route_but_no_invented_thinking():
    selection = resolve_model(FALLBACKS, "gemini-3.0-flash")
    assert selection.upstream_model == "gemini-3.0-flash"
    with pytest.raises(ModelSelectionError, match="verified thinking") as error:
        resolve_model(FALLBACKS, "gemini-3.0-flash", "high")
    assert error.value.code == "thinking_not_supported"


def test_observed_ui_model_is_not_routable_without_evidence():
    class Client:
        available_models = [{"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash",
                             "thinking_levels": ["low", "medium", "high"]}]

    values = discover_models(Client())
    assert values[0].thinking_levels == ("low", "medium", "high")
    with pytest.raises(ModelSelectionError, match="no verified") as error:
        resolve_model(values, "gemini-3.6-flash", "high")
    assert error.value.code == "model_route_unverified"


def test_configured_model_can_declare_an_evidence_backed_route(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash",
        "upstream_model": "gemini-3.0-flash", "thinking_levels": ["low", "medium", "high"],
        "default_thinking_level": "medium", "request_headers": {"x-test-route": "captured"},
        "thinking_request_headers": {"high": {"x-test-thinking": "high-captured"}},
    }]}))
    values = discover_models()
    selection = resolve_model(values, "gemini-3.6-flash", "high")
    assert selection.thinking_level == "high"
    assert dict(selection.request_headers) == {
        "x-test-route": "captured", "x-test-thinking": "high-captured",
    }


def test_catalog_cache_is_account_scoped_and_invalidatable():
    cache = ModelCatalogCache(ttl_seconds=3600)
    one, timestamp = cache.get(("subject", "account-a"), None)
    two, other_timestamp = cache.get(("subject", "account-b"), None)
    assert one == two == list(FALLBACKS)
    assert timestamp <= other_timestamp
    cache.invalidate(("subject", "account-a"))
    assert ("subject", "account-a") not in cache._values
    assert ("subject", "account-b") in cache._values


def test_discovery_metadata_does_not_misrepresent_static_routes_as_account_discovery():
    class Client:
        pass

    metadata = discovery_metadata(Client())
    assert metadata["mode"] == "legacy_route_fallback"
    assert metadata["authenticated_account_capability_endpoint"] is False
    assert metadata["thinking_level_protocol_verified"] is False
    assert metadata["client_attributes_with_values"] == []


def test_discovery_metadata_marks_observed_client_attribute_without_claiming_route_verification():
    class Client:
        available_models = [{"id": "observed"}]

    metadata = discovery_metadata(Client())
    assert metadata["mode"] == "client_attribute_observation"
    assert metadata["client_attributes_with_values"] == ["available_models"]
    assert metadata["authenticated_account_capability_endpoint"] is False
