"""Leroy v2 Spec Analyzer — Typed IR extraction, dedup, complexity, and pre-flight checks.

Parses spec text into a structured TypedIR record before execution.
Gates spec quality with dedup detection, complexity warnings, and
infrastructure pre-flight checks.
"""

import re
import time
import socket
import logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("leroy-spec-analyzer")


@dataclass
class TypedIR:
    """Structured intermediate representation extracted from spec text."""
    criteria: list[str] = field(default_factory=list)
    target: str | None = None           # "kush", "haze", or None
    files: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    subsystem: str | None = None        # "dashboard", "server", "mcp", "monitor", etc.
    complexity: int = 0                 # criteria * 2 + endpoints * 3 + len(files)
    timeout_override: int | None = None # from spec metadata (minutes)
    dependencies: list[str] = field(default_factory=list)
    do_not_do: list[str] = field(default_factory=list)
    max_retries: int | None = None

    def to_json(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_section(text: str, header: str) -> list[str]:
    """Extract bullet/numbered items from a named section."""
    pattern = rf"(?:^|\n)#+\s*{re.escape(header)}\s*\n([\s\S]*?)(?=\n#+\s|\Z)"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return []
    block = match.group(1)
    items = []
    for line in block.splitlines():
        stripped = line.strip()
        # Match numbered items (1. ...) or bullets (- ..., * ...)
        m = re.match(r"^(?:\d+[.)]\s*|[-*]\s+)(.*)", stripped)
        if m:
            item = m.group(1).strip()
            if item:
                items.append(item)
    return items


def _extract_target(text: str) -> str | None:
    """Extract target machine from spec."""
    # Check for Target: header
    m = re.search(r"(?:^|\n)#+\s*Target:\s*(\w+)", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # Check for inline "Target: kush" or "target: haze"
    m = re.search(r"\btarget:\s*(\w+)", text, re.IGNORECASE)
    if m:
        target = m.group(1).lower()
        if target in ("kush", "haze", "runtz"):
            return target
    # Infer from SSH mentions
    if "kush.local" in text.lower() or "bradwood@kush" in text.lower():
        return "kush"
    return None


def _extract_files(text: str) -> list[str]:
    """Extract file paths mentioned in spec."""
    paths = set()
    for m in re.finditer(r'(?:~/|\.\/|/)[^\s\'"`,)}\]>]+\.\w+', text):
        paths.add(m.group(0))
    return sorted(paths)


def _extract_endpoints(text: str) -> list[str]:
    """Extract API endpoints mentioned in spec."""
    endpoints = set()
    for m in re.finditer(r'(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s]+)', text):
        endpoints.add(m.group(0))
    for m in re.finditer(r'/api/[^\s\'"`,)}\]>]+', text):
        endpoints.add(m.group(0))
    return sorted(endpoints)


def _infer_subsystem(files: list[str], text: str) -> str | None:
    """Infer subsystem from file paths and keywords."""
    text_lower = text.lower()
    for f in files:
        if "dashboard" in f:
            return "dashboard"
        if "server/" in f or "server.py" in f:
            return "server"
        if "mcp/" in f:
            return "mcp"
        if "monitor/" in f:
            return "monitor"
    if "dashboard" in text_lower:
        return "dashboard"
    if "server.py" in text_lower or "a2a server" in text_lower:
        return "server"
    if "mcp tool" in text_lower or "mcp client" in text_lower:
        return "mcp"
    return None


def _extract_dependencies(text: str) -> list[str]:
    """Extract infrastructure dependencies."""
    deps = []
    text_lower = text.lower()
    if "postgres" in text_lower or "psql" in text_lower:
        deps.append("postgres")
    if "qdrant" in text_lower:
        deps.append("qdrant")
    if "forge-brain" in text_lower or "aianna" in text_lower:
        deps.append("forge-brain")
    if "neo4j" in text_lower:
        deps.append("neo4j")
    if "redis" in text_lower:
        deps.append("redis")
    return deps


def _extract_metadata(text: str) -> dict:
    """Extract frontmatter-style metadata."""
    meta = {}
    m = re.search(r"inactivity_timeout:\s*(\d+)", text)
    if m:
        meta["timeout_override"] = int(m.group(1))
    m = re.search(r"max_retries:\s*(\d+)", text)
    if m:
        meta["max_retries"] = int(m.group(1))
    return meta


def extract_typed_ir(spec_text: str, subject: str = "") -> TypedIR:
    """Parse spec text into a TypedIR record."""
    criteria = _extract_section(spec_text, "Success Criteria")
    if not criteria:
        criteria = _extract_section(spec_text, "Criteria")
    do_not_do = _extract_section(spec_text, "Do Not Do")
    if not do_not_do:
        do_not_do = _extract_section(spec_text, "DO NOT DO")
    target = _extract_target(spec_text)
    files = _extract_files(spec_text)
    endpoints = _extract_endpoints(spec_text)
    subsystem = _infer_subsystem(files, spec_text)
    dependencies = _extract_dependencies(spec_text)
    metadata = _extract_metadata(spec_text)

    complexity = len(criteria) * 2 + len(endpoints) * 3 + len(files)

    return TypedIR(
        criteria=criteria,
        target=target,
        files=files,
        endpoints=endpoints,
        subsystem=subsystem,
        complexity=complexity,
        timeout_override=metadata.get("timeout_override"),
        dependencies=dependencies,
        do_not_do=do_not_do,
        max_retries=metadata.get("max_retries"),
    )


# ---------------------------------------------------------------------------
# Dedup check
# ---------------------------------------------------------------------------

def _word_overlap(a: str, b: str) -> float:
    """Simple word-level Jaccard similarity."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _criteria_overlap(criteria_a: list[str], criteria_b: list[str]) -> float:
    """Jaccard similarity on criteria lists (by word overlap)."""
    if not criteria_a or not criteria_b:
        return 0.0
    joined_a = " ".join(criteria_a).lower()
    joined_b = " ".join(criteria_b).lower()
    return _word_overlap(joined_a, joined_b)


def check_dedup(typed_ir: TypedIR, subject: str,
                active_tasks: list[dict], recent_tasks: list[dict]) -> dict:
    """Check for overlapping specs.

    Returns: {blocked: bool, overlap_pct: float, overlapping_task_id: str|None, message: str}
    """
    best_overlap = 0.0
    best_task_id = None
    best_source = None

    for task in active_tasks:
        task_subject = task.get("subject", task.get("spec", "")[:100])
        overlap = _word_overlap(subject, task_subject)
        if overlap > best_overlap:
            best_overlap = overlap
            best_task_id = task.get("task_id")
            best_source = "active"

    for task in recent_tasks:
        task_subject = task.get("subject", task.get("spec", "")[:100])
        overlap = _word_overlap(subject, task_subject)
        if overlap > best_overlap:
            best_overlap = overlap
            best_task_id = task.get("task_id")
            best_source = "recent"

    if best_overlap > 0.7 and best_source == "active":
        return {
            "blocked": True,
            "overlap_pct": round(best_overlap, 2),
            "overlapping_task_id": best_task_id,
            "message": f"Spec overlaps {int(best_overlap*100)}% with active task {best_task_id}",
        }
    elif best_overlap > 0.7 and best_source == "recent":
        return {
            "blocked": False,
            "overlap_pct": round(best_overlap, 2),
            "overlapping_task_id": best_task_id,
            "message": f"WARNING: Spec overlaps {int(best_overlap*100)}% with recently completed task {best_task_id}",
        }
    return {
        "blocked": False,
        "overlap_pct": round(best_overlap, 2),
        "overlapping_task_id": None,
        "message": "",
    }


# ---------------------------------------------------------------------------
# Complexity analysis
# ---------------------------------------------------------------------------

def check_complexity(typed_ir: TypedIR, spec_text: str) -> dict:
    """Analyze spec complexity.

    Returns: {score: int, warnings: list[str], blocked: bool}
    """
    warnings = []
    if len(typed_ir.criteria) > 6:
        warnings.append(f"High criteria count: {len(typed_ir.criteria)} (threshold: 6)")
    if len(spec_text) > 8000:
        warnings.append(f"Spec is {len(spec_text)} chars (threshold: 8000)")
    if typed_ir.complexity > 20:
        warnings.append(f"High complexity score: {typed_ir.complexity} (threshold: 20)")
    if not typed_ir.target and ("kush" in spec_text.lower() or "postgres" in spec_text.lower()):
        warnings.append("Spec mentions kush/postgres but no target machine declared")

    return {
        "score": typed_ir.complexity,
        "warnings": warnings,
        "blocked": False,  # complexity never blocks in Phase 2, just warns
    }


# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

_preflight_cache: dict[str, tuple[bool, float]] = {}  # key -> (result, timestamp)
_PREFLIGHT_CACHE_TTL = 300  # 5 minutes


def _check_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is reachable."""
    cache_key = f"{host}:{port}"
    cached = _preflight_cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _PREFLIGHT_CACHE_TTL:
        return cached[0]

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        up = result == 0
    except (socket.error, OSError):
        up = False

    _preflight_cache[cache_key] = (up, time.time())
    return up


def check_preflight(typed_ir: TypedIR) -> dict:
    """Verify target is reachable and dependencies are available.

    Returns: {passed: bool, checks: list[dict], blocked: bool}
    """
    checks = []

    if typed_ir.target == "kush":
        reachable = _check_port("kush.local", 22)
        checks.append({"name": "kush SSH", "host": "kush.local", "port": 22, "up": reachable})

    for dep in typed_ir.dependencies:
        if dep == "postgres":
            up = _check_port("kush.local", 5432)
            checks.append({"name": "PostgreSQL", "host": "kush.local", "port": 5432, "up": up})
        elif dep == "qdrant":
            up = _check_port("kush.local", 6333)
            checks.append({"name": "Qdrant", "host": "kush.local", "port": 6333, "up": up})
        elif dep == "neo4j":
            up = _check_port("kush.local", 7687)
            checks.append({"name": "Neo4j", "host": "kush.local", "port": 7687, "up": up})
        elif dep == "forge-brain":
            up = _check_port("kush.local", 8300)
            checks.append({"name": "forge-brain", "host": "kush.local", "port": 8300, "up": up})

    all_up = all(c["up"] for c in checks) if checks else True

    return {
        "passed": all_up,
        "checks": checks,
        "blocked": not all_up,
    }
