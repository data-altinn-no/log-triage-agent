from agents.services.parser import parse_issue_body

SAMPLE = """### Exception
System.NullReferenceException

### Message
Object reference not set to an instance of an object.

### Cloud role
data.altinn.no.api

### Stack trace
```
at Foo.Bar() in /src/x.cs:line 42
```

### Correlation id
abc-123
"""


def test_parses_sections():
    p = parse_issue_body(SAMPLE)
    assert p.exception_type == "System.NullReferenceException"
    assert p.cloud_role == "data.altinn.no.api"
    assert "Foo.Bar()" in (p.stack_trace or "")
    assert p.correlation_id == "abc-123"


def test_empty_body_is_safe():
    p = parse_issue_body("")
    assert p.exception_type is None
    assert p.raw == ""
