"""Run the agent on real issues with every GitHub write refused, to see what it would do."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _refuse_writes() -> None:
    from github.Requester import Requester

    from agents.services.workspace import Workspace

    for name in ("requestJson", "requestMultipart", "requestBlob"):
        original = getattr(Requester, name)

        def guarded(self, verb, url, *args, _original=original, **kwargs):
            # Minting an installation token is the one POST App auth needs.
            if verb.upper() not in ("GET", "HEAD") and not url.endswith("/access_tokens"):
                raise RuntimeError(f"trial refused a GitHub write: {verb} {url}")
            return _original(self, verb, url, *args, **kwargs)

        setattr(Requester, name, guarded)

    def no_push(*_args, **_kwargs):
        raise RuntimeError("trial refused a git push")

    Workspace.push_branch = no_push


def _outcome(record: dict) -> str:
    if record.get("error"):
        return "error"
    if record.get("declined_reason"):
        return "declined"
    if record.get("changed_on_main"):
        return "moved"
    if record.get("diff"):
        changed = sum(
            1 for line in record["diff"].splitlines()
            if line[:1] in "+-" and line[:3] not in ("+++", "---")
        )
        return f"patch({changed})"
    return (record.get("skipped_reason") or "no outcome")[:24]


def _run(args: tuple[list[dict], Path, int]) -> list[dict]:
    cases, out, run = args
    _refuse_writes()

    from agents.graph.nodes.dedupe import dedupe_node
    from agents.graph.nodes.enrich import enrich_node
    from agents.graph.nodes.fingerprint import fingerprint_node
    from agents.graph.nodes.locate import locate_node
    from agents.graph.nodes.parse import parse_node
    from agents.graph.nodes.plan import plan_node
    from agents.graph.nodes.provenance import provenance_node
    from agents.graph.state import TriageState
    from agents.services import github as gh
    from agents.services.patch_guard import review
    from shared.config import get_settings

    settings = get_settings()
    settings.autofix_workdir = f"{settings.autofix_workdir}-trial-{run}"
    run_dir = out / f"run-{run}"
    run_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for case in cases:
        started = time.time()
        issue = gh.input_repo().get_issue(case["issue"])
        state: TriageState = {
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_body": case.get("body") or issue.body or "",
            "issue_labels": [label.name for label in issue.labels],
        }
        record = {"case": case["id"], "issue": issue.number, "title": issue.title}
        try:
            for node in (parse_node, fingerprint_node, dedupe_node, enrich_node):
                state.update(node(state))
            for node in (provenance_node, locate_node, plan_node):
                state.update(node(state))
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"

        result, autofix = state.get("result"), state.get("autofix")
        provenance = state.get("provenance")
        patch = autofix.patch if autofix else None
        record.update({
            "seconds": round(time.time() - started, 1),
            "route": state.get("output_repo"),
            "fingerprint": result.fingerprint if result else None,
            "triage": result.model_dump(exclude={"fingerprint"}) if result else None,
            "provenance": provenance.model_dump() if provenance else None,
            "suspect": autofix.suspect.model_dump() if autofix and autofix.suspect else None,
            "skipped_reason": autofix.skipped_reason if autofix else None,
            "declined_reason": autofix.declined_reason if autofix else None,
            "changed_on_main": autofix.changed_on_main if autofix else [],
            "diff": patch.diff if patch else "",
            "rationale": patch.rationale if patch else "",
        })
        record["outcome"] = _outcome(record)
        record["guard_violations"] = review(record["diff"])
        (run_dir / f"{case['id']}.json").write_text(
            json.dumps(record, indent=2, default=str, ensure_ascii=False)
        )
        records.append(record)
        seconds, outcome = record["seconds"], record["outcome"]
        print(f"run {run}  {case['id']:<12} {seconds:>5}s  {outcome}", flush=True)
    return records


def _cases(args) -> list[dict]:
    if args.issues:
        return [{"id": str(n), "issue": n} for n in args.issues]
    expected = json.loads((args.eval / "expected.json").read_text())
    return [
        {
            "id": c["id"][:2],
            "issue": c["representative_issue"],
            "body": (args.eval / "fixtures" / f"{c['id']}.md").read_text(),
        }
        for c in expected
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--issues", type=int, nargs="+", help="log-triage issue numbers")
    source.add_argument("--eval", type=Path, help="the eval directory from the log-triage repo")
    parser.add_argument("--runs", type=int, default=1, help="repeat to separate signal from noise")
    parser.add_argument(
        "--out", type=Path, required=True,
        help="results contain production error text; keep this outside the repository",
    )
    args = parser.parse_args()

    from shared.config import get_settings

    settings = get_settings()
    print(f"model {settings.azure_ai_foundry_model} via {settings.llm_provider}, "
          f"routing under {settings.github_output_owner}", flush=True)

    cases = _cases(args)
    jobs = [(cases, args.out, run) for run in range(1, args.runs + 1)]
    with ProcessPoolExecutor(max_workers=args.runs) as pool:
        runs = list(pool.map(_run, jobs))

    print(f"\n{'case':<12} " + " ".join(f"{'run ' + str(i):<14}" for i in range(1, args.runs + 1)))
    for i, case in enumerate(cases):
        outcomes = [r[i]["outcome"] + (" GUARD" if r[i]["guard_violations"] else "") for r in runs]
        print(f"{case['id']:<12} " + " ".join(f"{o:<14}" for o in outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
