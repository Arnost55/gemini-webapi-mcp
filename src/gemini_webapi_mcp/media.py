"""Validated, account-scoped media artifacts for MCP tool results."""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from mcp.types import Annotations, ImageContent
from PIL import Image as PillowImage


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    EXPIRED = "expired"


class AccessPolicy(StrEnum):
    ACCOUNT_PRIVATE = "account_private"


_IMAGE_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_IMAGE_EXTENSION = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_VIDEO_EXTENSION = {"video/mp4": ".mp4", "video/webm": ".webm"}


@dataclass(frozen=True, slots=True)
class MediaArtifact:
    """A private generated artifact whose public view omits its storage path."""

    artifact_id: str
    media_type: MediaType
    mime_type: str
    filename: str
    size: int
    storage_path: Path
    account_id: str
    conversation_reference: tuple[str, ...] = ()
    status: ArtifactStatus = ArtifactStatus.COMPLETE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    access_policy: AccessPolicy = AccessPolicy.ACCOUNT_PRIVATE
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None

    def require_account(self, account_id: str) -> None:
        if not account_id or account_id != self.account_id:
            raise PermissionError("media artifact does not belong to this account")

    def public_metadata(self) -> dict:
        value = {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type.value,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "size": self.size,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "access_policy": self.access_policy.value,
        }
        if self.width is not None:
            value["width"] = self.width
        if self.height is not None:
            value["height"] = self.height
        if self.duration_seconds is not None:
            value["duration_seconds"] = self.duration_seconds
        if self.expires_at is not None:
            value["expires_at"] = self.expires_at.isoformat()
        return value

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.status is ArtifactStatus.EXPIRED:
            return True
        instant = now or datetime.now(timezone.utc)
        return self.expires_at is not None and instant >= self.expires_at

    def iter_bytes(self, account_id: str, *, chunk_size: int = 1024 * 1024):
        """Stream a private artifact after ownership, status, and size checks."""
        self.require_account(account_id)
        if self.status is not ArtifactStatus.COMPLETE or self.is_expired():
            raise ValueError("media artifact is not available")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        limit = _image_limit() if self.media_type is MediaType.IMAGE else _video_limit()
        if self.storage_path.stat().st_size != self.size or self.size > limit:
            raise ValueError("media artifact changed after validation")
        with self.storage_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def as_image_content(self, account_id: str) -> ImageContent:
        self.require_account(account_id)
        if (
            self.media_type is not MediaType.IMAGE
            or self.status is not ArtifactStatus.COMPLETE
            or self.is_expired()
        ):
            raise ValueError("artifact is not a complete image")
        raw = b"".join(self.iter_bytes(account_id))
        return ImageContent(
            type="image",
            data=base64.b64encode(raw).decode("ascii"),
            mimeType=self.mime_type,
            annotations=Annotations(audience=["user"], priority=1.0),
            _meta={"artifact_id": self.artifact_id},
        )


def _image_limit() -> int:
    return int(os.environ.get("GEMINI_MAX_IMAGE_ARTIFACT_BYTES", str(25 * 1024 * 1024)))


def _video_limit() -> int:
    return int(os.environ.get("GEMINI_MAX_VIDEO_ARTIFACT_BYTES", str(250 * 1024 * 1024)))


def _bounded_read(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("generated artifact is empty")
    if size > limit:
        raise ValueError("generated artifact exceeds the configured size limit")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("generated artifact exceeds the configured size limit")
    return data


def _validate_image(path: Path) -> tuple[str, int, int]:
    _bounded_read(path, _image_limit())
    try:
        with PillowImage.open(path) as image:
            image.verify()
        with PillowImage.open(path) as image:
            mime = _IMAGE_MIME.get((image.format or "").upper())
            width, height = image.size
    except Exception as exc:
        raise ValueError("generated image artifact is malformed") from exc
    if mime is None or width <= 0 or height <= 0:
        raise ValueError("generated image artifact has an unsupported format")
    return mime, width, height


def _validate_video(path: Path) -> str:
    data = _bounded_read(path, _video_limit())[:64]
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    raise ValueError("generated video artifact is malformed or unsupported")


def create_media_artifact(
    path: str | Path,
    *,
    account_id: str,
    conversation_reference: list[str] | tuple[str, ...] | None = None,
    media_type: MediaType | str | None = None,
    duration_seconds: float | None = None,
    media_root: str | Path | None = None,
) -> MediaArtifact:
    """Validate a completed artifact and bind it to one authenticated account."""
    if not account_id:
        raise ValueError("account_id is required for a private media artifact")
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("generated artifact is not a regular file")
    if media_root is not None:
        root = Path(media_root).expanduser().resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("generated artifact is outside the account media directory") from exc

    requested = MediaType(media_type) if media_type is not None else None
    if requested is None:
        suffix = resolved.suffix.lower()
        if suffix in set(_IMAGE_EXTENSION.values()) | {".jpeg"}:
            requested = MediaType.IMAGE
        elif suffix in set(_VIDEO_EXTENSION.values()):
            requested = MediaType.VIDEO
    if requested is MediaType.VIDEO:
        mime = _validate_video(resolved)
        width = height = None
    else:
        try:
            mime, width, height = _validate_image(resolved)
            requested = MediaType.IMAGE
        except ValueError:
            if requested is MediaType.IMAGE:
                raise
            mime = _validate_video(resolved)
            requested = MediaType.VIDEO
            width = height = None

    expected_suffix = (_IMAGE_EXTENSION if requested is MediaType.IMAGE else _VIDEO_EXTENSION)[mime]
    filename = resolved.stem + expected_suffix
    return MediaArtifact(
        artifact_id=str(uuid.uuid4()),
        media_type=requested,
        mime_type=mime,
        filename=filename,
        size=resolved.stat().st_size,
        storage_path=resolved,
        account_id=account_id,
        conversation_reference=tuple(conversation_reference or ()),
        created_at=datetime.now(timezone.utc),
        width=width,
        height=height,
        duration_seconds=duration_seconds,
    )
