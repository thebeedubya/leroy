"""One-time migration of v1 spec files into the plans database.

Scans ~/Projects/leroy/specs/*.md for YAML frontmatter and imports
records into the plans table with source='v1_import'.

Usage:
    python migrate_v1_specs.py [--dry-run]
"""

import re
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).parent
sys.path.insert(0, str(SERVER_DIR))

import task_db


def _parse_frontmatter(content: str) -> dict:
    """Parse simple YAML front matter (key: value lines)."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = content[3:end].strip()
    result = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def migrate(dry_run: bool = False) -> dict:
    """Import v1 spec files into plans table.

    Returns: {complete: int, incomplete: int, skipped: int, errors: list[str]}
    """
    specs_dir = Path.home() / "Projects" / "leroy" / "specs"
    if not specs_dir.exists():
        return {"complete": 0, "incomplete": 0, "skipped": 0, "errors": ["specs dir not found"]}

    if not dry_run:
        task_db.init()
        store = task_db.plan_store

    stats = {"complete": 0, "incomplete": 0, "skipped": 0, "errors": []}

    for spec_file in sorted(specs_dir.glob("*.md")):
        if spec_file.name == ".gitkeep":
            continue
        try:
            content = spec_file.read_text(encoding="utf-8")
        except Exception as e:
            stats["errors"].append(f"{spec_file.name}: read error: {e}")
            continue

        fm = _parse_frontmatter(content)
        task_id = fm.get("task_id", "").strip()
        subject = fm.get("spec_id", spec_file.stem)
        created_at = fm.get("date", "")
        status = fm.get("status", "")
        pass_rate = fm.get("pass_rate", "")
        retro = fm.get("retrospective", "")

        # Skip minimal records (no task_id)
        if not task_id or task_id == "(pending)":
            stats["skipped"] += 1
            continue

        # Determine completeness
        has_pass_rate = pass_rate and pass_rate != "(pending)"
        has_retro = retro and retro != "(pending)"

        if has_pass_rate and has_retro:
            outcome = "verified"
            stats["complete"] += 1
        else:
            outcome = "unknown"
            stats["incomplete"] += 1

        # Strip frontmatter to get raw spec text
        end = content.find("\n---", 3)
        spec_text = content[end + 4:].strip() if end != -1 else content

        if not dry_run:
            plan_id = store.create_plan(
                spec_text=spec_text,
                subject=subject,
                source="v1_import",
                outcome=outcome,
            )
            store.link_task(plan_id, task_id)
            if has_pass_rate:
                store.update_outcome(
                    plan_id,
                    status=status or "completed",
                    pass_rate=pass_rate,
                    retro_text=retro if has_retro else None,
                )

    return stats


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("DRY RUN -- no database writes")
    result = migrate(dry_run=dry)
    print(f"\nMigration summary:")
    print(f"  Complete (task_id + pass_rate + retro): {result['complete']}")
    print(f"  Incomplete (task_id but missing data): {result['incomplete']}")
    print(f"  Skipped (no task_id):                  {result['skipped']}")
    if result["errors"]:
        print(f"  Errors: {len(result['errors'])}")
        for e in result["errors"]:
            print(f"    - {e}")
