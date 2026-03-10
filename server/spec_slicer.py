"""spec_slicer.py — Pure-function module for slicing large specs into VehicleSpec objects.

Phase 2 of the Dispatcher implementation (IC-4: TypedIR Slicing).
No side effects, no network calls, no database writes.
Input: spec_text + TypedIR -> Output: list[VehicleSpec]

Called by the dispatcher when complexity > 15 OR chars > 5000.
Builder never knows it's operating inside a container.
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Add mcp/ to sys.path so spec_analyzer is importable without package name collision
# (mirrors the pattern used in mcp/leroy_client.py)
_LEROY_ROOT = Path(__file__).parent.parent
_MCP_DIR = str(_LEROY_ROOT / "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from spec_analyzer import TypedIR, _extract_files  # noqa: E402  # type: ignore[import]

logger = logging.getLogger("leroy-spec-slicer")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VehicleSpec:
    """A single builder-sized unit of work sliced from a larger spec."""
    vehicle_index: int              # 0-based position in sequence
    section_title: str              # e.g. "Change 1: Success-Language Heuristic"
    spec_text: str                  # full markdown for this vehicle (includes inherited context)
    criteria: list[str] = field(default_factory=list)       # scoped criteria for this vehicle
    global_criteria: list[str] = field(default_factory=list)  # do_not_do items inherited from parent
    files: list[str] = field(default_factory=list)          # file paths mentioned in this vehicle's section
    depends_on: list[int] = field(default_factory=list)     # vehicle_indexes this depends on (empty = ready)
    parallelizable: bool = False    # True if can run alongside siblings with no deps


# ---------------------------------------------------------------------------
# Section boundary detection
# ---------------------------------------------------------------------------

_PRIMARY_HEADER = re.compile(
    r"^##\s+(?:(?:Change|Phase|Step|Part|Deliverable)\s+\d+[:\s]|\d+[.:])",
    re.IGNORECASE,
)

_SECONDARY_HEADER = re.compile(r"^###\s+.+")


def _find_primary_sections(spec_text: str) -> list[tuple[str, str]]:
    """Find ## Change/Phase/Step/Part/Deliverable N: or ## N. sections.

    Returns list of (title, body) tuples. Title is header text without leading ##.
    Stops collecting body for a section when any ## header is encountered
    (prevents Success Criteria / Do Not Do sections from polluting section bodies).
    """
    lines = spec_text.split("\n")
    sections: list[tuple[str, str]] = []
    current_header: str | None = None
    body_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Any level-2 header (##) is a boundary
        is_h2 = bool(re.match(r"^##\s", stripped))
        if is_h2:
            if _PRIMARY_HEADER.match(stripped):
                # Save current section, start new primary section
                if current_header is not None:
                    sections.append((current_header, "\n".join(body_lines).strip()))
                current_header = re.sub(r"^##\s*", "", stripped)
                body_lines = []
            else:
                # Non-primary ## header: close current section if open
                if current_header is not None:
                    sections.append((current_header, "\n".join(body_lines).strip()))
                    current_header = None
                    body_lines = []
        else:
            if current_header is not None:
                body_lines.append(line)

    if current_header is not None:
        sections.append((current_header, "\n".join(body_lines).strip()))

    return sections


def _find_secondary_sections(spec_text: str) -> list[tuple[str, str]]:
    """Find ### headers as section boundaries."""
    lines = spec_text.split("\n")
    sections: list[tuple[str, str]] = []
    current_header: str | None = None
    body_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _SECONDARY_HEADER.match(stripped):
            if current_header is not None:
                sections.append((current_header, "\n".join(body_lines).strip()))
            current_header = re.sub(r"^###\s*", "", stripped)
            body_lines = []
        else:
            if current_header is not None:
                body_lines.append(line)

    if current_header is not None:
        sections.append((current_header, "\n".join(body_lines).strip()))

    return sections


def _find_numbered_list_sections(spec_text: str) -> list[tuple[str, str]]:
    """Find numbered list items under 'Changes Required' or 'Execution' header."""
    for header_name in ("Changes Required", "Execution", "Changes"):
        body = _extract_named_section(spec_text, header_name)
        if not body:
            continue

        lines = body.split("\n")
        sections: list[tuple[str, str]] = []
        current_header: str | None = None
        body_lines: list[str] = []

        for line in lines:
            m = re.match(r"^\s*(\d+)[.)]\s+(.*)", line)
            if m:
                if current_header is not None:
                    sections.append((current_header, "\n".join(body_lines).strip()))
                current_header = m.group(2).strip()
                body_lines = []
            elif current_header is not None:
                body_lines.append(line)

        if current_header is not None:
            sections.append((current_header, "\n".join(body_lines).strip()))

        if len(sections) >= 2:
            return sections

    return []


# ---------------------------------------------------------------------------
# Spec section extraction
# ---------------------------------------------------------------------------

def _extract_named_section(text: str, header: str) -> str:
    """Extract body text of a named section (returns empty string if not found)."""
    pattern = rf"(?:^|\n)#+\s*{re.escape(header)}\s*\n([\s\S]*?)(?=\n#+\s|\Z)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_do_not_do_block(spec_text: str) -> str:
    """Return the Do Not Do section as a formatted markdown block."""
    for name in ("Do Not Do", "DO NOT DO", "Do not do"):
        pattern = rf"(?:^|\n)(#+\s*{re.escape(name)}\s*\n[\s\S]*?)(?=\n#+\s|\Z)"
        m = re.search(pattern, spec_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _extract_objective_block(spec_text: str) -> str:
    """Return first 200 words of the Objective section as markdown."""
    for name in ("Objective", "Overview", "Summary"):
        body = _extract_named_section(spec_text, name)
        if body:
            words = body.split()[:200]
            return f"## {name}\n\n{' '.join(words)}"
    return ""


def _extract_target_line(spec_text: str) -> str:
    """Return the ## Target: line if present."""
    m = re.search(r"(##\s*Target:\s*\w+)", spec_text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Criteria mapping
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of",
    "and", "or", "but", "with", "from", "that", "this", "be", "are",
    "was", "were", "will", "has", "have", "had", "do", "does", "did",
    "not", "no", "by", "as", "if", "so", "up", "out", "all", "any",
    "each", "when", "then", "than", "into", "can", "should", "must",
    "may", "its", "per", "via", "after", "before", "only", "also",
})


def _keywords(text: str) -> set[str]:
    """Extract meaningful lowercase keywords from text."""
    words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _criterion_belongs_to(
    criterion: str,
    section_title: str,
    section_body: str,
    section_files: list[str],
) -> bool:
    """Return True if criterion belongs to this section.

    Three signals (any one sufficient):
    1. Numbered reference: "Change N" in criterion matches "Change N" in title
    2. File overlap: criterion mentions a file also in the section
    3. Keyword overlap: strong keyword match between criterion and section title/body
    """
    # Signal 1: numbered reference
    m_title = re.search(r"\b(Change|Phase|Step|Part|Deliverable)\s+(\d+)\b", section_title, re.IGNORECASE)
    m_crit = re.search(r"\b(Change|Phase|Step|Part|Deliverable)\s+(\d+)\b", criterion, re.IGNORECASE)
    if m_title and m_crit:
        if (m_title.group(1).lower() == m_crit.group(1).lower()
                and m_title.group(2) == m_crit.group(2)):
            return True

    # Signal 2: file overlap
    crit_files = set(_extract_files(criterion))
    if crit_files and set(section_files) and (crit_files & set(section_files)):
        return True

    # Signal 3: keyword overlap
    crit_kw = _keywords(criterion)
    title_kw = _keywords(section_title)
    body_kw = _keywords(section_body[:600])   # limit body search

    title_hit = len(crit_kw & title_kw)
    body_hit = len(crit_kw & body_kw)

    # Require either 2+ title hits OR (1 title hit + 2+ body hits)
    if title_hit >= 2:
        return True
    if title_hit >= 1 and body_hit >= 2:
        return True

    return False


# ---------------------------------------------------------------------------
# Vehicle spec_text construction
# ---------------------------------------------------------------------------

def _build_spec_text(
    section_title: str,
    section_body: str,
    vehicle_index: int,
    total_vehicles: int,
    objective_block: str,
    do_not_do_block: str,
    target_line: str,
) -> str:
    """Assemble full spec_text for a vehicle including inherited context."""
    parts: list[str] = []

    # PRINT NOW directive (required by builder protocol)
    parts.append("## PRINT NOW\nPrint 'VEHICLE START' immediately and after each major step.\n")

    # Target header
    if target_line:
        parts.append(f"{target_line}\n")

    # Vehicle context note
    parts.append(
        f"**Note: This is vehicle {vehicle_index + 1} of {total_vehicles}. "
        f"Focus ONLY on this section. Do not implement other sections.**\n"
    )

    # Objective context (inherited from parent)
    if objective_block:
        parts.append(objective_block + "\n")

    # This vehicle's section content
    parts.append(f"## {section_title}\n\n{section_body}\n")

    # Do Not Do (inherited from parent, full)
    if do_not_do_block:
        parts.append(do_not_do_block + "\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def slice_spec(spec_text: str, typed_ir: TypedIR) -> list[VehicleSpec]:
    """Slice a large spec into builder-sized VehicleSpec objects.

    Returns an empty list in two cases:
    - Spec is below threshold (caller should pass through as single task)
    - Fewer than 2 sections found (fail-open: pass through as single task)

    Otherwise returns a list of VehicleSpec objects (at least 2).
    Each vehicle has non-empty criteria (IC-4 zero-criteria validation).
    """
    # --- Step 1: Check thresholds ---
    if typed_ir.complexity <= 15 and len(spec_text) <= 5000:
        logger.debug(
            "Spec below threshold (complexity=%d, chars=%d) -- pass-through",
            typed_ir.complexity, len(spec_text),
        )
        return []

    # --- Step 2: Find section boundaries ---
    sections = _find_primary_sections(spec_text)

    if len(sections) < 2:
        logger.debug(
            "Primary sections: %d, trying secondary (### headers)", len(sections)
        )
        sections = _find_secondary_sections(spec_text)

    if len(sections) < 2:
        logger.debug(
            "Secondary sections: %d, trying numbered list items", len(sections)
        )
        sections = _find_numbered_list_sections(spec_text)

    if len(sections) < 2:
        logger.debug("Could not find 2+ sections -- failing open (pass-through)")
        return []

    # --- Step 3: Extract files per section and map criteria ---
    section_files: list[list[str]] = [
        _extract_files(f"## {title}\n\n{body}") for title, body in sections
    ]

    n = len(sections)
    mapped: list[list[str]] = [[] for _ in range(n)]
    unmapped: list[str] = []

    for criterion in typed_ir.criteria:
        assigned = False
        for i, (title, body) in enumerate(sections):
            if _criterion_belongs_to(criterion, title, body, section_files[i]):
                mapped[i].append(criterion)
                assigned = True
                break  # first match wins
        if not assigned:
            unmapped.append(criterion)

    # --- Step 4: Zero-criteria validation (IC-4) ---

    # First: distribute unmapped criteria to sections that have none
    remaining_unmapped = list(unmapped)
    for i in range(n):
        if len(mapped[i]) == 0 and remaining_unmapped:
            mapped[i].extend(remaining_unmapped)
            remaining_unmapped = []
            break

    # Second: merge any still-empty sections into adjacent
    i = 0
    while i < len(sections):
        if len(mapped[i]) == 0:
            if i + 1 < len(sections):
                # Merge forward: i absorbs i+1
                logger.warning(
                    "Section '%s' has 0 criteria -- merging with next section '%s'",
                    sections[i][0], sections[i + 1][0],
                )
                merged_title = f"{sections[i][0]} + {sections[i + 1][0]}"
                merged_body = f"{sections[i][1]}\n\n{sections[i + 1][1]}"
                merged_files = sorted(set(section_files[i] + section_files[i + 1]))
                merged_crit = mapped[i] + mapped[i + 1]
                sections = sections[:i] + [(merged_title, merged_body)] + sections[i + 2:]
                mapped = mapped[:i] + [merged_crit] + mapped[i + 2:]
                section_files = section_files[:i] + [merged_files] + section_files[i + 2:]
                n = len(sections)
                # Re-check position i (now holds the merged section)
            elif i > 0:
                # Last section, merge backward into i-1
                logger.warning(
                    "Section '%s' has 0 criteria -- merging with previous section '%s'",
                    sections[i][0], sections[i - 1][0],
                )
                merged_title = f"{sections[i - 1][0]} + {sections[i][0]}"
                merged_body = f"{sections[i - 1][1]}\n\n{sections[i][1]}"
                merged_files = sorted(set(section_files[i - 1] + section_files[i]))
                merged_crit = mapped[i - 1] + mapped[i]
                sections = sections[:i - 1] + [(merged_title, merged_body)] + sections[i + 1:]
                mapped = mapped[:i - 1] + [merged_crit] + mapped[i + 1:]
                section_files = section_files[:i - 1] + [merged_files] + section_files[i + 1:]
                n = len(sections)
                i = max(0, i - 1)
            else:
                # Lone section with 0 criteria: give it all remaining unmapped
                logger.warning(
                    "Section '%s' has 0 criteria and no adjacent section -- assigning remaining unmapped",
                    sections[i][0],
                )
                mapped[i].extend(remaining_unmapped)
                remaining_unmapped = []
                i += 1
        else:
            i += 1

    # --- Step 5: Build VehicleSpec objects ---
    objective_block = _extract_objective_block(spec_text)
    do_not_do_block = _extract_do_not_do_block(spec_text)
    target_line = _extract_target_line(spec_text)
    global_criteria = list(typed_ir.do_not_do)
    total = len(sections)

    vehicles: list[VehicleSpec] = []
    for idx, (title, body) in enumerate(sections):
        vspec_text = _build_spec_text(
            section_title=title,
            section_body=body,
            vehicle_index=idx,
            total_vehicles=total,
            objective_block=objective_block,
            do_not_do_block=do_not_do_block,
            target_line=target_line,
        )
        vehicles.append(VehicleSpec(
            vehicle_index=idx,
            section_title=title,
            spec_text=vspec_text,
            criteria=mapped[idx],
            global_criteria=global_criteria,
            files=section_files[idx],
            depends_on=[idx - 1] if idx > 0 else [],   # default: sequential
            parallelizable=False,
        ))

    # --- Step 6: Parallelization classification ---
    # Conservative: only parallelize when file sets are non-empty AND disjoint
    for i in range(len(vehicles)):
        for j in range(i + 1, len(vehicles)):
            fi = set(vehicles[i].files)
            fj = set(vehicles[j].files)
            if fi and fj and not (fi & fj):
                vehicles[i].parallelizable = True
                vehicles[j].parallelizable = True
                # Remove the sequential dependency between i and j
                vehicles[i].depends_on = [d for d in vehicles[i].depends_on if d != j]
                vehicles[j].depends_on = [d for d in vehicles[j].depends_on if d != i]

    # --- Step 7: Pre-emit validation ---
    valid_indexes = set(range(len(vehicles)))
    for v in vehicles:
        if len(v.criteria) == 0:
            logger.error(
                "Vehicle %d '%s' still has 0 criteria after all merges -- "
                "this indicates a spec with criteria that don't map to any section. "
                "Check criteria and section structure.",
                v.vehicle_index, v.section_title,
            )
        assert v.spec_text, f"Vehicle {v.vehicle_index} spec_text is empty"
        assert v.vehicle_index == len([x for x in vehicles if x.vehicle_index < v.vehicle_index]), \
            f"Vehicle index {v.vehicle_index} is out of sequence"
        # Sanitize depends_on to only reference valid indexes
        v.depends_on = [d for d in v.depends_on if d in valid_indexes]

    return vehicles


def estimate_vehicle_complexity(vehicle: VehicleSpec) -> int:
    """Estimate complexity score for a vehicle.

    Formula mirrors TypedIR: len(criteria) * 2 + len(endpoints) * 3 + len(files)
    Endpoints are extracted from vehicle.spec_text using same regex as spec_analyzer.
    """
    endpoint_set: set[str] = set()
    for m in re.finditer(r"(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s]+)", vehicle.spec_text):
        endpoint_set.add(m.group(0))
    for m in re.finditer(r"/api/[^\s'\"`,)}\\]>]+", vehicle.spec_text):
        endpoint_set.add(m.group(0))

    return len(vehicle.criteria) * 2 + len(endpoint_set) * 3 + len(vehicle.files)
