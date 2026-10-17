from gemini_webapi_mcp.protocol.normalization import normalize_response_text


def test_extracts_validated_trailing_follow_up_block():
    result = normalize_response_text(
        'Final answer with [source](https://example.test).\n\n'
        '<FollowUp label="Explain more" query="Explain the result"/>\n'
        '<FollowUp query="Show an example"/>\n'
    )
    assert result.text == 'Final answer with [source](https://example.test).'
    assert [(item.label, item.query) for item in result.follow_ups] == [
        ("Explain more", "Explain the result"),
        (None, "Show an example"),
    ]


def test_preserves_inline_or_unseparated_follow_up_markup():
    inline = 'Use <FollowUp query="literal XML"/> in this example.'
    unseparated = 'Answer\n<FollowUp query="not a protocol suffix"/>\n'
    assert normalize_response_text(inline).text == inline
    assert normalize_response_text(unseparated).text == unseparated


def test_preserves_follow_up_markup_in_a_fenced_code_block():
    text = 'Example:\n```xml\n<FollowUp query="literal XML"/>\n```\n'
    result = normalize_response_text(text)
    assert result.text == text
    assert result.follow_ups == ()


def test_preserves_ordinary_thinking_language_and_citations():
    text = '**Thinking:** is a legitimate heading in this requested template.\n\nSee [1](https://example.test).'
    assert normalize_response_text(text).text == text


def test_extracts_trailing_image_and_follow_up_protocol_without_exposing_markup():
    result = normalize_response_text(
        'Here is the requested image.\n\n'
        '<Image src="internal-artifact"/>\n'
        '<FollowUp query="Make it brighter"/>\n'
    )
    assert result.text == "Here is the requested image."
    assert result.image_markers == 1
    assert result.follow_ups[0].query == "Make it brighter"
    assert "<Image" not in result.text


def test_extracts_supported_paired_follow_up_protocol():
    result = normalize_response_text('Answer.\n\n<FollowUp>Make another version</FollowUp>\n')
    assert result.text == "Answer."
    assert result.follow_ups[0].query == "Make another version"


def test_preserves_literal_image_markup_when_not_a_separated_protocol_suffix():
    inline = 'The literal example is <Image src="sample"/>.'
    fenced = '```xml\n<Image src="sample"/>\n```\n'
    assert normalize_response_text(inline).text == inline
    assert normalize_response_text(fenced).text == fenced
