import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from gemini_webapi_mcp.media import (
    ArtifactStatus,
    MediaType,
    create_media_artifact,
)


@pytest.mark.parametrize(
    ("image_format", "suffix", "mime_type"),
    [("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"), ("WEBP", ".webp", "image/webp")],
)
def test_image_artifact_is_validated_and_emitted_as_native_mcp_content(
    tmp_path, image_format, suffix, mime_type
):
    path = tmp_path / f"wide{suffix}"
    Image.new("RGB", (17, 9), (12, 34, 56)).save(path, format=image_format)

    artifact = create_media_artifact(
        path,
        account_id="subject/account-a",
        conversation_reference=["cid", "rid", "rcid"],
        media_type=MediaType.IMAGE,
        media_root=tmp_path,
    )
    content = artifact.as_image_content("subject/account-a")

    assert content.type == "image"
    assert content.mimeType == mime_type
    assert base64.b64decode(content.data) == path.read_bytes()
    assert artifact.width == 17
    assert artifact.height == 9
    assert artifact.conversation_reference == ("cid", "rid", "rcid")
    assert "storage_path" not in artifact.public_metadata()
    assert "account_id" not in artifact.public_metadata()


def test_multiple_images_keep_independent_native_content(tmp_path):
    paths = []
    for index, size in enumerate(((8, 4), (4, 8))):
        path = tmp_path / f"image-{index}.png"
        Image.new("RGB", size).save(path)
        paths.append(path)
    artifacts = [
        create_media_artifact(path, account_id="owner", media_root=tmp_path)
        for path in paths
    ]

    assert [item.as_image_content("owner").mimeType for item in artifacts] == ["image/png", "image/png"]
    assert [(item.width, item.height) for item in artifacts] == [(8, 4), (4, 8)]


def test_artifact_rejects_malformed_out_of_scope_and_cross_account_access(tmp_path):
    malformed = tmp_path / "bad.png"
    malformed.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="malformed"):
        create_media_artifact(malformed, account_id="owner", media_type="image", media_root=tmp_path)

    outside = tmp_path.parent / "outside-phase52.png"
    Image.new("RGB", (1, 1)).save(outside)
    try:
        with pytest.raises(ValueError, match="outside"):
            create_media_artifact(outside, account_id="owner", media_root=tmp_path)
    finally:
        outside.unlink(missing_ok=True)

    valid = tmp_path / "valid.png"
    Image.new("RGB", (2, 2)).save(valid)
    artifact = create_media_artifact(valid, account_id="owner", media_root=tmp_path)
    with pytest.raises(PermissionError):
        artifact.as_image_content("different-owner")


def test_image_size_limit_and_post_validation_changes_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(path)
    monkeypatch.setenv("GEMINI_MAX_IMAGE_ARTIFACT_BYTES", "4")
    with pytest.raises(ValueError, match="size limit"):
        create_media_artifact(path, account_id="owner", media_root=tmp_path)

    monkeypatch.setenv("GEMINI_MAX_IMAGE_ARTIFACT_BYTES", "10000")
    artifact = create_media_artifact(path, account_id="owner", media_root=tmp_path)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed after validation"):
        list(artifact.iter_bytes("owner"))


@pytest.mark.parametrize(
    ("name", "payload", "mime_type"),
    [("clip.mp4", b"\x00\x00\x00\x18ftypisom", "video/mp4"), ("clip.webm", b"\x1aE\xdf\xa3test", "video/webm")],
)
def test_video_artifact_validation_and_bounded_streaming(tmp_path, name, payload, mime_type):
    path = tmp_path / name
    path.write_bytes(payload)
    artifact = create_media_artifact(
        path,
        account_id="owner",
        media_type=MediaType.VIDEO,
        duration_seconds=10.0,
        media_root=tmp_path,
    )

    assert artifact.mime_type == mime_type
    assert artifact.duration_seconds == 10.0
    assert b"".join(artifact.iter_bytes("owner", chunk_size=3)) == payload


def test_expired_or_failed_artifacts_cannot_be_read(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(path)
    artifact = create_media_artifact(path, account_id="owner", media_root=tmp_path)
    expired = replace(artifact, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    failed = replace(artifact, status=ArtifactStatus.FAILED)

    with pytest.raises(ValueError, match="not available"):
        list(expired.iter_bytes("owner"))
    with pytest.raises(ValueError, match="not available"):
        list(failed.iter_bytes("owner"))
