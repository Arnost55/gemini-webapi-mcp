"""Verified capability registries for Gemini Studio and NotebookLM.

These registries intentionally separate observed product UI capabilities from
server-callable protocols.  A browser observation must never silently become
an MCP capability.
"""

from __future__ import annotations


def gemini_video_capabilities() -> dict:
    """Return the currently verified Gemini video integration boundary."""
    return {
        "product": "gemini_web",
        "feature": "video_generation",
        "browser_ui": {
            "observed": True,
            "playback_observed": True,
            "observed_container": "video/mp4",
            "observed_dimensions": {"width": 1280, "height": 720},
            "observed_duration_seconds": 10.005,
        },
        "mcp": {
            "generation": "unsupported",
            "reason": "authenticated generation RPC and durable job protocol are unverified",
            "artifact_validation": True,
            "account_private_artifacts": True,
            "embedded_video_viewer": True,
            "chatgpt_playback": "unverified",
            "cancellation": "unsupported",
        },
    }


def notebooklm_capabilities() -> dict:
    """Return consumer and enterprise NotebookLM capability boundaries."""
    return {
        "product": "notebooklm",
        "authentication": {
            "independent_from_gemini_adapter": True,
            "consumer_browser_access_observed": True,
            "server_session_protocol": "unverified",
        },
        "consumer_web": {
            "notebook_management": "unsupported",
            "source_upload": "unsupported",
            "grounded_chat": "unsupported",
            "studio_generation": "unsupported",
            "reason": "no verified authorized consumer API or recovered account-scoped web protocol",
        },
        "enterprise_api": {
            "documented": True,
            "configured": False,
            "reason": "requires a separately licensed Google Cloud project and IAM authorization",
        },
    }
