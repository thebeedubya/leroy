"""Leroy v2 Phase 10: Recursive Improvement Engine.

Analyzes accumulated plan data to learn patterns and improve system behavior.

Components:
  10A: Pattern analysis -- correlations between spec attributes and outcomes
  10B: Learned thresholds -- retry budgets per category, complexity warnings, quality weights
  10C: Golden spec templates -- subsystems with 3+ clean passes generate templates
  10D: Proactive suggestions -- recommendations surfaced to PM
  10E: v1 vs v2 baseline comparison

All analysis is read-only against plan_store. No mutations to existing data.
Suggestions are returned as structured dicts, not executed.
"""

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

logger = logging.getLogger("leroy-improvement-engine")


# ---------------------------------------------------------------------------
# 10A: Pattern Analysis
# ---------------------------------------------------------------------------

def analyze_patterns(plan_store) -> dict:
    """Analyze correlations between spec attributes and outcomes.

    Returns:
        {
            failure_correlations: [{attribute, value, failure_rate, sample_size}],
            success_correlations: [{attribute, value, success_rate, sample_size}],
            timeout_correlations: [{attribute, value, timeout_rate, sample_size}],
            respec_correlations: [{attribute, value, respec_rate, sample_size}],
        }
    """
    plans = plan_store.list_plans(limit=500, source="v2")
    if len(plans) < 5:
        return {"error": "insufficient data", "plan_count": len(plans)}

    failure_corr = []
    success_corr = []
    timeout_corr = []
    respec_corr = []

    # Group by attributes and compute outcome rates
    for attr, extractor in _ATTRIBUTE_EXTRACTORS.items():
        buckets = defaultdict(list)
        for p in plans:
            val = extractor(p)
            if val is not None:
                buckets[val].append(p)

        for val, subset in buckets.items():
            if len(subset) < 3:  # Minimum sample size
                continue

            n = len(subset)
            failed = sum(1 for p in subset if p.get("status") == "failed")
            completed = sum(1 for p in subset if p.get("outcome") in ("verified", "completed", "completed_unverified"))
            timeouts = sum(1 for p in subset if _is_timeout(p))
            respecced = sum(1 for p in subset if (p.get("respec_count") or 0) > 0)

            fail_rate = round(failed / n, 3)
            success_rate = round(completed / n, 3)
            timeout_rate = round(timeouts / n, 3)
            respec_rate = round(respecced / n, 3)

            entry = {"attribute": attr, "value": str(val), "sample_size": n}

            if fail_rate > 0.4:
                failure_corr.append({**entry, "failure_rate": fail_rate})
            if success_rate > 0.8:
                success_corr.append({**entry, "success_rate": success_rate})
            if timeout_rate > 0.2:
                timeout_corr.append({**entry, "timeout_rate": timeout_rate})
            if respec_rate > 0.3:
                respec_corr.append({**entry, "respec_rate": respec_rate})

    return {
        "failure_correlations": sorted(failure_corr, key=lambda x: -x.get("failure_rate", 0)),
        "success_correlations": sorted(success_corr, key=lambda x: -x.get("success_rate", 0)),
        "timeout_correlations": sorted(timeout_corr, key=lambda x: -x.get("timeout_rate", 0)),
        "respec_correlations": sorted(respec_corr, key=lambda x: -x.get("respec_rate", 0)),
        "plan_count": len(plans),
    }


def _is_timeout(plan: dict) -> bool:
    cats = plan.get("failure_categories", "")
    if isinstance(cats, str) and cats:
        return "TIMEOUT" in cats.upper()
    return False


def _complexity_bucket(plan: dict) -> str | None:
    score = plan.get("complexity_score")
    if score is None:
        return None
    if score <= 5:
        return "low (0-5)"
    elif score <= 15:
        return "medium (6-15)"
    elif score <= 25:
        return "high (16-25)"
    else:
        return "very_high (26+)"


def _criteria_bucket(plan: dict) -> str | None:
    count = plan.get("criteria_count")
    if count is None:
        return None
    if count <= 2:
        return "few (1-2)"
    elif count <= 5:
        return "moderate (3-5)"
    elif count <= 8:
        return "many (6-8)"
    else:
        return "excessive (9+)"


_ATTRIBUTE_EXTRACTORS = {
    "subsystem": lambda p: p.get("subsystem"),
    "target_machine": lambda p: p.get("target_machine"),
    "complexity_bucket": _complexity_bucket,
    "criteria_bucket": _criteria_bucket,
    "brain_queried": lambda p: "yes" if p.get("brain_queried") else "no",
    "preflight_passed": lambda p: "yes" if p.get("preflight_passed") else "no",
}


# ---------------------------------------------------------------------------
# 10B: Learned Thresholds
# ---------------------------------------------------------------------------

@dataclass
class LearnedThresholds:
    """Thresholds derived from outcome data."""
    retry_budget_by_category: dict = field(default_factory=dict)
    complexity_warning_level: int = 20
    quality_weight_adjustments: dict = field(default_factory=dict)
    sample_size: int = 0
    computed_at: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def learn_thresholds(plan_store) -> LearnedThresholds:
    """Compute optimal thresholds from historical plan data.

    Returns LearnedThresholds with data-driven values.
    """
    plans = plan_store.list_plans(limit=500, source="v2")
    thresholds = LearnedThresholds(
        sample_size=len(plans),
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    if len(plans) < 10:
        return thresholds

    # Retry budget by failure category
    category_attempts = defaultdict(list)
    for p in plans:
        cats_raw = p.get("failure_categories", "")
        retry = p.get("retry_count", 0) or 0
        if cats_raw:
            cats = json.loads(cats_raw) if isinstance(cats_raw, str) else cats_raw
            if isinstance(cats, list):
                for cat in cats:
                    category_attempts[cat].append(retry)

    for cat, attempts in category_attempts.items():
        if len(attempts) >= 3:
            avg = sum(attempts) / len(attempts)
            # Suggest budget = avg attempts * 1.5, capped at 5
            suggested = min(5, max(1, round(avg * 1.5)))
            thresholds.retry_budget_by_category[cat] = {
                "suggested_budget": suggested,
                "avg_attempts": round(avg, 1),
                "sample_size": len(attempts),
            }

    # Complexity warning threshold
    # Find the complexity score above which failure rate spikes
    complexity_outcomes = []
    for p in plans:
        score = p.get("complexity_score")
        if score is not None:
            succeeded = p.get("outcome") in ("verified", "completed", "completed_unverified")
            complexity_outcomes.append((score, succeeded))

    if len(complexity_outcomes) >= 10:
        complexity_outcomes.sort()
        # Find inflection point: where success rate drops below 60%
        for threshold in range(10, 40, 5):
            above = [s for c, s in complexity_outcomes if c > threshold]
            if len(above) >= 3 and sum(above) / len(above) < 0.6:
                thresholds.complexity_warning_level = threshold
                break

    # Quality weight adjustments based on factor-outcome correlations
    scored_plans = [p for p in plans if p.get("quality_score") is not None]
    if len(scored_plans) >= 10:
        high_q = [p for p in scored_plans if p["quality_score"] >= 0.6]
        low_q = [p for p in scored_plans if p["quality_score"] < 0.4]

        high_success = sum(1 for p in high_q if p.get("outcome") in ("verified", "completed")) / max(1, len(high_q))
        low_success = sum(1 for p in low_q if p.get("outcome") in ("verified", "completed")) / max(1, len(low_q))

        thresholds.quality_weight_adjustments = {
            "high_quality_success_rate": round(high_success, 3),
            "low_quality_success_rate": round(low_success, 3),
            "quality_predictive": high_success > low_success + 0.15,
            "recommendation": "quality scoring is predictive" if high_success > low_success + 0.15
                             else "quality scoring needs weight tuning",
        }

    return thresholds


# ---------------------------------------------------------------------------
# 10C: Golden Spec Templates
# ---------------------------------------------------------------------------

def find_golden_templates(plan_store) -> list[dict]:
    """Find subsystems with 3+ clean passes and extract template patterns.

    Returns: [{subsystem, clean_pass_count, template_patterns}]
    """
    plans = plan_store.list_plans(limit=500, source="v2")

    # Group completed plans by subsystem
    by_subsystem = defaultdict(list)
    for p in plans:
        sub = p.get("subsystem")
        if not sub:
            continue
        outcome = p.get("outcome", p.get("status", ""))
        if outcome in ("verified", "completed", "completed_unverified"):
            respec = (p.get("respec_count") or 0)
            pass_rate = p.get("pass_rate", "")
            # "Clean" = no respec AND full QA pass (or no QA yet)
            is_clean = respec == 0 and (not pass_rate or _is_full_pass(pass_rate))
            if is_clean:
                by_subsystem[sub].append(p)

    templates = []
    for sub, clean_plans in by_subsystem.items():
        if len(clean_plans) < 3:
            continue

        # Extract common patterns from clean specs
        criteria_counts = [p.get("criteria_count", 0) for p in clean_plans]
        complexities = [p.get("complexity_score", 0) for p in clean_plans]
        targets = Counter(p.get("target_machine", "haze") for p in clean_plans)
        brain_queried_pct = sum(1 for p in clean_plans if p.get("brain_queried")) / len(clean_plans)

        # Extract common criteria patterns from typed_ir
        common_criteria = _extract_common_criteria(clean_plans)

        templates.append({
            "subsystem": sub,
            "clean_pass_count": len(clean_plans),
            "template_patterns": {
                "avg_criteria_count": round(sum(criteria_counts) / len(criteria_counts), 1),
                "avg_complexity": round(sum(complexities) / len(complexities), 1),
                "preferred_target": targets.most_common(1)[0][0] if targets else "haze",
                "brain_queried_pct": round(brain_queried_pct, 2),
                "common_criteria_patterns": common_criteria[:5],
            },
        })

    return sorted(templates, key=lambda t: -t["clean_pass_count"])


def _is_full_pass(pass_rate: str) -> bool:
    try:
        parts = pass_rate.split("/")
        return int(parts[0]) == int(parts[1])
    except (ValueError, IndexError):
        return False


def _extract_common_criteria(plans: list[dict]) -> list[str]:
    """Extract frequently recurring criteria phrases from plan typed_ir."""
    word_freq = Counter()
    for p in plans:
        ir_raw = p.get("typed_ir")
        if not ir_raw:
            continue
        try:
            ir = json.loads(ir_raw) if isinstance(ir_raw, str) else ir_raw
        except (json.JSONDecodeError, TypeError):
            continue
        criteria = ir.get("criteria", [])
        for c in criteria:
            # Extract action verbs and key phrases
            words = re.findall(r'\b\w{4,}\b', c.lower())
            for w in words:
                word_freq[w] += 1

    # Return most common meaningful words (skip generic ones)
    generic = {"should", "must", "will", "that", "this", "with", "from", "have", "does", "when", "test"}
    return [w for w, _ in word_freq.most_common(20) if w not in generic][:10]


# ---------------------------------------------------------------------------
# 10D: Proactive Suggestions
# ---------------------------------------------------------------------------

def generate_suggestions(plan_store) -> list[dict]:
    """Generate improvement suggestions based on accumulated data.

    Returns: [{category, severity, suggestion, evidence, action}]
    """
    plans = plan_store.list_plans(limit=500, source="v2")
    suggestions = []

    if len(plans) < 5:
        return [{"category": "data", "severity": "info",
                 "suggestion": f"Only {len(plans)} plans. Need 10+ for meaningful analysis.",
                 "evidence": None, "action": None}]

    total = len(plans)
    failed = [p for p in plans if p.get("status") == "failed"]
    timeouts = [p for p in plans if _is_timeout(p)]
    respecced = [p for p in plans if (p.get("respec_count") or 0) > 0]
    no_brain = [p for p in plans if not p.get("brain_queried")]

    # Timeout rate
    timeout_rate = len(timeouts) / total
    if timeout_rate > 0.15:
        suggestions.append({
            "category": "timeout",
            "severity": "warning",
            "suggestion": f"Timeout rate is {timeout_rate:.0%} ({len(timeouts)}/{total}). "
                         "Consider breaking large specs into phases or adding stdout frequency warnings.",
            "evidence": {"timeout_count": len(timeouts), "total": total, "rate": round(timeout_rate, 3)},
            "action": "Add 'Execution: produce stdout every 60 seconds' to specs over complexity 15.",
        })

    # Respec rate
    respec_rate = len(respecced) / total
    if respec_rate > 0.25:
        suggestions.append({
            "category": "respec",
            "severity": "warning",
            "suggestion": f"Respec rate is {respec_rate:.0%} ({len(respecced)}/{total}). "
                         "Specs may be under-specified or over-scoped.",
            "evidence": {"respec_count": len(respecced), "total": total, "rate": round(respec_rate, 3)},
            "action": "Ensure all specs have 3+ specific success criteria and a do-not-do section.",
        })

    # Brain compliance
    brain_skip_rate = len(no_brain) / total
    if brain_skip_rate > 0.2:
        suggestions.append({
            "category": "brain_compliance",
            "severity": "warning",
            "suggestion": f"Brain query skipped on {brain_skip_rate:.0%} of specs ({len(no_brain)}/{total}). "
                         "Missing lessons leads to repeated mistakes.",
            "evidence": {"skipped": len(no_brain), "total": total, "rate": round(brain_skip_rate, 3)},
            "action": "Enforce brain query on all specs. Use BRAIN_OVERRIDE only for emergencies.",
        })

    # Failure by subsystem
    sub_failures = defaultdict(lambda: {"total": 0, "failed": 0})
    for p in plans:
        sub = p.get("subsystem", "unknown")
        sub_failures[sub]["total"] += 1
        if p.get("status") == "failed":
            sub_failures[sub]["failed"] += 1

    for sub, counts in sub_failures.items():
        if counts["total"] >= 3 and counts["failed"] / counts["total"] > 0.4:
            suggestions.append({
                "category": "subsystem_risk",
                "severity": "alert",
                "suggestion": f"Subsystem '{sub}' has {counts['failed']}/{counts['total']} failure rate. "
                             "Review specs targeting this subsystem for common failure patterns.",
                "evidence": counts,
                "action": f"Run pattern analysis on '{sub}' specs. Consider golden template if clean passes exist.",
            })

    # Quality score trend (if enough scored plans)
    scored = [(p.get("created_at", ""), p.get("quality_score")) for p in plans if p.get("quality_score") is not None]
    scored.sort()
    if len(scored) >= 10:
        first_half = [s for _, s in scored[:len(scored)//2]]
        second_half = [s for _, s in scored[len(scored)//2:]]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        if avg_second > avg_first + 0.05:
            suggestions.append({
                "category": "quality_trend",
                "severity": "positive",
                "suggestion": f"Quality scores improving: {avg_first:.2f} -> {avg_second:.2f}. "
                             "Spec quality discipline is working.",
                "evidence": {"early_avg": round(avg_first, 3), "recent_avg": round(avg_second, 3)},
                "action": None,
            })
        elif avg_second < avg_first - 0.05:
            suggestions.append({
                "category": "quality_trend",
                "severity": "warning",
                "suggestion": f"Quality scores declining: {avg_first:.2f} -> {avg_second:.2f}. "
                             "Review recent specs for regression in quality discipline.",
                "evidence": {"early_avg": round(avg_first, 3), "recent_avg": round(avg_second, 3)},
                "action": "Review recent spec retrospectives. Identify pattern causing quality drop.",
            })

    return suggestions


# ---------------------------------------------------------------------------
# 10E: v1 vs v2 Baseline Comparison
# ---------------------------------------------------------------------------

def baseline_comparison(plan_store) -> dict:
    """Compare v1 imported plans vs v2 plans.

    Returns:
        {v1: {stats}, v2: {stats}, improvements: [{metric, v1_value, v2_value, change}]}
    """
    # Use existing plan_report for base stats
    report = plan_store.plan_report()
    v1 = report.get("v1_import", {})
    v2 = report.get("v2", {})

    improvements = []

    # Compare key metrics
    for metric, better_direction in [
        ("timeout_count", "lower"),
        ("respec_count", "lower"),
        ("brain_queried", "higher"),
        ("brain_persisted", "higher"),
    ]:
        v1_total = v1.get("total", 0) or 1
        v2_total = v2.get("total", 0) or 1
        v1_val = v1.get(metric, 0) or 0
        v2_val = v2.get(metric, 0) or 0

        v1_rate = round(v1_val / v1_total, 3)
        v2_rate = round(v2_val / v2_total, 3)

        if better_direction == "lower":
            improved = v2_rate < v1_rate
            change = round(v1_rate - v2_rate, 3)
        else:
            improved = v2_rate > v1_rate
            change = round(v2_rate - v1_rate, 3)

        improvements.append({
            "metric": metric,
            "v1_rate": v1_rate,
            "v2_rate": v2_rate,
            "change": change,
            "improved": improved,
            "direction": better_direction,
        })

    # Cost comparison
    v1_avg_cost = v1.get("avg_cost_usd", 0) or 0
    v2_avg_cost = v2.get("avg_cost_usd", 0) or 0
    if v1_avg_cost > 0:
        cost_change = round((v2_avg_cost - v1_avg_cost) / v1_avg_cost, 3)
        improvements.append({
            "metric": "avg_cost_usd",
            "v1_rate": v1_avg_cost,
            "v2_rate": v2_avg_cost,
            "change": cost_change,
            "improved": v2_avg_cost < v1_avg_cost,
            "direction": "lower",
        })

    return {
        "v1": v1,
        "v2": v2,
        "improvements": improvements,
        "v2_plan_count": v2.get("total", 0),
        "v1_plan_count": v1.get("total", 0),
    }


# ---------------------------------------------------------------------------
# Unified analysis endpoint
# ---------------------------------------------------------------------------

def full_analysis(plan_store) -> dict:
    """Run all analysis components and return unified report."""
    return {
        "patterns": analyze_patterns(plan_store),
        "thresholds": learn_thresholds(plan_store).to_json(),
        "golden_templates": find_golden_templates(plan_store),
        "suggestions": generate_suggestions(plan_store),
        "baseline": baseline_comparison(plan_store),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
