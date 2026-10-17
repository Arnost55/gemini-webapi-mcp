"""Conservative normalization of user-visible Gemini response text."""

from __future__ import annotations

from dataclasses import dataclass
import re
from xml.etree import ElementTree


_FOLLOW_UP_LINE = re.compile(r"^[ \t]*(<FollowUp\b[^\r\n]*?/>)[ \t]*$")


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
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].rstrip("\r\n")
        match = _FOLLOW_UP_LINE.fullmatch(line)
        if not match:
            break
        try:
            element = ElementTree.fromstring(match.group(1))
        except ElementTree.ParseError:
            break
        if (
            element.tag != "FollowUp"
            or element.text not in (None, "")
            or len(element) != 0
            or set(element.attrib) - {"label", "query"}
            or not any(element.attrib.get(key, "").strip() for key in ("label", "query"))
        ):
            break
        parsed.append(FollowUp(
            label=element.attrib.get("label") or None,
            query=element.attrib.get("query") or None,
        ))
        start = index

    if not parsed or start == 0:
        return NormalizedResponse(text=text)

    prefix = "".join(lines[:start])
    if not prefix.endswith(("\n\n", "\r\n\r\n")):
        return NormalizedResponse(text=text)

    return NormalizedResponse(text=prefix.rstrip(), follow_ups=tuple(reversed(parsed)))
