"""Tool-using agent loop for code fixes.

The model edits a real checkout through read/edit tools; the resulting
`git diff` is the patch. Because edits apply to real file contents, context
lines match by construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agents.services.llm import get_chat_model
from agents.services.workspace import Workspace, WorkspaceError
from shared.logging import get_logger
from shared.models import ErrorPayload, SuspectSite

log = get_logger(__name__)

_MAX_ITERATIONS = 12

# Same tool, same args, this often in the window means no progress.
_STUCK_REPEAT_LIMIT = 3
_STUCK_WINDOW = 5

# Hitting the cap discards the run's edits, so warn before we get there.
_WRAPUP_WARNING_TURNS = 3
_WRAPUP_NOTICE = (
    "You have {left} turn(s) left before this session ends. Stop exploring. "
    "If you have a fix, make the remaining edits now and call done. If you do "
    "not, call done with rationale 'no confident fix'."
)

_FILE_READ_CAP = 400_000
# Far above _TOOL_RESULT_CAP: a silently truncated read makes the model edit
# code it never saw.
_READ_RESULT_CAP = 60_000
_TOOL_RESULT_CAP = 8_000
# Either side of the suspect line.
_WINDOW_RADIUS = 400


@dataclass
class AgentResult:
    success: bool
    rationale: str = ""
    changed_files: list[str] = field(default_factory=list)
    failure_reason: str | None = None


class _DoneSignal(Exception):  # noqa: N818 — control-flow signal, not an error
    """Raised internally when the LLM calls the done tool."""

    def __init__(self, rationale: str, changed_files: list[str]):
        self.rationale = rationale
        self.changed_files = changed_files


def render_file_window(
    content: str,
    *,
    path: str,
    suspect_line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Line-numbered view: whole file if it fits, else a window on the suspect
    line. Any partial view carries an explicit TRUNCATED note."""
    lines = content.splitlines()
    total = len(lines)

    if start_line is None and end_line is None:
        if len("\n".join(lines)) <= _READ_RESULT_CAP:
            lo, hi = 1, total
        else:
            centre = suspect_line or 1
            lo = max(1, centre - _WINDOW_RADIUS)
            hi = min(total, centre + _WINDOW_RADIUS)
    else:
        lo = max(1, start_line or 1)
        hi = min(total, end_line or total)

    if total and lo > total:
        return f"ERROR: start_line {lo} is past end of file ({total} lines)"

    selected = lines[lo - 1 : hi]
    body = "\n".join(f"{lo + i:5d}\t{line}" for i, line in enumerate(selected))

    # Backstop, but never silently.
    clipped = False
    if len(body) > _READ_RESULT_CAP:
        body = body[:_READ_RESULT_CAP]
        hi = lo + body.count("\n")
        clipped = True

    header = f"# {path} — showing lines {lo}-{hi} of {total}"
    notes = []
    if lo > 1 or hi < total:
        notes.append(
            f"TRUNCATED: {total} lines total, you are seeing {lo}-{hi}. "
            f"Call read_file('{path}', start_line=..., end_line=...) to see more."
        )
    if clipped:
        notes.append("Output was clipped at the size cap; request a narrower range.")
    suffix = ("\n\n" + "\n".join(notes)) if notes else ""
    return f"{header}\n{body}{suffix}"


def run_fix_agent(
    *,
    ws: Workspace,
    payload: ErrorPayload,
    suspect: SuspectSite,
) -> AgentResult:
    """Drive the LLM through a read/edit loop on the workspace."""
    read_files: set[str] = set()
    edited_files: set[str] = set()

    @tool
    def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Read a file from the workspace, with line numbers.

        Args:
            path: Repo-relative path, e.g. 'src/Foo/Bar.cs'.
            start_line: Optional 1-based first line to return. Use to page through
                a large file after an earlier read was truncated.
            end_line: Optional 1-based last line to return (inclusive).
        """
        try:
            content = ws.read_file(path, max_bytes=_FILE_READ_CAP)
        except WorkspaceError as exc:
            return f"ERROR: {exc}"
        read_files.add(path)
        return render_file_window(
            content,
            path=path,
            suspect_line=suspect.line,
            start_line=start_line,
            end_line=end_line,
        )

    @tool
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace exact `old_string` with `new_string` in the file.

        Rules:
        - You MUST call read_file on this path first.
        - `old_string` must be unique in the file.
        - Indentation must match the file exactly.
        """
        if path not in read_files:
            return f"ERROR: read_file('{path}') first"
        try:
            content = ws.read_file(path, max_bytes=_FILE_READ_CAP)
        except WorkspaceError as exc:
            return f"ERROR: {exc}"
        occurrences = content.count(old_string)
        if occurrences == 0:
            return "ERROR: old_string not found verbatim in file"
        if occurrences > 1:
            return (
                f"ERROR: old_string appears {occurrences} times — "
                "make it unique with more surrounding context"
            )
        new_content = content.replace(old_string, new_string, 1)
        try:
            ws.write_file(path, new_content)
        except WorkspaceError as exc:
            return f"ERROR: {exc}"
        edited_files.add(path)
        return "OK"

    @tool
    def done(rationale: str) -> str:
        """Signal that the fix is complete.

        Args:
            rationale: 1-3 sentences explaining the change and why it fixes the error.
        """
        raise _DoneSignal(rationale=rationale, changed_files=sorted(edited_files))

    tools = [read_file, edit_file, done]
    tools_by_name = {t.name: t for t in tools}

    llm = get_chat_model(temperature=0.0).bind_tools(tools)

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_user_prompt(payload, suspect)),
    ]

    signatures: list[str] = []
    warned = False

    for iteration in range(_MAX_ITERATIONS):
        turns_left = _MAX_ITERATIONS - iteration
        if turns_left <= _WRAPUP_WARNING_TURNS and not warned:
            warned = True
            messages.append(HumanMessage(content=_WRAPUP_NOTICE.format(left=turns_left)))

        response = llm.invoke(messages)
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            log.info("agent_fix.no_tool_calls", iteration=iteration)
            return AgentResult(
                success=bool(edited_files),
                rationale=str(response.content)[:1000],
                changed_files=sorted(edited_files),
                failure_reason=None if edited_files else "agent ended without edits",
            )

        signatures.extend(
            f"{c['name']}:{json.dumps(c['args'], sort_keys=True, default=str)}"
            for c in tool_calls
        )
        window = signatures[-_STUCK_WINDOW:]
        if window and max(window.count(sig) for sig in set(window)) >= _STUCK_REPEAT_LIMIT:
            log.info("agent_fix.stuck", iteration=iteration, edits=len(edited_files))
            return AgentResult(
                success=bool(edited_files),
                rationale="agent repeated the same call without progress",
                changed_files=sorted(edited_files),
                failure_reason="stuck: identical tool call repeated",
            )

        for call in tool_calls:
            name = call["name"]
            args = call["args"]
            tool_id = call["id"]
            try:
                result = tools_by_name[name].invoke(args)
            except _DoneSignal as done_sig:
                log.info(
                    "agent_fix.done",
                    iteration=iteration,
                    files=done_sig.changed_files,
                )
                return AgentResult(
                    success=bool(done_sig.changed_files),
                    rationale=done_sig.rationale,
                    changed_files=done_sig.changed_files,
                    failure_reason=None
                    if done_sig.changed_files
                    else "done called without any edits",
                )
            except Exception as exc:  # noqa: BLE001
                result = f"ERROR: tool execution failed: {exc}"
            log.info("agent_fix.tool", name=name, ok=not str(result).startswith("ERROR"))
            # read_file already windowed and labelled itself.
            cap = _READ_RESULT_CAP if name == "read_file" else _TOOL_RESULT_CAP
            messages.append(ToolMessage(content=str(result)[:cap], tool_call_id=tool_id))

    log.info("agent_fix.iteration_cap", edits=len(edited_files))
    return AgentResult(
        success=bool(edited_files),
        rationale="agent reached iteration cap",
        changed_files=sorted(edited_files),
        failure_reason="iteration cap reached without `done`",
    )



_SYSTEM_PROMPT = """You are a careful code-fix agent.

You have three tools: read_file, edit_file, done.

Workflow:
1. read_file the suspect file to see the actual code. Large files come back as a
   window with a `TRUNCATED:` note giving the total line count — if the code you
   need is outside the range shown, call read_file again with start_line/end_line
   before editing. Never edit a region you have not actually read.
2. Identify the SPECIFIC line(s) responsible for the error described in the user message.
   The error message and stack trace tell you what failed; the file shows you where.
3. Use edit_file to make the smallest correct change. Each edit_file call replaces
   one unique snippet — copy `old_string` verbatim from what read_file returned
   (without the line-number prefix).
4. When the fix is complete, call done with a 1-3 sentence rationale.

Hard rules:
- Diagnose root cause. If the error message names an incompatible API call, library command,
  or schema (e.g. "unknown command 'EXPIRETIME'", "column does not exist", "method not found"),
  REPLACE the offending call with a compatible alternative. Do NOT wrap it in try/catch.
  Do NOT add a null-check around it. Those do not fix the underlying problem.
- The indicated line in the user message is where the runtime caught the exception. The
  actual call that needs to change may be on a nearby line. Read enough of the file to
  understand the local control flow before editing.
- Touch the minimum number of lines needed for a correct fix. If the type signature of a
  replacement API is different, you may need to update related variable usages too —
  do that, don't leave broken code.
- Do not modify config, CI, package, or migration files. Code changes only.
- If you cannot identify a confident fix from the file shown, call done with rationale
  "no confident fix" and make no edits.
"""


def _user_prompt(payload: ErrorPayload, suspect: SuspectSite) -> str:
    return f"""Production error to fix:

- exception_type: {payload.exception_type or 'unknown'}
- message: {payload.message or ''}
- operation: {payload.operation or ''}

Stack trace (top frame first):
```
{(payload.stack_trace or '')[:3000]}
```

Suspect site (where the runtime caught it; the call that needs changing may be a few lines above):
- file: {suspect.file_path}
- line: {suspect.line or '?'}
- symbol: {suspect.symbol or '?'}

Begin by reading {suspect.file_path}, then make the fix.
"""
