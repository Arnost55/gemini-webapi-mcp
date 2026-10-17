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
