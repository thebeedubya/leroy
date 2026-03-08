"""Leroy v2 Failure Taxonomy.

Classifies task failures into categories for routing (ops vs PM),
retry budget decisions, and historical analysis.
"""

import re
from enum import Enum
import logging

logger = logging.getLogger("leroy-failure-taxonomy")


class FailureCategory(str, Enum):
    TIMEOUT_NO_OUTPUT = "timeout_no_output"
    TIMEOUT_PARTIAL = "timeout_partial"
    INFRA_UNREACHABLE = "infra_unreachable"
    INFRA_AUTH = "infra_auth"
    SCOPE_TOO_LARGE = "scope_too_large"
    SCOPE_AMBIGUOUS = "scope_ambiguous"
    MISSING_CONTEXT = "missing_context"
    DEPENDENCY_MISSING = "dependency_missing"
    CODE_ERROR = "code_error"
    HALLUCINATED_PASS = "hallucinated_pass"
    CLEAN_PASS = "clean_pass"
    PARTIAL_PASS = "partial_pass"


# Infra categories bypass retry budget and route to ops agent
INFRA_CATEGORIES = {FailureCategory.INFRA_UNREACHABLE, FailureCategory.INFRA_AUTH}

# Pattern rules: (compiled regex, FailureCategory)
_PATTERNS: list[tuple[re.Pattern, FailureCategory]] = [
    # Infra
    (re.compile(r"connection refused|unreachable|cannot connect|ECONNREFUSED|host is down", re.I),
     FailureCategory.INFRA_UNREACHABLE),
    (re.compile(r"permission denied|auth(entication|orization)?\s+(failed|error|denied)|SSH.*key|invalid credentials", re.I),
     FailureCategory.INFRA_AUTH),
    # Dependency
    (re.compile(r"module\s+not\s+found|import\s+error|no\s+such\s+file|command\s+not\s+found|ModuleNotFoundError|FileNotFoundError", re.I),
     FailureCategory.DEPENDENCY_MISSING),
    # Code errors
    (re.compile(r"traceback|exception|error:|syntax\s*error|type\s*error|name\s*error|runtime\s*error", re.I),
     FailureCategory.CODE_ERROR),
    # Scope
    (re.compile(r"ambiguous|unclear|which\s+one|not\s+sure\s+what", re.I),
     FailureCategory.SCOPE_AMBIGUOUS),
    (re.compile(r"missing\s+context|not\s+enough\s+information|need\s+more\s+detail", re.I),
     FailureCategory.MISSING_CONTEXT),
]


def classify_failure(result_text: str, task_meta: dict) -> list[FailureCategory]:
    """Parse result text and task metadata to assign failure categories.

    Multiple categories can be returned. Order: most specific first.
    """
    categories: list[FailureCategory] = []
    result_text = result_text or ""
    status = task_meta.get("status", "")

    # Timeout detection (check task_meta signals first)
    is_timeout = task_meta.get("_stuck_detected_at") is not None or task_meta.get("timeout", False)
    killed = bool(re.search(r"timeout|killed|SIGTERM|SIGKILL|timed?\s*out", result_text, re.I))

    if is_timeout or killed:
        if len(result_text.strip()) < 100:
            categories.append(FailureCategory.TIMEOUT_NO_OUTPUT)
        else:
            categories.append(FailureCategory.TIMEOUT_PARTIAL)

    # Pattern-based classification
    for pattern, category in _PATTERNS:
        if pattern.search(result_text) and category not in categories:
            categories.append(category)

    # Scope too large heuristic: short result for a spec with many criteria
    spec_text = task_meta.get("spec", "")
    criteria_count = spec_text.lower().count("success criteria") + spec_text.lower().count("- [ ]")
    if len(result_text) < 200 and criteria_count > 4 and FailureCategory.SCOPE_TOO_LARGE not in categories:
        categories.append(FailureCategory.SCOPE_TOO_LARGE)

    # If nothing matched and there IS output, check for pass/fail signals
    if not categories:
        has_pass = bool(re.search(r"pass|success|completed|done|✓|✅", result_text, re.I))
        has_fail = bool(re.search(r"fail|error|broken|❌", result_text, re.I))
        if has_pass and not has_fail:
            categories.append(FailureCategory.CLEAN_PASS)
        elif has_pass and has_fail:
            categories.append(FailureCategory.PARTIAL_PASS)
        elif has_fail:
            categories.append(FailureCategory.CODE_ERROR)

    # Fallback: if still nothing, it's an unknown code error
    if not categories:
        categories.append(FailureCategory.CODE_ERROR)

    logger.info("Classified failure: %s", [c.value for c in categories])
    return categories


def is_infra_failure(categories: list[FailureCategory]) -> bool:
    """Check if any category is an infra failure (routes to ops, not PM)."""
    return bool(set(categories) & INFRA_CATEGORIES)


def should_route_to_ops(categories: list[FailureCategory]) -> bool:
    """Check if failure should be routed to ops agent."""
    return is_infra_failure(categories)
