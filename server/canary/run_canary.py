#!/usr/bin/env python3
"""Leroy v2 Canary Test Runner.

Runs all phase canary assertion modules in order.
Reports pass/fail per assertion. Exits non-zero if any assertion fails.

Usage:
    python server/canary/run_canary.py
"""

import importlib
import sys
import time
from pathlib import Path

# Add server dir to path for imports
SERVER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SERVER_DIR))


def discover_phases() -> list[str]:
    """Find all phase assertion modules in order."""
    canary_dir = Path(__file__).parent
    modules = sorted(canary_dir.glob("phase*_assertions.py"))
    return [m.stem for m in modules]


def run_canary() -> bool:
    """Run all canary phases. Returns True if all pass."""
    phases = discover_phases()
    if not phases:
        print("WARNING: No canary assertion modules found!")
        return False

    total_pass = 0
    total_fail = 0
    total_time = 0.0

    for phase_module_name in phases:
        print(f"\n{'='*60}")
        print(f"  CANARY: {phase_module_name}")
        print(f"{'='*60}")

        try:
            module = importlib.import_module(f"canary.{phase_module_name}")
        except Exception as e:
            print(f"  IMPORT ERROR: {e}")
            total_fail += 1
            continue

        assertions = getattr(module, "ASSERTIONS", [])
        if not assertions:
            print(f"  WARNING: No ASSERTIONS list found in {phase_module_name}")
            continue

        for assertion in assertions:
            name = assertion.get("name", "unnamed")
            test_fn = assertion.get("test")
            if not test_fn:
                print(f"  SKIP: {name} (no test function)")
                continue

            start = time.time()
            try:
                test_fn()
                elapsed = time.time() - start
                total_time += elapsed
                print(f"  PASS: {name} ({elapsed:.3f}s)")
                total_pass += 1
            except Exception as e:
                elapsed = time.time() - start
                total_time += elapsed
                print(f"  FAIL: {name} ({elapsed:.3f}s)")
                print(f"        {e}")
                total_fail += 1

    print(f"\n{'='*60}")
    print(f"  RESULTS: {total_pass} passed, {total_fail} failed ({total_time:.3f}s)")
    print(f"{'='*60}")

    return total_fail == 0


if __name__ == "__main__":
    success = run_canary()
    sys.exit(0 if success else 1)
