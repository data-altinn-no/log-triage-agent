"""Parse a GitHub issue body (produced by the Azure Logic App) into structured ErrorPayload.

The Logic App writes a predictable template, for example:

    ### Exception
    System.NullReferenceException

    ### Message
    Object reference not set to an instance of an object.

    ### Cloud role
    data.altinn.no.api

    ### Operation
    GET /datasets/{id}

    ### Stack trace
    ```
    at Foo.Bar() in /src/...
    ```

    ### Timestamp
    2026-04-24T08:00:00Z

    ### Correlation id
    abc-123
"""

import re

from shared.models import ErrorPayload

_SECTION_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$", re.MULTILINE)


def _extract_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for idx, m in enumerate(matches):
        name = m.group("name").strip().lower()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        # strip fenced code blocks
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
        sections[name] = content.strip()
    return sections


def parse_issue_body(body: str) -> ErrorPayload:
    sections = _extract_sections(body or "")
    return ErrorPayload(
        exception_type=sections.get("exception"),
        message=sections.get("message"),
        stack_trace=sections.get("stack trace"),
        cloud_role=sections.get("cloud role"),
        operation=sections.get("operation"),
        request_path=sections.get("request path") or sections.get("operation"),
        timestamp=sections.get("timestamp"),
        correlation_id=sections.get("correlation id"),
        raw=body or "",
    )
