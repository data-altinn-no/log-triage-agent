"""Run the agent's graph nodes in-process on a fixture body. No HTTP, no creds.

Exercises the deterministic nodes only:

    parse  → fingerprint  → (skip dedupe)  → (skip enrich)  → locate  → (skip plan/fix/publish)

This lets you see what the Function-shaped payload looks like after parsing,
what fingerprint it would dedupe against, and which file the auto-fix branch
would target — all without calling GitHub or an LLM.

Usage:
    python scripts/dry_run.py                                    # default fixture
    python scripts/dry_run.py <issue-body.md>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.graph.nodes.fingerprint import fingerprint_node
from agents.graph.nodes.locate import locate_node
from agents.graph.nodes.parse import parse_node
from agents.graph.state import TriageState
from shared.config import get_settings


def pretty(obj) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: dry_run.py <issue-body.md>", file=sys.stderr)
        return 2
    fixture = Path(argv[1])
    if not fixture.exists():
        print(f"fixture not found: {fixture}", file=sys.stderr)
        return 2

    body = fixture.read_text()

    # Without a target, locate short-circuits on the kill-switch.
    settings = get_settings()
    if not settings.autofix_target_repo:
        settings.autofix_target_owner = "data-altinn-no"
        settings.autofix_target_repo = "estilda"

    state: TriageState = {
        "issue_number": 4221,
        "issue_title": "[prod] System.NullReferenceException in func-estilda-prod-prod",
        "issue_body": body,
        "issue_labels": ["auto-triage", "prod"],
    }

    print(f"=== fixture: {fixture} ({len(body)} chars) ===\n")

    parsed = parse_node(state)
    state.update(parsed)
    print("--- 1. parse ---")
    print(pretty(state["payload"]))

    fp = fingerprint_node(state)
    state.update(fp)
    print("\n--- 2. fingerprint ---")
    print(f"fingerprint: {state['result'].fingerprint}")

    print("\n--- 3. dedupe --- (skipped: would query GitHub for this fingerprint)")

    print("--- 4. enrich --- (skipped: would call Azure OpenAI)")

    loc = locate_node(state)
    state.update(loc)
    print("\n--- 5. locate ---")
    print(pretty(state["autofix"]))

    print("\n--- what would happen next ---")
    autofix = state["autofix"]
    if autofix.skipped_reason:
        print(f"SKIP fix branch: {autofix.skipped_reason}")
        print("→ publish-only path (triage issue only, no PR)")
    elif autofix.suspect:
        s = autofix.suspect
        print("PROCEED to plan:")
        print(f"  target: {s.repo_owner}/{s.repo_name}")
        print(f"  file:   {s.file_path}:{s.line}")
        print(f"  symbol: {s.symbol}")
        print(f"  evidence: {s.evidence}")
        print("→ plan would shallow-clone the target repo, read this file,")
        print("  and run the read/edit loop against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
