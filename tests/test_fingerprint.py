from agents.services.fingerprint import compute_fingerprint, normalize_signature


def test_normalize_strips_guids_and_numbers():
    s = "at Foo() in Bar.cs:line 42 id=a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    n = normalize_signature(s)
    assert "42" not in n
    assert "a1b2c3d4" not in n
    assert "<guid>" in n


def test_same_stack_different_line_numbers_match():
    a = "System.NullReferenceException at Foo.Bar() in /src/x.cs:line 10"
    b = "System.NullReferenceException at Foo.Bar() in /src/x.cs:line 99"
    assert compute_fingerprint("NRE", a) == compute_fingerprint("NRE", b)


def test_different_exceptions_differ():
    assert compute_fingerprint("A", "same stack") != compute_fingerprint("B", "same stack")
