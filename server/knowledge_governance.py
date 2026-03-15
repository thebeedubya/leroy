"""Leroy v2 Phase 6: Knowledge Governance Pipeline.

Prevents brain rot by scoring knowledge candidates before promotion.
Two sub-phases:
  6A: KnowledgeCandidate scoring (novelty, specificity, non-contradiction)
  6B: Knowledge pruning (stale lessons archived after 30 days unreferenced)

Integration: Called from task_events.py event handlers before persist_task().
Failures always bypass scoring (failures are always novel -- Phase 5C rule).
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger("leroy-knowledge-gov")

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------
PROMOTION_THRESHOLD = 0.5      # Composite score must reach this for promotion
NOVELTY_WEIGHT = 0.5           # How much novelty matters in composite
SPECIFICITY_WEIGHT = 0.3       # How much specificity matters
NON_CONTRADICTION_WEIGHT = 0.2 # How much non-contradiction matters

# Specificity heuristics
MIN_SPECIFIC_LENGTH = 200      # Content shorter than this is likely generic
NAMED_ENTITY_PATTERNS = [
    r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b',  # Multi-word proper nouns
    r'\b(?:server|dashboard|mcp|api|endpoint|database|schema|table)\b',
    r'\b(?:kush|haze|runtz|apex)\b',           # Known machines
    r'\b(?:aianna|leroy|sentinel|goose|baker|horizon|mcmahon)\b',  # Known agents/personas
    r'\b\d{4}-\d{2}-\d{2}\b',                  # Dates
    r'\b(?:port|localhost|127\.0\.0\.1)\s*:?\s*\d+\b',  # Ports/addresses
    r'/[a-zA-Z_/]+\.(?:py|js|ts|md|json|sql)\b',  # File paths
]

# Stale pruning
STALE_DAYS_THRESHOLD = 30      # Lessons unreferenced for this many days get archived
PRUNE_BATCH_SIZE = 20          # Max lessons to prune per run

# Metrics tracking (in-memory, reset on restart)
_metrics = {
    "candidates_evaluated": 0,
    "promoted": 0,
    "discarded": 0,
    "failures_bypassed": 0,
    "discard_reasons": {},      # reason -> count
}


# ---------------------------------------------------------------------------
# KnowledgeCandidate
# ---------------------------------------------------------------------------
@dataclass
class KnowledgeCandidate:
    """A knowledge item evaluated for promotion to brain."""
    content: str
    source: str                    # "task_completion", "failure_pattern", "manual"
    task_id: str = ""
    plan_id: str = ""

    # Scores (populated by evaluate())
    novelty_score: float = 0.0
    specificity_score: float = 0.0
    non_contradiction_score: float = 1.0  # Assume no contradiction unless proven
    composite_score: float = 0.0

    # Decision
    decision: str = "pending"       # "pending", "promoted", "discarded", "bypassed"
    discard_reason: str = ""

    # Audit
    similar_lessons: list = field(default_factory=list)
    evaluation_ms: int = 0


# ---------------------------------------------------------------------------
# 6A: Scoring functions
# ---------------------------------------------------------------------------

def _score_novelty(content: str, existing_lessons: list[dict]) -> float:
    """Score how novel this content is vs existing brain knowledge.

    1.0 = entirely new (no similar lessons found)
    0.0 = exact duplicate exists

    Uses content fingerprint + keyword overlap as heuristic.
    Full semantic similarity would require embedding comparison (future).
    """
    if not existing_lessons:
        return 1.0  # Nothing in brain = fully novel

    content_lower = content.lower()
    content_words = set(re.findall(r'\b\w{4,}\b', content_lower))

    if not content_words:
        return 0.5  # Can't evaluate, give benefit of doubt

    best_overlap = 0.0
    for lesson in existing_lessons:
        lesson_text = (lesson.get("content", "") or lesson.get("text", "")).lower()
        lesson_words = set(re.findall(r'\b\w{4,}\b', lesson_text))
        if not lesson_words:
            continue

        # Jaccard similarity
        intersection = content_words & lesson_words
        union = content_words | lesson_words
        overlap = len(intersection) / len(union) if union else 0.0
        best_overlap = max(best_overlap, overlap)

    # Convert overlap to novelty (inverse)
    # 0.8+ overlap = very low novelty (0.0-0.2)
    # 0.0 overlap = fully novel (1.0)
    novelty = max(0.0, 1.0 - (best_overlap * 1.25))
    return round(min(1.0, novelty), 3)


def _score_specificity(content: str) -> float:
    """Score how specific and actionable the content is.

    1.0 = highly specific (named entities, file paths, concrete details)
    0.0 = completely generic ("task completed successfully")
    """
    if not content:
        return 0.0

    score = 0.0

    # Length factor: longer content is generally more specific
    if len(content) >= MIN_SPECIFIC_LENGTH:
        score += 0.2
    elif len(content) >= 100:
        score += 0.1

    # Named entity density
    entity_count = 0
    for pattern in NAMED_ENTITY_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        entity_count += len(matches)

    # Normalize: 3+ entities = full credit (0.4), 1-2 = partial
    entity_factor = min(1.0, entity_count / 3.0) * 0.4
    score += entity_factor

    # Code/technical indicators (file paths, error messages, config values)
    technical_patterns = [
        r'```',                    # Code blocks
        r'\b(?:error|exception|traceback|failed)\b',  # Error context
        r'\b(?:GET|POST|PUT|DELETE)\s+/',  # API calls
        r'\b\d+\.\d+\.\d+',       # Version numbers or IPs
    ]
    tech_count = sum(1 for p in technical_patterns if re.search(p, content, re.IGNORECASE))
    score += min(0.2, tech_count * 0.05)

    # Penalty for generic phrases
    generic_phrases = [
        "task completed successfully",
        "no issues encountered",
        "all criteria met",
        "clean pass",
        "everything worked",
    ]
    for phrase in generic_phrases:
        if phrase in content.lower():
            score -= 0.15

    return round(max(0.0, min(1.0, score)), 3)


def _check_non_contradiction(content: str, existing_lessons: list[dict]) -> float:
    """Check if content contradicts existing knowledge.

    1.0 = no contradiction detected
    0.0 = clear contradiction found

    Heuristic: look for negation patterns against existing lesson keywords.
    Full contradiction detection would require NLI model (future).
    """
    if not existing_lessons:
        return 1.0

    content_lower = content.lower()

    # Simple heuristic: check for direct negation of existing lessons
    negation_patterns = [
        (r"(?:do not|don't|never|avoid)\s+(.+?)(?:\.|$)", "prohibition"),
        (r"(?:always|must|should)\s+(.+?)(?:\.|$)", "mandate"),
    ]

    content_directives = []
    for pattern, dtype in negation_patterns:
        for match in re.finditer(pattern, content_lower):
            content_directives.append((dtype, match.group(1).strip()[:60]))

    if not content_directives:
        return 1.0  # No directives to contradict

    # Check each directive against existing lessons
    for dtype, directive in content_directives:
        for lesson in existing_lessons:
            lesson_text = (lesson.get("content", "") or lesson.get("text", "")).lower()

            # Check for opposite directive
            if dtype == "prohibition":
                # Content says "don't X" -- check if existing says "always X" or "should X"
                if re.search(rf"(?:always|must|should)\s+{re.escape(directive[:20])}", lesson_text):
                    logger.info("Contradiction detected: content prohibits '%s' but existing lesson mandates it",
                                directive[:40])
                    return 0.0
            elif dtype == "mandate":
                # Content says "always X" -- check if existing says "don't X"
                if re.search(rf"(?:do not|don't|never|avoid)\s+{re.escape(directive[:20])}", lesson_text):
                    logger.info("Contradiction detected: content mandates '%s' but existing lesson prohibits it",
                                directive[:40])
                    return 0.0

    return 1.0


def evaluate_candidate(candidate: KnowledgeCandidate, persist_manager) -> KnowledgeCandidate:
    """Score a knowledge candidate and decide promote/discard.

    Args:
        candidate: The KnowledgeCandidate to evaluate.
        persist_manager: PersistenceManager instance for brain queries.

    Returns:
        The same candidate with scores and decision populated.
    """
    start = time.monotonic()
    _metrics["candidates_evaluated"] += 1

    # Failure patterns always bypass scoring (Phase 5C rule)
    if candidate.source == "failure_pattern":
        candidate.decision = "bypassed"
        candidate.composite_score = 1.0
        candidate.novelty_score = 1.0
        candidate.specificity_score = 1.0
        candidate.evaluation_ms = round((time.monotonic() - start) * 1000)
        _metrics["failures_bypassed"] += 1
        logger.info("Candidate %s: failure pattern bypassed scoring (always promote)",
                     candidate.task_id[:8])
        return candidate

    # Query brain for similar existing lessons
    similar = []
    try:
        result = persist_manager.query_lessons(candidate.content[:200], max_results=5)
        if result.get("queried"):
            similar = result.get("lessons", [])
    except Exception as e:
        logger.warning("Candidate %s: brain query failed during scoring: %s",
                        candidate.task_id[:8], e)
        # Brain unavailable = can't check novelty, promote anyway
        candidate.decision = "promoted"
        candidate.composite_score = 0.75  # Assume moderate value
        candidate.evaluation_ms = round((time.monotonic() - start) * 1000)
        _metrics["promoted"] += 1
        return candidate

    candidate.similar_lessons = similar

    # Score each dimension
    candidate.novelty_score = _score_novelty(candidate.content, similar)
    candidate.specificity_score = _score_specificity(candidate.content)
    candidate.non_contradiction_score = _check_non_contradiction(candidate.content, similar)

    # Composite
    candidate.composite_score = round(
        candidate.novelty_score * NOVELTY_WEIGHT
        + candidate.specificity_score * SPECIFICITY_WEIGHT
        + candidate.non_contradiction_score * NON_CONTRADICTION_WEIGHT,
        3,
    )

    # Threshold decision
    if candidate.composite_score >= PROMOTION_THRESHOLD:
        candidate.decision = "promoted"
        _metrics["promoted"] += 1
        logger.info(
            "Candidate %s PROMOTED (score=%.3f: novelty=%.2f, specificity=%.2f, non_contradiction=%.2f)",
            candidate.task_id[:8], candidate.composite_score,
            candidate.novelty_score, candidate.specificity_score, candidate.non_contradiction_score,
        )
    else:
        candidate.decision = "discarded"
        reasons = []
        if candidate.novelty_score < 0.3:
            reasons.append("low_novelty")
        if candidate.specificity_score < 0.3:
            reasons.append("low_specificity")
        if candidate.non_contradiction_score < 0.5:
            reasons.append("contradiction")
        candidate.discard_reason = ", ".join(reasons) if reasons else "below_threshold"
        _metrics["discarded"] += 1
        _metrics["discard_reasons"][candidate.discard_reason] = (
            _metrics["discard_reasons"].get(candidate.discard_reason, 0) + 1
        )
        logger.info(
            "Candidate %s DISCARDED (score=%.3f: %s)",
            candidate.task_id[:8], candidate.composite_score, candidate.discard_reason,
        )

    candidate.evaluation_ms = round((time.monotonic() - start) * 1000)
    return candidate


# ---------------------------------------------------------------------------
# 6B: Knowledge Pruning
# ---------------------------------------------------------------------------

def find_stale_lessons(persist_manager, days: int = STALE_DAYS_THRESHOLD) -> list[dict]:
    """Query brain for lessons that haven't been referenced in `days` days.

    Returns list of lesson dicts with metadata.
    Note: This requires forge-brain to support a staleness query.
    Falls back to empty list if brain doesn't support it yet.
    """
    try:
        # Query for lessons with "stale" or "unreferenced" semantic
        result = persist_manager.query_lessons(
            f"lessons older than {days} days unreferenced", max_results=PRUNE_BATCH_SIZE
        )
        if result.get("queried"):
            return result.get("lessons", [])
    except Exception as e:
        logger.warning("Stale lesson query failed: %s", e)
    return []


def prune_stale_knowledge(persist_manager) -> dict:
    """Archive lessons unreferenced for >30 days.

    Returns: {checked: int, archived: int, errors: int}

    Note: Full pruning requires forge-brain archive_lesson tool.
    For now, this identifies candidates and logs them.
    Actual archival is deferred until forge-brain supports it.
    """
    result = {"checked": 0, "archived": 0, "errors": 0, "candidates": []}

    stale = find_stale_lessons(persist_manager)
    result["checked"] = len(stale)

    for lesson in stale:
        lesson_id = lesson.get("id", lesson.get("session_id", ""))
        title = lesson.get("session_title", lesson.get("title", ""))
        result["candidates"].append({
            "id": lesson_id,
            "title": title,
            "action": "log_only",  # Until forge-brain supports archive
        })
        logger.info("Stale lesson candidate: %s -- %s", lesson_id[:12] if lesson_id else "?", title)

    logger.info("Prune check: %d stale candidates found (archive pending brain support)",
                len(result["candidates"]))
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def governance_metrics() -> dict:
    """Return current knowledge governance metrics."""
    return dict(_metrics)


def reset_metrics() -> None:
    """Reset metrics (testing)."""
    _metrics.update({
        "candidates_evaluated": 0,
        "promoted": 0,
        "discarded": 0,
        "failures_bypassed": 0,
        "discard_reasons": {},
    })
