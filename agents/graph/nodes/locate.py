"""Locate node: parse the stack trace to guess file + line in the target repo."""

from __future__ import annotations

from agents.graph.state import TriageState
from agents.services.locator import extract_frames, pick_best_frame
from shared.config import get_settings
from shared.logging import get_logger
from shared.models import AutoFixOutcome, SuspectSite

log = get_logger(__name__)


def locate_node(state: TriageState) -> TriageState:
    settings = get_settings()
    outcome: AutoFixOutcome = state.get("autofix") or AutoFixOutcome(attempted=True)
    outcome.attempted = True

    # Pre-flight: block label on the input issue short-circuits everything.
    labels = state.get("issue_labels") or []
    if settings.autofix_block_label and settings.autofix_block_label in labels:
        outcome.skipped_reason = f"blocked by label '{settings.autofix_block_label}'"
        log.info("locate.blocked", reason=outcome.skipped_reason)
        return {"autofix": outcome}

    payload = state["payload"]
    frames = extract_frames(payload.stack_trace or "")
    best = pick_best_frame(frames)
    if best is None:
        outcome.skipped_reason = "no parseable frame in stack trace"
        log.info("locate.no_frame")
        return {"autofix": outcome}

    # Confidence heuristic:
    #  - clean repo-relative-looking path + line number  => 0.7
    #  - only a filename (Java-style)                     => 0.3
    looks_relative = "/" in best.file_path and not best.file_path.startswith("/")
    confidence = 0.7 if looks_relative and best.line else 0.3

    if confidence < settings.autofix_min_confidence:
        outcome.skipped_reason = (
            f"confidence {confidence:.2f} below threshold {settings.autofix_min_confidence:.2f}"
        )
        log.info("locate.low_confidence", file=best.file_path, confidence=confidence)
        return {"autofix": outcome}

    outcome.suspect = SuspectSite(
        repo_owner=settings.autofix_target_owner,
        repo_name=settings.autofix_target_repo,
        file_path=best.file_path,
        line=best.line,
        symbol=best.symbol,
        confidence=confidence,
        evidence=f"Top application frame: {best.raw}",
    )
    log.info(
        "locate.picked",
        file=best.file_path,
        line=best.line,
        symbol=best.symbol,
        confidence=confidence,
        total_frames=len(frames),
    )
    return {"autofix": outcome}
