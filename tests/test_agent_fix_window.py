"""Tests for the file-window rendering used by the fix agent.

The regression these guard: reads used to be silently capped at 8k chars, so on
any file where the suspect line sat past ~180 lines the agent never saw the code
it was asked to fix, got no truncation signal, and either hallucinated an edit or
gave up. A partial view must always announce itself.
"""

from agents.services.agent_fix import _READ_RESULT_CAP, _WINDOW_RADIUS, render_file_window


def _numbered_source(n_lines: int) -> str:
    return "\n".join(f"var x{i} = {i};" for i in range(1, n_lines + 1))


def test_small_file_is_returned_whole_without_truncation_note():
    out = render_file_window(_numbered_source(30), path="src/Foo.cs")
    assert "showing lines 1-30 of 30" in out
    assert "TRUNCATED" not in out
    assert "var x1 = 1;" in out
    assert "var x30 = 30;" in out


def test_line_numbers_are_one_based_and_match_content():
    out = render_file_window("alpha\nbravo\ncharlie", path="a.txt")
    assert "    1\talpha" in out
    assert "    3\tcharlie" in out


def test_large_file_windows_around_the_suspect_line():
    # Big enough that the whole-file branch cannot apply.
    total = 20_000
    suspect = 12_000
    out = render_file_window(
        _numbered_source(total), path="src/Big.cs", suspect_line=suspect
    )
    assert f"var x{suspect} = {suspect};" in out, "suspect line must be inside the window"
    assert "TRUNCATED" in out
    assert f"{total} lines total" in out


def test_window_is_centred_not_truncated_from_the_top():
    # The old behaviour returned the first ~180 lines regardless of the suspect
    # line. A deep suspect line must not fall outside the window.
    total = 20_000
    suspect = 15_000
    out = render_file_window(
        _numbered_source(total), path="src/Big.cs", suspect_line=suspect
    )
    assert "var x1 = 1;" not in out
    assert f"var x{suspect} = {suspect};" in out


def test_explicit_range_is_honoured():
    out = render_file_window(
        _numbered_source(500), path="src/Foo.cs", start_line=100, end_line=110
    )
    assert "showing lines 100-110 of 500" in out
    assert "var x100 = 100;" in out
    assert "var x111 = 111;" not in out
    assert "TRUNCATED" in out  # partial view still announces itself


def test_start_line_past_eof_is_an_error_not_an_empty_read():
    out = render_file_window(_numbered_source(10), path="src/Foo.cs", start_line=999)
    assert out.startswith("ERROR:")
    assert "past end of file" in out


def test_partial_view_never_omits_the_truncation_marker():
    total = 20_000
    out = render_file_window(
        _numbered_source(total), path="src/Big.cs", suspect_line=10_000
    )
    shown = [ln for ln in out.splitlines() if "\t" in ln]
    assert len(shown) < total, "sanity: this file should be windowed"
    assert "TRUNCATED" in out, "a partial view must always be labelled"


def test_window_radius_bounds_the_view():
    total = 20_000
    suspect = 10_000
    out = render_file_window(
        _numbered_source(total), path="src/Big.cs", suspect_line=suspect
    )
    assert f"var x{suspect - _WINDOW_RADIUS + 5} = " in out
    assert f"var x{suspect - _WINDOW_RADIUS - 50} = " not in out


def test_read_cap_is_far_above_the_generic_tool_cap():
    # Guards the specific bug: the read cap must not collapse back to the 8k
    # generic tool-result cap.
    assert _READ_RESULT_CAP >= 50_000
