"""Leroy v2 Task Analytics (KISS consolidation: was quality_scoring.py + criteria_validator.py).

Two analysis concerns unified into one module:

A) Spec Quality Scoring (was quality_scoring.py — Phase 9)
   Quality rubric (0.0 to 1.0) with pre-send and post-outcome scoring.
   Pre-send factors: criteria clarity, target declared, do-not-do, brain queried,
   complexity appropriate, preflight passed, dedup clean.
   Post-outcome factors: QA pass rate, hallucinated pass, respec count,
   pipe timeout, budget exhausted.
   Weights are starting points — Phase 10 learns optimal weights from data.

B) Criteria Validation + Drift Detection (was criteria_validator.py — Phase 11)
   11A: Compare builder [WHAT] against typed IR criteria (verified/unverified/contradicted).
   11B: Hallucination detection (builder claims pass, <50% criteria verified).
   11C: Drift detection on respec (added/removed criteria, scope expansion warning).
   11D: Promote/fail decision (COMPLETED_UNVERIFIED -> COMPLETED_VERIFIED or FAILED_RETRYABLE).
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

logger = logging.getLogger("leroy-task-analytics")


# ===========================================================================
# A) SPEC QUALITY SCORING
# ===========================================================================

# ---------------------------------------------------------------------------
# Score weights (Phase 10 will learn these from data)
# ---------------------------------------------------------------------------

PRE_SEND_WEIGHTS = {
    "criteria_present": 0.10,
    "criteria_specific": 0.10,    # >2 criteria, each >10 chars
    "target_declared": 0.10,
    "do_not_do_present": 0.05,
    "brain_queried": 0.15,
    "brain_not_queried": -0.15,
    "complexity_appropriate": 0.10,  # 1-20 complexity score
    "preflight_passed": 0.05,
    "dedup_clean": 0.05,
}

POST_OUTCOME_WEIGHTS = {
    "qa_full_pass": 0.20,
    "qa_partial_pass": 0.10,
    "qa_fail": -0.10,
    "hallucinated_pass": -0.50,
    "no_respec": 0.05,
    "respec_required": -0.10,
    "pipe_timeout": -0.15,
    "budget_exhausted": -0.20,
}


@dataclass
class QualityBreakdown:
    """Detailed breakdown of a quality score."""
    pre_send_score: float = 0.0
    post_outcome_score: float = 0.0
    total_score: float = 0.0
    factors: list[dict] = field(default_factory=list)
    computed_at: str = ""
    phase: str = ""  # "pre_send" or "post_outcome"

    def to_json(self) -> dict:
        return asdict(self)


def score_pre_send(typed_ir, spec_text: str, brain_queried: bool = False,
                   dedup_result: dict | None = None,
                   preflight_result: dict | None = None,
                   complexity_result: dict | None = None) -> QualityBreakdown:
    """Compute pre-send quality score from spec analysis results."""
    factors = []
    score = 0.0

    has_criteria = len(typed_ir.criteria) > 0
    factors.append({
        "name": "criteria_present",
        "weight": PRE_SEND_WEIGHTS["criteria_present"],
        "applied": has_criteria,
        "reason": f"{len(typed_ir.criteria)} criteria found" if has_criteria else "no criteria found",
    })
    if has_criteria:
        score += PRE_SEND_WEIGHTS["criteria_present"]

    specific = (len(typed_ir.criteria) >= 2 and all(len(c) > 10 for c in typed_ir.criteria))
    factors.append({
        "name": "criteria_specific",
        "weight": PRE_SEND_WEIGHTS["criteria_specific"],
        "applied": specific,
        "reason": "criteria are specific and testable" if specific else "criteria lack specificity",
    })
    if specific:
        score += PRE_SEND_WEIGHTS["criteria_specific"]

    has_target = typed_ir.target is not None
    factors.append({
        "name": "target_declared",
        "weight": PRE_SEND_WEIGHTS["target_declared"],
        "applied": has_target,
        "reason": f"target: {typed_ir.target}" if has_target else "no target machine declared",
    })
    if has_target:
        score += PRE_SEND_WEIGHTS["target_declared"]

    has_dnd = len(typed_ir.do_not_do) > 0
    factors.append({
        "name": "do_not_do_present",
        "weight": PRE_SEND_WEIGHTS["do_not_do_present"],
        "applied": has_dnd,
        "reason": f"{len(typed_ir.do_not_do)} do-not-do items" if has_dnd else "no do-not-do section",
    })
    if has_dnd:
        score += PRE_SEND_WEIGHTS["do_not_do_present"]

    if brain_queried:
        factors.append({
            "name": "brain_queried",
            "weight": PRE_SEND_WEIGHTS["brain_queried"],
            "applied": True,
            "reason": "forge-brain queried before spec",
        })
        score += PRE_SEND_WEIGHTS["brain_queried"]
    else:
        factors.append({
            "name": "brain_not_queried",
            "weight": PRE_SEND_WEIGHTS["brain_not_queried"],
            "applied": True,
            "reason": "forge-brain NOT queried (compliance penalty)",
        })
        score += PRE_SEND_WEIGHTS["brain_not_queried"]

    comp_score = complexity_result.get("score", 0) if complexity_result else typed_ir.complexity
    comp_ok = 1 <= comp_score <= 20
    comp_warnings = complexity_result.get("warnings", []) if complexity_result else []
    factors.append({
        "name": "complexity_appropriate",
        "weight": PRE_SEND_WEIGHTS["complexity_appropriate"],
        "applied": comp_ok and not comp_warnings,
        "reason": f"complexity={comp_score}" + (f", warnings: {len(comp_warnings)}" if comp_warnings else ""),
    })
    if comp_ok and not comp_warnings:
        score += PRE_SEND_WEIGHTS["complexity_appropriate"]

    pf_passed = preflight_result.get("passed", True) if preflight_result else True
    pf_checks = preflight_result.get("checks", []) if preflight_result else []
    factors.append({
        "name": "preflight_passed",
        "weight": PRE_SEND_WEIGHTS["preflight_passed"],
        "applied": pf_passed,
        "reason": f"{len(pf_checks)} checks passed" if pf_passed else "preflight failed",
    })
    if pf_passed:
        score += PRE_SEND_WEIGHTS["preflight_passed"]

    dedup_clean = not (dedup_result or {}).get("blocked", False) and (dedup_result or {}).get("overlap_pct", 0) < 0.5
    factors.append({
        "name": "dedup_clean",
        "weight": PRE_SEND_WEIGHTS["dedup_clean"],
        "applied": dedup_clean,
        "reason": f"overlap={dedup_result.get('overlap_pct', 0)}" if dedup_result else "no dedup check",
    })
    if dedup_clean:
        score += PRE_SEND_WEIGHTS["dedup_clean"]

    score = max(0.0, min(1.0, score))
    breakdown = QualityBreakdown(
        pre_send_score=round(score, 3),
        total_score=round(score, 3),
        factors=factors,
        computed_at=datetime.now(timezone.utc).isoformat(),
        phase="pre_send",
    )
    logger.info("Pre-send quality score: %.3f (%d factors applied)",
                score, sum(1 for f in factors if f["applied"]))
    return breakdown


def score_post_outcome(pre_send_breakdown: QualityBreakdown | None,
                       pass_rate: str | None = None,
                       builder_claimed_pass: bool = False,
                       respec_count: int = 0,
                       failure_categories: list[str] | None = None,
                       status: str = "") -> QualityBreakdown:
    """Adjust quality score after task outcome is known."""
    pre_score = pre_send_breakdown.pre_send_score if pre_send_breakdown else 0.35
    pre_factors = pre_send_breakdown.factors if pre_send_breakdown else []
    post_factors = []
    adjustment = 0.0
    categories = failure_categories or []

    passed, total = 0, 0
    if pass_rate:
        try:
            parts = pass_rate.split("/")
            passed = int(parts[0])
            total = int(parts[1])
        except (ValueError, IndexError):
            pass

    if total > 0:
        ratio = passed / total
        if ratio >= 1.0:
            post_factors.append({"name": "qa_full_pass", "weight": POST_OUTCOME_WEIGHTS["qa_full_pass"],
                                  "applied": True, "reason": f"QA {pass_rate} (100% pass)"})
            adjustment += POST_OUTCOME_WEIGHTS["qa_full_pass"]
        elif ratio >= 0.5:
            post_factors.append({"name": "qa_partial_pass", "weight": POST_OUTCOME_WEIGHTS["qa_partial_pass"],
                                  "applied": True, "reason": f"QA {pass_rate} ({ratio:.0%} pass)"})
            adjustment += POST_OUTCOME_WEIGHTS["qa_partial_pass"]
        else:
            post_factors.append({"name": "qa_fail", "weight": POST_OUTCOME_WEIGHTS["qa_fail"],
                                  "applied": True, "reason": f"QA {pass_rate} ({ratio:.0%} pass)"})
            adjustment += POST_OUTCOME_WEIGHTS["qa_fail"]

    if builder_claimed_pass and total > 0 and (passed / total) < 0.5:
        post_factors.append({"name": "hallucinated_pass", "weight": POST_OUTCOME_WEIGHTS["hallucinated_pass"],
                              "applied": True, "reason": f"Builder claimed pass but QA found {pass_rate}"})
        adjustment += POST_OUTCOME_WEIGHTS["hallucinated_pass"]

    if respec_count == 0:
        post_factors.append({"name": "no_respec", "weight": POST_OUTCOME_WEIGHTS["no_respec"],
                              "applied": True, "reason": "completed without respec"})
        adjustment += POST_OUTCOME_WEIGHTS["no_respec"]
    elif respec_count > 0:
        post_factors.append({"name": "respec_required", "weight": POST_OUTCOME_WEIGHTS["respec_required"],
                              "applied": True, "reason": f"{respec_count} respec(s) required"})
        adjustment += POST_OUTCOME_WEIGHTS["respec_required"]

    if "timeout" in categories or status == "failed_timeout":
        post_factors.append({"name": "pipe_timeout", "weight": POST_OUTCOME_WEIGHTS["pipe_timeout"],
                              "applied": True, "reason": "pipe timeout occurred"})
        adjustment += POST_OUTCOME_WEIGHTS["pipe_timeout"]

    if "escalated" in status.lower() or status == "budget_exhausted":
        post_factors.append({"name": "budget_exhausted", "weight": POST_OUTCOME_WEIGHTS["budget_exhausted"],
                              "applied": True, "reason": "retry budget exhausted"})
        adjustment += POST_OUTCOME_WEIGHTS["budget_exhausted"]

    total_score = max(0.0, min(1.0, pre_score + adjustment))
    breakdown = QualityBreakdown(
        pre_send_score=round(pre_score, 3),
        post_outcome_score=round(adjustment, 3),
        total_score=round(total_score, 3),
        factors=pre_factors + post_factors,
        computed_at=datetime.now(timezone.utc).isoformat(),
        phase="post_outcome",
    )
    logger.info("Post-outcome quality score: %.3f (pre=%.3f, adjustment=%+.3f)",
                total_score, pre_score, adjustment)
    return breakdown


def quality_metrics(plan_store) -> dict:
    """Aggregate quality metrics across all scored plans."""
    try:
        plans = plan_store.list_plans(limit=500)
    except Exception:
        return {"error": "could not load plans"}

    scored = [p for p in plans if p.get("quality_score") is not None]
    unscored = [p for p in plans if p.get("quality_score") is None]
    scores = [p["quality_score"] for p in scored]

    if not scores:
        return {"avg_score": None, "median_score": None, "scored_count": 0,
                "unscored_count": len(unscored), "brain_compliance_pct": None,
                "score_distribution": {}}

    scores_sorted = sorted(scores)
    n = len(scores_sorted)
    median = scores_sorted[n // 2] if n % 2 else (scores_sorted[n // 2 - 1] + scores_sorted[n // 2]) / 2

    buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0}
    for s in scores:
        if s < 0.2:       buckets["0.0-0.2"] += 1
        elif s < 0.4:     buckets["0.2-0.4"] += 1
        elif s < 0.6:     buckets["0.4-0.6"] += 1
        elif s < 0.8:     buckets["0.6-0.8"] += 1
        else:             buckets["0.8-1.0"] += 1

    brain_queried_count = sum(1 for p in plans if p.get("brain_queried"))
    brain_compliance = round(brain_queried_count / len(plans), 3) if plans else None

    return {
        "avg_score": round(sum(scores) / len(scores), 3),
        "median_score": round(median, 3),
        "scored_count": len(scored),
        "unscored_count": len(unscored),
        "brain_compliance_pct": brain_compliance,
        "score_distribution": buckets,
    }


# ===========================================================================
# B) CRITERIA VALIDATION + DRIFT DETECTION
# ===========================================================================

@dataclass
class CriterionResult:
    """Validation result for a single criterion."""
    criterion: str = ""
    status: str = "unverified"  # verified, unverified, contradicted
    confidence: float = 0.0
    evidence: str = ""
    matched_words: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Full validation result for a task."""
    task_id: str = ""
    plan_id: str = ""
    criteria_count: int = 0
    verified_count: int = 0
    unverified_count: int = 0
    contradicted_count: int = 0
    verification_rate: float = 0.0
    hallucination_detected: bool = False
    hallucination_reason: str = ""
    criteria_results: list[dict] = field(default_factory=list)
    recommendation: str = ""  # "promote", "fail", "review"
    computed_at: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def validate_criteria(typed_ir_dict: dict, builder_sections: dict,
                      result_text: str = "", task_id: str = "",
                      plan_id: str = "") -> ValidationResult:
    """Validate builder output against typed IR criteria."""
    criteria = typed_ir_dict.get("criteria", [])
    if not criteria:
        return ValidationResult(
            task_id=task_id, plan_id=plan_id,
            recommendation="promote",
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    what_text = builder_sections.get("what", "")
    output_text = builder_sections.get("output", "")
    reasoning_text = builder_sections.get("reasoning", "")
    search_text = f"{what_text}\n{output_text}\n{reasoning_text}\n{result_text}".lower()

    results = [_score_criterion(c, search_text, what_text) for c in criteria]

    verified = sum(1 for r in results if r.status == "verified")
    unverified = sum(1 for r in results if r.status == "unverified")
    contradicted = sum(1 for r in results if r.status == "contradicted")
    total = len(criteria)
    rate = verified / total if total > 0 else 0.0

    if rate >= 0.8 and contradicted == 0:
        recommendation = "promote"
    elif rate >= 0.5:
        recommendation = "review"
    else:
        recommendation = "fail"

    return ValidationResult(
        task_id=task_id, plan_id=plan_id,
        criteria_count=total, verified_count=verified,
        unverified_count=unverified, contradicted_count=contradicted,
        verification_rate=round(rate, 3),
        hallucination_detected=False, hallucination_reason="",
        criteria_results=[asdict(r) for r in results],
        recommendation=recommendation,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


def _score_criterion(criterion: str, search_text: str, what_text: str) -> CriterionResult:
    criterion_lower = criterion.lower()
    keywords = _extract_keywords(criterion_lower)
    if not keywords:
        return CriterionResult(criterion=criterion, status="unverified",
                               confidence=0.0, evidence="no meaningful keywords to match")

    matched = [kw for kw in keywords if kw in search_text]
    match_ratio = len(matched) / len(keywords) if keywords else 0

    negation_patterns = (
        [(r"not\s+" + re.escape(kw), kw) for kw in keywords[:3]] +
        [(r"remov(?:e|ed|ing)\s+" + re.escape(kw), kw) for kw in keywords[:3]] +
        [(r"skip(?:ped|ping)?\s+" + re.escape(kw), kw) for kw in keywords[:3]]
    )
    for pattern, _ in negation_patterns:
        if re.search(pattern, search_text):
            return CriterionResult(criterion=criterion, status="contradicted",
                                   confidence=0.8, evidence="negation pattern found for keyword",
                                   matched_words=matched)

    criterion_paths = _extract_file_paths(criterion_lower)
    if criterion_paths:
        output_paths = _extract_file_paths(search_text)
        for cp in criterion_paths:
            cp_filename = cp.split("/")[-1]
            if cp_filename and any(cp_filename in op for op in output_paths):
                return CriterionResult(criterion=criterion, status="verified",
                                       confidence=0.85,
                                       evidence=f"file path match: {cp_filename} found in builder output",
                                       matched_words=matched)

    if match_ratio >= 0.6:
        status, confidence = "verified", min(1.0, match_ratio)
    else:
        status, confidence = "unverified", match_ratio

    what_lower = what_text.lower()
    if sum(1 for kw in keywords if kw in what_lower) > 0 and status == "unverified" and match_ratio >= 0.4:
        status, confidence = "verified", min(1.0, confidence + 0.2)

    return CriterionResult(criterion=criterion, status=status,
                           confidence=round(confidence, 3),
                           evidence=f"{len(matched)}/{len(keywords)} keywords matched",
                           matched_words=matched)


_GENERIC_WORDS = {
    "should", "must", "will", "that", "this", "with", "from", "have",
    "does", "when", "test", "make", "sure", "each", "also", "only",
    "more", "than", "into", "been", "being", "some", "other", "like",
    "work", "properly", "correctly", "successfully", "without", "error",
}


def _extract_keywords(text: str) -> list[str]:
    words = re.findall(r'\b[a-z]{4,}\b', text)
    return [w for w in words if w not in _GENERIC_WORDS]


def _extract_file_paths(text: str) -> list[str]:
    return [m.lower() for m in re.findall(r'[~/.][\w./\-]+\.\w{1,5}', text)]


def _has_success_language(text: str) -> bool:
    text_lower = text.lower()
    for phrase in ["all checks pass", "all criteria", "build complete"]:
        if phrase in text_lower:
            return True
    for pat in [r'all\s+\d+\s+success\s+criteria', r'all\s+\d+\s+criteria\s+satisfied',
                r'all\s+\d+\s+criteria\s+met', r'\b(\d+)/\1\s+pass']:
        if re.search(pat, text_lower):
            return True
    for m in re.finditer(r'\b(\d+)/(\d+)\b', text_lower):
        num, denom = int(m.group(1)), int(m.group(2))
        if denom > 0 and num == denom:
            return True
    return False


def detect_hallucination(validation: ValidationResult,
                         builder_claimed_pass: bool = False,
                         pass_rate: str | None = None) -> ValidationResult:
    """Check for hallucinated pass claim. Updates validation in place."""
    if builder_claimed_pass and validation.verification_rate < 0.5:
        validation.hallucination_detected = True
        validation.hallucination_reason = (
            f"Builder claimed pass but only {validation.verification_rate:.0%} "
            f"of criteria verified ({validation.verified_count}/{validation.criteria_count})"
        )
        validation.recommendation = "fail"
        logger.warning("HALLUCINATION detected for task %s: %s",
                       validation.task_id, validation.hallucination_reason)

    if pass_rate and builder_claimed_pass:
        try:
            parts = pass_rate.split("/")
            passed, total = int(parts[0]), int(parts[1])
            if total > 0 and passed / total < 0.5:
                validation.hallucination_detected = True
                validation.hallucination_reason = (
                    f"Builder claimed pass but QA found {pass_rate} ({passed/total:.0%} pass rate)"
                )
                validation.recommendation = "fail"
        except (ValueError, IndexError):
            pass

    if validation.contradicted_count > 0:
        if not validation.hallucination_detected:
            validation.hallucination_reason = (
                f"{validation.contradicted_count} criteria contradicted in builder output"
            )
        validation.recommendation = "fail" if validation.contradicted_count > 1 else "review"

    return validation


@dataclass
class DriftResult:
    """Result of comparing criteria between parent and child plans."""
    parent_plan_id: str = ""
    child_plan_id: str = ""
    added_criteria: list[str] = field(default_factory=list)
    removed_criteria: list[str] = field(default_factory=list)
    unchanged_criteria: list[str] = field(default_factory=list)
    scope_expanded: bool = False
    scope_reduced: bool = False
    drift_warning: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def detect_drift(parent_plan: dict, child_plan: dict) -> DriftResult:
    """Compare criteria between parent and child (respec) plans."""
    parent_ir = _parse_ir(parent_plan.get("typed_ir"))
    child_ir = _parse_ir(child_plan.get("typed_ir"))

    parent_criteria = set(parent_ir.get("criteria", []))
    child_criteria = set(child_ir.get("criteria", []))

    unchanged = parent_criteria & child_criteria
    removed = parent_criteria - child_criteria
    added = child_criteria - parent_criteria

    truly_removed = []
    truly_added = list(added)
    for rc in removed:
        rc_kw = set(_extract_keywords(rc.lower()))
        matched = False
        for ac in list(truly_added):
            ac_kw = set(_extract_keywords(ac.lower()))
            if rc_kw and ac_kw:
                overlap = len(rc_kw & ac_kw) / len(rc_kw | ac_kw)
                if overlap > 0.6:
                    unchanged.add(f"{rc} -> {ac}")
                    truly_added.remove(ac)
                    matched = True
                    break
        if not matched:
            truly_removed.append(rc)

    scope_expanded = len(truly_added) > len(truly_removed)
    scope_reduced = len(truly_removed) > len(truly_added)
    warning = ""
    if scope_expanded:
        warning = (f"Scope expanded: {len(truly_added)} criteria added, "
                   f"{len(truly_removed)} removed. Respec should narrow scope, not expand it.")
    elif truly_removed and not truly_added:
        warning = f"{len(truly_removed)} criteria dropped without replacement."

    return DriftResult(
        parent_plan_id=parent_plan.get("plan_id", ""),
        child_plan_id=child_plan.get("plan_id", ""),
        added_criteria=truly_added, removed_criteria=truly_removed,
        unchanged_criteria=sorted(unchanged),
        scope_expanded=scope_expanded, scope_reduced=scope_reduced,
        drift_warning=warning,
    )


def _parse_ir(ir_raw) -> dict:
    if ir_raw is None:
        return {}
    if isinstance(ir_raw, str):
        try:
            return json.loads(ir_raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return ir_raw if isinstance(ir_raw, dict) else {}


def make_verification_decision(validation: ValidationResult,
                               builder_exit_code: int = 0,
                               result_text: str = "") -> str:
    """Decide whether to promote to COMPLETED_VERIFIED or fail.

    Returns "promote", "fail", or "review".
    """
    if validation.hallucination_detected:
        logger.warning("Task %s: hallucination -> FAILED_RETRYABLE", validation.task_id)
        return "fail"

    if validation.criteria_count == 0:
        logger.info("Task %s: no criteria extracted -> promoting by default", validation.task_id)
        return "promote"

    if (builder_exit_code == 0 and _has_success_language(result_text)
            and validation.contradicted_count == 0):
        logger.info("Task %s: clean exit + success language -> auto-promote (keyword rate %.0f%%)",
                    validation.task_id, validation.verification_rate * 100)
        return "promote"

    if validation.criteria_count <= 6 and builder_exit_code == 0 and validation.contradicted_count == 0:
        logger.info("Task %s: %d criteria, exit 0 -> low-criteria auto-promote",
                    validation.task_id, validation.criteria_count)
        return "promote"

    if validation.verification_rate >= 0.50 and validation.contradicted_count == 0:
        logger.info("Task %s: %.0f%% verified -> COMPLETED_VERIFIED",
                    validation.task_id, validation.verification_rate * 100)
        return "promote"

    if validation.verification_rate < 0.5:
        logger.warning("Task %s: only %.0f%% verified -> FAILED_RETRYABLE",
                       validation.task_id, validation.verification_rate * 100)
        return "fail"

    logger.info("Task %s: %.0f%% verified -> manual review needed",
                validation.task_id, validation.verification_rate * 100)
    return "review"
