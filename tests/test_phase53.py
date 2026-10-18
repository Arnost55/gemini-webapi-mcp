import asyncio
import base64

import pytest

from gemini_webapi_mcp.image_viewer import (
    IMAGE_VIEWER_HTML,
    IMAGE_VIEWER_URI,
    IMAGE_VIEWER_V1_URI,
    LEGACY_IMAGE_VIEWER_URI,
    image_viewer_resource_meta,
)
from gemini_webapi_mcp.media import MediaType, create_media_artifact
from gemini_webapi_mcp.server import mcp
from gemini_webapi_mcp.studio_capabilities import (
    gemini_video_capabilities,
    notebooklm_capabilities,
)
from gemini_webapi_mcp.video_viewer import (
    VIDEO_VIEWER_HTML,
    VIDEO_VIEWER_URI,
    video_viewer_resource_meta,
)


def test_image_viewer_has_responsive_gallery_and_real_host_actions():
    required = (
        "width:auto;height:auto;max-width:100%;object-fit:contain",
        "object-fit:contain",
        "ResizeObserver",
        "notifyIntrinsicHeight",
        "orientationchange",
        'requestDisplayMode({mode:"fullscreen"})',
        'callTool("gemini_generate_image"',
        "metadata.conversation_id",
        "Image gallery",
    )
    for contract in required:
        assert contract in IMAGE_VIEWER_HTML
    assert "http://" not in IMAGE_VIEWER_HTML
    assert "https://" not in IMAGE_VIEWER_HTML


def test_image_viewer_download_validates_and_saves_original_image_bytes():
    for mime, extension in (
        ("image/png", ".png"),
        ("image/jpeg", ".jpg"),
        ("image/webp", ".webp"),
    ):
        assert f'"{mime}":"{extension}"' in IMAGE_VIEWER_HTML
    for check in (
        "allowedBase64.test(data)",
        "MAX_IMAGE_BYTES=25*1024*1024",
        "validSignature(bytes,mime)",
        'new Blob([bytes],{type:mime})',
        'new File([item.blob],item.filename,{type:item.mime})',
        "bridge.uploadFile(file)",
        "bridge.getFileDownloadUrl({fileId:uploaded.fileId})",
        "bridge.openExternal({href:response.downloadUrl,redirectUrl:false})",
        'link.href=item.src;link.download=item.filename',
        "Could not download this image.",
    ):
        assert check in IMAGE_VIEWER_HTML
    assert "clipboard" not in IMAGE_VIEWER_HTML.lower()
    assert "URL.createObjectURL" not in IMAGE_VIEWER_HTML
    assert "data:${mime};base64,${data}" in IMAGE_VIEWER_HTML
    assert "storage_path" not in IMAGE_VIEWER_HTML


def test_image_viewer_height_measurement_and_letterboxing_are_transparent():
    for check in (
        "#stage{position:relative",
        "background:transparent",
        "object-fit:contain",
        "app.getBoundingClientRect()",
        "rect.bottom+bottomPadding",
        "style.paddingBottom",
        "Math.abs(height-lastHeight)<2",
        "new ResizeObserver(scheduleHeight)",
        'image.addEventListener("load",()=>{',
        'window.addEventListener("orientationchange",scheduleHeight',
    ):
        assert check in IMAGE_VIEWER_HTML
    assert "document.documentElement.scrollHeight" not in IMAGE_VIEWER_HTML
    assert "width:fit-content;max-width:min(100%,1200px);min-height:0" in IMAGE_VIEWER_HTML
    assert "#stage.empty{min-height:clamp(180px,40vw,320px)}" in IMAGE_VIEWER_HTML
    assert "function fitInlineImage()" not in IMAGE_VIEWER_HTML
    assert "window.innerHeight-bodySpace" not in IMAGE_VIEWER_HTML
    assert ".controls{position:relative" in IMAGE_VIEWER_HTML
    assert "function fitExpandedImage()" in IMAGE_VIEWER_HTML
    assert "#stage.empty{width:100%}" in IMAGE_VIEWER_HTML
    assert IMAGE_VIEWER_HTML.count("background:transparent") >= 3


def test_image_widget_domain_is_opt_in_and_https_only(monkeypatch):
    monkeypatch.delenv("GEMINI_WIDGET_DOMAIN", raising=False)
    assert "domain" not in image_viewer_resource_meta()["ui"]
    monkeypatch.setenv("GEMINI_WIDGET_DOMAIN", "http://unsafe.example")
    assert "domain" not in image_viewer_resource_meta()["ui"]
    monkeypatch.setenv("GEMINI_WIDGET_DOMAIN", "https://widgets.example")
    meta = image_viewer_resource_meta()
    assert meta["ui"]["domain"] == "https://widgets.example"
    assert meta["openai/widgetDomain"] == "https://widgets.example"
    assert meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}


def test_image_and_video_resources_are_registered_with_compatibility_uri():
    async def inspect():
        resources = await mcp.list_resources()
        video = await mcp.read_resource(VIDEO_VIEWER_URI)
        return resources, video

    resources, video = asyncio.run(inspect())
    uris = {str(resource.uri) for resource in resources}
    assert {
        IMAGE_VIEWER_URI,
        LEGACY_IMAGE_VIEWER_URI,
        IMAGE_VIEWER_V1_URI,
        VIDEO_VIEWER_URI,
    } <= uris
    assert VIDEO_VIEWER_HTML in video[0].content


def test_video_viewer_uses_embedded_blob_and_native_controls():
    required = (
        "controls playsinline",
        "URL.createObjectURL",
        "URL.revokeObjectURL",
        "video\\/(?:mp4|webm)",
        "notifyIntrinsicHeight",
        "requestDisplayMode",
        "object-fit:contain",
    )
    for contract in required:
        assert contract in VIDEO_VIEWER_HTML
    assert "http://" not in VIDEO_VIEWER_HTML
    assert "https://" not in VIDEO_VIEWER_HTML
    assert video_viewer_resource_meta()["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
    }


def test_private_video_can_be_embedded_without_storage_path(tmp_path):
    path = tmp_path / "clip.mp4"
    payload = b"\x00\x00\x00\x18ftypisom" + b"private-video"
    path.write_bytes(payload)
    artifact = create_media_artifact(
        path, account_id="account-a", media_type=MediaType.VIDEO, media_root=tmp_path
    )
    resource = artifact.as_video_resource("account-a")
    assert resource.type == "resource"
    assert resource.resource.mimeType == "video/mp4"
    assert base64.b64decode(resource.resource.blob) == payload
    assert str(path) not in str(resource.model_dump())
    assert str(resource.resource.uri).startswith("gemini-artifact://")
    with pytest.raises(PermissionError):
        artifact.as_video_resource("account-b")


def test_embedded_video_size_limit_is_enforced(tmp_path, monkeypatch):
    path = tmp_path / "clip.webm"
    path.write_bytes(b"\x1aE\xdf\xa3payload")
    artifact = create_media_artifact(
        path, account_id="owner", media_type=MediaType.VIDEO, media_root=tmp_path
    )
    monkeypatch.setenv("GEMINI_MAX_EMBEDDED_VIDEO_BYTES", "4")
    with pytest.raises(ValueError, match="embedded delivery limit"):
        artifact.as_video_resource("owner")


def test_capability_reports_do_not_promote_browser_observations_to_mcp_support():
    video = gemini_video_capabilities()
    assert video["browser_ui"]["observed"] is True
    assert video["mcp"]["generation"] == "unsupported"
    assert video["mcp"]["cancellation"] == "unsupported"
    notebook = notebooklm_capabilities()
    assert notebook["authentication"]["independent_from_gemini_adapter"] is True
    assert notebook["consumer_web"]["grounded_chat"] == "unsupported"
    assert notebook["enterprise_api"]["configured"] is False


def test_phase53_capability_tools_are_registered():
    async def names():
        return {tool.name for tool in await mcp.list_tools()}

    registered = asyncio.run(names())
    assert "gemini_video_capabilities" in registered
    assert "notebooklm_capabilities" in registered
