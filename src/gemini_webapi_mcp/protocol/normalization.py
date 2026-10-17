"""Conservative normalization of user-visible Gemini response text."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from xml.etree import ElementTree

_PROTOCOL_LINE = re.compile(
    r"^[ \t]*(<(?:FollowUp|Image)\b[^\r\n]*?(?:/>|>[^<\r\n]*</(?:FollowUp|Image)>))[ \t]*$"
)
_IMAGE_PROTOCOL_SRC = re.compile(
    r"^(?:https?://(?:[a-z0-9-]+\.)*googleusercontent\.com/)?"
    r"image_generation_content/(?P<index>\d+)(?:[?#][^\s<>]*)?$",
    re.IGNORECASE,
)
_FENCE_LINE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")


@dataclass(frozen=True)
class FollowUp:
    """A validated follow-up suggestion from a trailing protocol block."""

    label: str | None = None
    query: str | None = None


@dataclass(frozen=True)
class NormalizedResponse:
    """Public answer plus safely separated response metadata."""

    text: str
    follow_ups: tuple[FollowUp, ...] = ()
    image_markers: int = 0


def _protocol_element(line: str):
    match = _PROTOCOL_LINE.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    try:
        return ElementTree.fromstring(match.group(1))
    except ElementTree.ParseError:
        return None


def normalize_response_text(
    text: str,
    *,
    extracted_image_count: int = 0,
    extracted_image_indices: Collection[int] | None = None,
) -> NormalizedResponse:
    """Remove confirmed image markers and extract trailing follow-up metadata.

    Gemini sometimes appends self-closing protocol elements to an otherwise
    normal answer.  Treating arbitrary XML-looking text as metadata would
    corrupt code samples and quoted content. Image markers are therefore
    removed only when their numeric reference maps to an image that was
    successfully extracted, and never inside fenced code. Follow-ups remain
    limited to a contiguous suffix of standalone lines after a blank line.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return NormalizedResponse(text=text)
    confirmed_indices = (
        set(range(extracted_image_count))
        if extracted_image_indices is None
        else set(extracted_image_indices)
    )

    filtered: list[str] = []
    image_markers = 0
    fence: str | None = None
    for line in lines:
        fence_match = _FENCE_LINE.match(line)
        if fence_match:
            marker = fence_match.group("fence")[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            filtered.append(line)
            continue
        element = _protocol_element(line) if fence is None else None
        src_match = None
        if element is not None and element.tag == "Image":
            src_match = _IMAGE_PROTOCOL_SRC.fullmatch(element.attrib.get("src", ""))
        if (
            element is not None
            and element.tag == "Image"
            and len(element) == 0
            and not (element.text or "").strip()
            and not (set(element.attrib) - {"alt", "src"})
            and src_match is not None
            and int(src_match.group("index")) in confirmed_indices
        ):
            image_markers += 1
            continue
        filtered.append(line)

    lines = filtered
    if not lines:
        return NormalizedResponse(text="", image_markers=image_markers)

    start = len(lines)
    parsed: list[FollowUp] = []
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].rstrip("\r\n")
        element = _protocol_element(line)
        if element is None:
            break
        if len(element) != 0:
            break
        if element.tag == "FollowUp":
            body = (element.text or "").strip()
            if (
                set(element.attrib) - {"label", "query"}
                or not (body or any(element.attrib.get(key, "").strip() for key in ("label", "query")))
            ):
                break
            parsed.append(FollowUp(
                label=element.attrib.get("label") or None,
                query=element.attrib.get("query") or body or None,
            ))
        else:
            break
        start = index

    if not parsed or start == 0:
        return NormalizedResponse(text="".join(lines), image_markers=image_markers)

    prefix = "".join(lines[:start])
    if not prefix.endswith(("\n\n", "\r\n\r\n")):
        return NormalizedResponse(text="".join(lines), image_markers=image_markers)

    return NormalizedResponse(
        text=prefix.rstrip(),
        follow_ups=tuple(reversed(parsed)),
        image_markers=image_markers,
    )
