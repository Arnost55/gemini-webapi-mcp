from gemini_webapi_mcp.models import FALLBACKS, capabilities_json, discover_models


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
