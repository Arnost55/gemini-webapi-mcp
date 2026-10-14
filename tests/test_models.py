import json
import asyncio

import pytest

from gemini_webapi_mcp.models import (
    FALLBACKS,
    ModelCatalogCache,
    ModelSelectionError,
    capabilities_json,
    current_request_fields,
    discover_authenticated_models,
    discovery_metadata,
    discover_models,
    model_request_context,
    parse_authenticated_models,
    resolve_model,
    selector_evidence_sha256,
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
    model_header = '[1,null,"captured"]'
    fingerprint = selector_evidence_sha256(
        ["low", "medium", "high"],
        {level: {"x-goog-ext-525001261-jspb": model_header}
         for level in ("low", "medium", "high")},
        {},
    )
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash",
        "upstream_model": "gemini-3.0-flash", "thinking_levels": ["low", "medium", "high"],
        "default_thinking_level": "medium",
        "thinking_request_headers": {
            level: {"x-goog-ext-525001261-jspb": model_header}
            for level in ("low", "medium", "high")
        },
        "protocol_version": "gemini-web-streamgenerate-v1",
        "evidence_sha256": fingerprint,
    }]}))
    values = discover_models()
    selection = resolve_model(values, "gemini-3.6-flash", "high")
    assert selection.thinking_level == "high"
    assert dict(selection.request_headers) == {
        "x-goog-ext-525001261-jspb": model_header,
    }


def test_versioned_capture_can_configure_request_local_selector_fields(monkeypatch):
    fingerprint = selector_evidence_sha256(
        ["low", "high"], {}, {"low": {17: [[1]], 80: 1}, "high": {17: [[3]], 80: 3}},
    )
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "modern", "upstream_model": {"model_name": "modern", "model_header": {}},
        "thinking_levels": ["low", "high"],
        "request_fields": {"79": 3},
        "thinking_request_fields": {"low": {"17": [[1]], "80": 1}, "high": {"17": [[3]], "80": 3}},
        "protocol_version": "gemini-web-streamgenerate-v1",
        "evidence_sha256": fingerprint,
    }]}))
    selection = resolve_model(discover_models(), "modern", "high")
    assert dict(selection.request_fields) == {17: [[3]], 79: 3, 80: 3}


def test_unfingerprinted_private_protocol_fields_are_ignored(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "unsafe", "upstream_model": "gemini-3.0-flash",
        "thinking_levels": ["high"], "request_fields": {"79": 3},
        "thinking_request_fields": {"high": {"80": 3}},
    }]}))
    with pytest.raises(ModelSelectionError, match="no verified") as error:
        resolve_model(discover_models(), "unsafe", "high")
    assert error.value.code == "model_route_unverified"


def test_tampered_capture_fingerprint_fails_closed(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "tampered", "upstream_model": "gemini-3.0-pro",
        "thinking_levels": ["high"],
        "thinking_request_fields": {"high": {"80": 3}},
        "protocol_version": "gemini-web-streamgenerate-v1",
        "evidence_sha256": "a" * 64,
    }]}))
    with pytest.raises(ModelSelectionError) as error:
        resolve_model(discover_models(), "tampered", "high")
    assert error.value.code == "model_route_unverified"


def test_catalog_cannot_inject_arbitrary_http_headers(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL_CATALOG_JSON", json.dumps({"models": [{
        "id": "legacy", "upstream_model": "gemini-3.0-flash",
        "request_headers": {"Authorization": "secret", "Cookie": "secret"},
    }]}))
    selection = resolve_model(discover_models(), "legacy")
    assert selection.request_headers == ()


def test_protocol_selector_fields_are_task_local():
    from gemini_webapi_mcp.models import ModelSelection

    selection = ModelSelection("modern", "modern", "high", request_fields=((79, 3), (80, 3)))

    async def verify():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def selected():
            with model_request_context(selection):
                entered.set()
                await release.wait()
                return dict(current_request_fields())

        async def unrelated():
            await entered.wait()
            observed = dict(current_request_fields())
            release.set()
            return observed

        return await asyncio.gather(selected(), unrelated())

    assert asyncio.run(verify()) == [{79: 3, 80: 3}, {}]


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


def _get_user_status_frame():
    body = [None] * 18
    body[15] = [
        ["flash-id", "Flash", None, None, None, None, None, None, None, 1, None, "Gemini Flash"],
        ["lite-id", "Flash Lite", None, None, None, None, None, None, None, 6, None, "Gemini Flash Lite"],
        ["pro-id", "Pro", None, None, None, None, None, None, None, 3, None, "Gemini Pro"],
    ]
    body[16] = [8]
    body[17] = [19]
    return [["wrb.fr", "otAQ7b", json.dumps(body)]]


def test_authenticated_model_catalog_builds_account_specific_routes():
    models = parse_authenticated_models(_get_user_status_frame())
    assert [item.id for item in models] == ["gemini-flash", "gemini-flash-lite", "gemini-pro"]
    assert all(item.source == "authenticated_rpc" for item in models)
    assert all(item.confidence == "verified" for item in models)
    selection = resolve_model(models, "gemini-pro")
    assert selection.upstream_model == "unspecified"
    header = json.loads(dict(selection.request_headers)["x-goog-ext-525001261-jspb"])
    assert header[4] == "pro-id"
    assert header[8] == [4, 5, 6, 8]
    assert header[11] == 2
    assert header[14] == 3
    legacy_flash = resolve_model(models, "gemini-3.0-flash")
    assert legacy_flash.id == "gemini-flash"
    assert json.loads(dict(legacy_flash.request_headers)["x-goog-ext-525001261-jspb"])[4] == "flash-id"


def test_authenticated_discovery_uses_existing_client_session():
    class Response:
        text = "ignored by patched parser"

    class Client:
        async def _batch_execute(self, payloads):
            assert payloads[0].rpcid == "otAQ7b"
            return Response()

    import gemini_webapi_mcp.models as models_module
    original = models_module.parse_authenticated_models
    models_module.parse_authenticated_models = lambda _payload: parse_authenticated_models(_get_user_status_frame())
    try:
        discovered = asyncio.run(discover_authenticated_models(Client()))
    finally:
        models_module.parse_authenticated_models = original
    assert [item.id for item in discovered] == ["gemini-flash", "gemini-flash-lite", "gemini-pro"]


def test_authenticated_catalog_does_not_invent_independent_thinking_levels():
    models = parse_authenticated_models(_get_user_status_frame())
    with pytest.raises(ModelSelectionError) as error:
        resolve_model(models, "gemini-flash", "high")
    assert error.value.code == "thinking_not_supported"
