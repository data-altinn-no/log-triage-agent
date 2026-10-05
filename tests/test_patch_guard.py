from agents.services.patch_guard import find_leaks, find_placeholders, review


def _diff(*added: str) -> str:
    return "--- a/X.cs\n+++ b/X.cs\n@@ -1 +1 @@\n" + "\n".join(f"+{a}" for a in added)


def test_structured_log_placeholder_for_the_body_is_a_leak():
    assert find_leaks(_diff('_logger.LogWarning("Status: {Status}. Body: {Body}", s, content);'))


def test_interpolated_body_in_an_exception_message_is_a_leak():
    assert find_leaks(_diff(
        'throw new EvidenceSourcePermanentServerException(Code, $"Bad response: {json}", ex);'
    ))


def test_a_log_statement_spanning_several_lines_is_checked_as_one():
    assert find_leaks(_diff(
        '_logger.LogError("Invalid format from {Source}",',
        "    source,",
        "    content);",
    ))


def test_logging_status_codes_and_identifiers_is_allowed():
    assert not find_leaks(_diff(
        '_logger.LogWarning("Non-JSON error response, status {StatusCode}", code);',
        'throw new EvidenceSourcePermanentClientException(Code, $"No reports for {organization}");',
    ))


def test_placeholder_error_codes_are_rejected():
    assert find_placeholders(_diff('throw new NsgException("TBD", "urn:x", "server.error");'))


def test_review_is_empty_for_a_clean_patch():
    assert review(_diff("Grunnlag = grunnlag?.Select(g => new Dto(g)).ToList() ?? new();")) == []
