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


def _fp(body: str) -> str:
    from agents.services.parser import parse_issue_body

    p = parse_issue_body(body)
    return compute_fingerprint(
        p.exception_type, p.stack_trace, message=p.message, cloud_role=p.cloud_role
    )


def _trace(msg: str, role: str = "func-dancore-prod") -> str:
    return f"### Error\ntrace\n\n### Message\n{msg}\n\n### Cloud role\n{role}\n"


def test_trace_issues_do_not_all_collide():
    """Without a message fallback every trace issue hashed the empty string,
    so dedupe marked unrelated failures as duplicates of the first one."""
    a = _fp(_trace("Failed to send consent reminder aid=4c7d5464-e858-470d-bb68-9a283933531c"))
    b = _fp(_trace("Bank failed while processing DNB, error ACC-1234"))
    assert a != b


def test_same_trace_condition_collapses_despite_volatile_ids():
    tmpl = (
        "Failed to send consent reminder order aid={g} exception=422 "
        "traceId 00-{t}-00f067aa0ba902b7-01"
    )
    a = _fp(_trace(tmpl.format(
        g="4c7d5464-e858-470d-bb68-9a283933531c", t="4bf92f3577b34da6a3ce929d0e0e4736")))
    b = _fp(_trace(tmpl.format(
        g="1a2b3c4d-0000-1111-2222-333344445555", t="0af7651916cd43dd8448eb211c80319c")))
    assert a == b


def test_same_message_different_role_stays_distinct():
    a = _fp(_trace("Timeout when fetching data", "func-dancore-prod"))
    b = _fp(_trace("Timeout when fetching data", "func-estilda-prod-prod"))
    assert a != b


def test_traceparent_collapses_in_stacks_too():
    a = compute_fingerprint("E", "traceId 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    b = compute_fingerprint("E", "traceId 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
    assert a == b


def test_stack_still_wins_over_message_when_present():
    """A parsed stack must remain the signal; the message is only a fallback."""
    a = compute_fingerprint("E", "at Foo.Bar()", message="wildly different text")
    b = compute_fingerprint("E", "at Foo.Bar()", message="other text entirely")
    assert a == b
