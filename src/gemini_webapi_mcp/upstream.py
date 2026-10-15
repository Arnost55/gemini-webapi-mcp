"""Compatibility boundary for the unsupported Gemini web client.

The dependency implements an undocumented protocol and has no stable model
discovery surface. Keep all assumptions detectable here instead of scattering
version guesses through MCP tools.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, distribution, version
import inspect
import json
from pathlib import Path
from typing import Any


AUDITED_COMMIT = "4402e5231760a1e85e28eb6cd0040ff302b764de"
AUDITED_VERSION = "1.19.2.post21"
LEGACY_ROUTE_NAMES = (
    "gemini-3.0-flash",
    "gemini-3.0-pro",
    "gemini-3.0-flash-thinking",
)


@dataclass(frozen=True)
class UpstreamCompatibility:
    package: str
    installed_version: str
    audited_version: str
    audited_commit: str
    compatible: bool
    stream_parser_utf16: bool
    streaming_api: bool
    authenticated_model_discovery: bool
    independent_thinking_control: bool
    module_origin: str | None
    source_revision: str | None
    legacy_routes: tuple[str, ...]
    notes: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["legacy_routes"] = list(self.legacy_routes)
        value["notes"] = list(self.notes)
        return value


def inspect_upstream() -> UpstreamCompatibility:
    """Inspect only local package surfaces; never probe an account or network."""
    try:
        installed = version("gemini-webapi")
    except PackageNotFoundError:
        installed = "missing"

    utf16 = streaming = False
    module_origin = source_revision = None
    routes: list[str] = []
    notes: list[str] = []
    try:
        from gemini_webapi import GeminiClient
        import gemini_webapi
        from gemini_webapi.constants import Model
        from gemini_webapi.utils import parse_response_by_frame

        streaming = callable(getattr(GeminiClient, "generate_content_stream", None))
        utf16 = "utf-16" in inspect.getsource(parse_response_by_frame).lower()
        routes = [item.model_name for item in Model if item.model_name != "unspecified"]
        module_origin = str(Path(gemini_webapi.__file__).resolve())
    except (ImportError, AttributeError, TypeError) as exc:
        notes.append(f"local_surface_error:{type(exc).__name__}")

    compatible = installed == AUDITED_VERSION and utf16 and streaming
    if installed != AUDITED_VERSION:
        notes.append("installed_version_differs_from_audited_build")
    if set(routes) != set(LEGACY_ROUTE_NAMES):
        notes.append("legacy_route_set_changed")
    try:
        direct_url = distribution("gemini-webapi").read_text("direct_url.json")
        if direct_url:
            vcs_info = json.loads(direct_url).get("vcs_info", {})
            revision = vcs_info.get("commit_id")
            if isinstance(revision, str) and revision:
                source_revision = revision
    except (PackageNotFoundError, TypeError, ValueError, json.JSONDecodeError):
        notes.append("installed_source_revision_unavailable")
    notes.append("web_protocol_is_undocumented_and_account_entitlements_are_not_enumerable")
    return UpstreamCompatibility(
        package="xob0t/Gemini-API",
        installed_version=installed,
        audited_version=AUDITED_VERSION,
        audited_commit=AUDITED_COMMIT,
        compatible=compatible,
        stream_parser_utf16=utf16,
        streaming_api=streaming,
        authenticated_model_discovery=False,
        independent_thinking_control=False,
        module_origin=module_origin,
        source_revision=source_revision,
        legacy_routes=tuple(routes),
        notes=tuple(notes),
    )


def require_compatible_upstream() -> None:
    status = inspect_upstream()
    if not status.compatible:
        raise RuntimeError(
            "upstream_client_incompatible: installed gemini-webapi does not "
            f"match audited {status.audited_version} ({status.audited_commit[:12]})"
        )
