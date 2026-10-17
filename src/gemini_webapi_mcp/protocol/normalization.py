"""Conservative normalization of user-visible Gemini response text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

_PROTOCOL_LINE = re.compile(
    r"^[ \t]*(<(?:FollowUp|Image)\b[^\r\n]*?(?:/>|>[^<\r\n]*</(?:FollowUp|Image)>))[ \t]*$"
)
_IMAGE_PROTOCOL_SRC = re.compile(
    r"^(?:https?://googleusercontent\.com/)?image_generation_content/\d+$"
)


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


def normalize_response_text(text: str) -> NormalizedResponse:
    """Extract only an unambiguous trailing block of ``FollowUp`` elements.

    Gemini sometimes appends self-closing protocol elements to an otherwise
    normal answer.  Treating arbitrary XML-looking text as metadata would
    corrupt code samples and quoted content, so extraction is deliberately
    limited to a contiguous suffix of standalone lines after a blank line.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return NormalizedResponse(text=text)

    start = len(lines)
    parsed: list[FollowUp] = []
    image_markers = 0
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].rstrip("\r\n")
        match = _PROTOCOL_LINE.fullmatch(line)
        if not match:
            break
        try:
            element = ElementTree.fromstring(match.group(1))
        except ElementTree.ParseError:
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
        elif element.tag == "Image":
            if (
                (element.text or "").strip()
                or set(element.attrib) - {"alt", "src"}
                or not _IMAGE_PROTOCOL_SRC.fullmatch(element.attrib.get("src", ""))
            ):
                break
            # Strip only known Gemini protocol references; preserve arbitrary
            # XML-looking user content and unknown upstream markup.
            image_markers += 1
        else:
            break
        start = index

    if (not parsed and not image_markers) or start == 0:
        return NormalizedResponse(text=text)

    prefix = "".join(lines[:start])
    if not prefix.endswith(("\n\n", "\r\n\r\n")):
        return NormalizedResponse(text=text)

    return NormalizedResponse(
        text=prefix.rstrip(),
        follow_ups=tuple(reversed(parsed)),
        image_markers=image_markers,
    )
