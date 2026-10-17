import json

from gemini_webapi_mcp import build_info


class _EmbeddedBuild:
    def __init__(self, payload):
        self.payload = payload

    def joinpath(self, _name):
        return self

    def read_text(self, *, encoding):
        assert encoding == "utf-8"
        return json.dumps(self.payload)


def _clear_runtime(monkeypatch):
    for name in ("GEMINI_BUILD_COMMIT", "GITHUB_SHA", "GEMINI_BUILD_TIMESTAMP", "GEMINI_BUILD_VERSION"):
        monkeypatch.delenv(name, raising=False)


def test_baked_build_metadata_is_used_without_runtime_environment(monkeypatch):
    _clear_runtime(monkeypatch)
    monkeypatch.setattr(build_info, "files", lambda _package: _EmbeddedBuild({
        "commit": "abc123", "timestamp": "2026-10-17T12:00:00Z", "version": "1.0.0"
    }))

    assert build_info.build_provenance() == {
        "commit": "abc123",
        "timestamp": "2026-10-17T12:00:00Z",
        "version": "1.0.0",
        "source": "image_metadata",
    }


def test_runtime_commit_precedence_and_unknown_values(monkeypatch):
    _clear_runtime(monkeypatch)
    monkeypatch.setattr(build_info, "files", lambda _package: _EmbeddedBuild({"commit": "baked"}))
    monkeypatch.setenv("GITHUB_SHA", "workflow-sha")
    assert build_info.build_provenance()["source"] == "GITHUB_SHA"
    assert build_info.build_provenance()["commit"] == "workflow-sha"

    monkeypatch.setenv("GEMINI_BUILD_COMMIT", "runtime-sha")
    assert build_info.build_provenance()["source"] == "GEMINI_BUILD_COMMIT"
    assert build_info.build_provenance()["commit"] == "runtime-sha"


def test_missing_build_metadata_is_explicit(monkeypatch):
    _clear_runtime(monkeypatch)

    class Missing(_EmbeddedBuild):
        def read_text(self, *, encoding):
            raise FileNotFoundError

    monkeypatch.setattr(build_info, "files", lambda _package: Missing({}))
    assert build_info.build_provenance() == {
        "commit": "unknown", "timestamp": "unknown", "version": "unknown", "source": "unavailable"
    }
