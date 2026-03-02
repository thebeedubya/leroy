"""Test suite for Leroy PersistenceManager.

Run with:
    cd /Users/brad.wood/Projects/leroy/server && python3 test_persist_manager.py

Tests are standalone -- they do NOT interact with the running Leroy server.
Each test uses temp files to avoid polluting real queue/log files.
"""

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Ensure we can import from server/ without a running server
# ---------------------------------------------------------------------------
SERVER_DIR = Path(__file__).parent
sys.path.insert(0, str(SERVER_DIR))

import config  # noqa: E402  (must come after path insert)
import persist_manager  # noqa: E402

# ---------------------------------------------------------------------------
# Test result tracking
# ---------------------------------------------------------------------------

RESULTS: list[dict] = []

def record(name: str, status: str, reason: str = ""):
    label = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    RESULTS.append({"name": name, "status": label, "reason": reason})
    tag = f"[{label}]"
    msg = f"{tag} {name}"
    if reason:
        msg += f" -- {reason}"
    print(msg)


def assert_true(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _forge_brain_reachable() -> bool:
    """Quick HTTP check against the health endpoint."""
    import httpx
    try:
        r = httpx.get("http://192.168.1.100:8301/", timeout=5.0)
        return True  # any HTTP response = server up
    except Exception:
        return False


def _make_fake_task_meta(task_id: str = "test-task-001") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": "completed",
        "result": "Task completed successfully. All acceptance criteria passed.",
        "spec": "# Test Spec\nThis is a test spec for the persistence manager test suite.",
        "created_at": now,
        "completed_at": now,
    }


def _make_manager_with_temp_files(url: str, tmp_dir: Path) -> tuple:
    """Return (manager, queue_path, log_path) all pointed at tmp_dir."""
    queue_path = tmp_dir / "test-queue.json"
    log_path = tmp_dir / "test-log.json"
    return queue_path, log_path


# ---------------------------------------------------------------------------
# TEST 1 -- Happy path: persist_task() succeeds when brain is up
# ---------------------------------------------------------------------------

def test_01_happy_path():
    name = "TEST 1 - Happy Path"

    if not _forge_brain_reachable():
        record(name, "SKIP", "forge-brain not reachable at http://192.168.1.100:8301/")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            # Patch module-level globals so this manager uses temp files
            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                mgr = persist_manager.PersistenceManager()
                # Use real URL and token from config
                mgr._url = config.FORGE_BRAIN_URL
                mgr._token = config.FORGE_BRAIN_TOKEN

                task_id = "test-happy-001"
                mgr.persist_task(task_id, _make_fake_task_meta(task_id))

                # Verify log entry written
                assert_true(log_path.exists(), "Log file was not created")
                log_entries = json.loads(log_path.read_text())
                assert_true(len(log_entries) > 0, "Log file is empty")

                last = log_entries[-1]
                assert_true(last["task_id"] == task_id, f"task_id mismatch: {last.get('task_id')}")
                assert_true(last["success"] is True, f"Expected success=True, got: {last.get('success')}, error: {last.get('error')}")

                # Verify queue stays empty
                queue_depth = 0
                if queue_path.exists():
                    data = json.loads(queue_path.read_text())
                    queue_depth = len(data.get("entries", []))
                assert_true(queue_depth == 0, f"Expected empty queue, got depth={queue_depth}")

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {e}")


# ---------------------------------------------------------------------------
# TEST 2 -- Failure path: brain unreachable, entry queued
# ---------------------------------------------------------------------------

def test_02_failure_path():
    name = "TEST 2 - Failure Path (brain unreachable)"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                mgr = persist_manager.PersistenceManager()
                mgr._url = "http://127.0.0.1:19999/sse"  # bogus URL
                mgr._token = "fake-token"

                task_id = "test-fail-001"
                mgr.persist_task(task_id, _make_fake_task_meta(task_id))

                # Verify entry was added to queue
                assert_true(queue_path.exists(), "Queue file was not created")
                data = json.loads(queue_path.read_text())
                entries = data.get("entries", [])
                assert_true(len(entries) == 1, f"Expected 1 queue entry, got {len(entries)}")
                assert_true(entries[0]["task_id"] == task_id, "Queue entry task_id mismatch")

                # Verify log entry written with success=False and queued=True
                assert_true(log_path.exists(), "Log file was not created")
                log_entries = json.loads(log_path.read_text())
                assert_true(len(log_entries) > 0, "Log file is empty")

                last = log_entries[-1]
                assert_true(last["task_id"] == task_id, f"task_id mismatch: {last.get('task_id')}")
                assert_true(last["success"] is False, f"Expected success=False, got: {last.get('success')}")
                assert_true(last.get("queued") is True, f"Expected queued=True, got: {last.get('queued')}")

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {e}")
        # tmp_dir is automatically cleaned up by context manager


# ---------------------------------------------------------------------------
# TEST 3 -- Retry path: flush queued entry when brain comes back
# ---------------------------------------------------------------------------

def test_03_retry_flush():
    name = "TEST 3 - Retry Path (flush from queue)"

    if not _forge_brain_reachable():
        record(name, "SKIP", "forge-brain not reachable at http://192.168.1.100:8301/")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            # Pre-populate queue with one entry
            task_id = "test-retry-001"
            now = datetime.now(timezone.utc).isoformat()
            from uuid import uuid4
            fake_entry = {
                "id": uuid4().hex,
                "created_at": now,
                "attempt_count": 1,
                "last_attempt": now,
                "task_id": task_id,
                "content": (
                    "Leroy completed task test-retry-001 - Test Retry Entry at " + now + ".\n\n"
                    "Status: completed\nDuration: 42s\nSource: leroy/haze\n\n"
                    "=== SPEC SUMMARY ===\nTest spec for retry path test.\n\n"
                    "=== RESULT ===\nTest result for retry path test.\n\n"
                    "=== METADATA ===\nTask ID: test-retry-001\n"
                    "Created: " + now + "\nCompleted: " + now + "\n"
                    "Spec length: 100 chars\nResult length: 100 chars\n\n"
                    "=== SYSTEM CONTEXT ===\n"
                    "This record was generated automatically by the Leroy A2A server test suite. "
                    "It is a synthetic entry used to verify that the retry/flush path works correctly. "
                    "The persist manager should pick this up from the queue file and push it to forge-brain. "
                    "If this appears in forge-brain it was written by test_persist_manager.py TEST 3."
                ),
                "session_title": "Leroy Task: Test Retry Entry",
                "session_tags": ["leroy", "task-completion", "engineering", "completed", "test"],
                "source": "leroy/haze",
            }
            queue_data = {"version": "1.0", "entries": [fake_entry]}
            queue_path.write_text(json.dumps(queue_data, indent=2))

            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                mgr = persist_manager.PersistenceManager()
                mgr._url = config.FORGE_BRAIN_URL
                mgr._token = config.FORGE_BRAIN_TOKEN

                # Call _flush_queue directly
                mgr._flush_queue()

                # Verify queue is now empty
                data = json.loads(queue_path.read_text())
                remaining = data.get("entries", [])
                assert_true(len(remaining) == 0, f"Expected empty queue after flush, got {len(remaining)} entries")

                # Verify log shows success=True with flushed_from_queue=True
                assert_true(log_path.exists(), "Log file was not created")
                log_entries = json.loads(log_path.read_text())
                assert_true(len(log_entries) > 0, "Log file is empty after flush")

                last = log_entries[-1]
                assert_true(last["success"] is True, f"Expected success=True after flush, got: {last.get('success')}, error: {last.get('error')}")
                assert_true(last.get("flushed_from_queue") is True, f"Expected flushed_from_queue=True, got: {last.get('flushed_from_queue')}")

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {e}")


# ---------------------------------------------------------------------------
# TEST 4 -- Background thread alive after start()
# ---------------------------------------------------------------------------

def test_04_background_thread():
    name = "TEST 4 - Background thread alive"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                mgr = persist_manager.PersistenceManager()
                # Use bogus URL so start() doesn't actually try to flush to brain
                mgr._url = "http://127.0.0.1:19999/sse"
                mgr._token = "fake-token"

                mgr.start()

                # Give thread a moment to spin up
                time.sleep(0.1)

                assert_true(
                    mgr._retry_thread is not None,
                    "_retry_thread is None after start()"
                )
                assert_true(
                    mgr._retry_thread.is_alive(),
                    "_retry_thread is not alive after start()"
                )

                mgr.stop()

                # Thread is daemon, so it won't block -- just verify stop_event was set
                assert_true(
                    mgr._stop_event.is_set(),
                    "_stop_event not set after stop()"
                )

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {e}")


# ---------------------------------------------------------------------------
# TEST 5 -- Queue depth alert: oldest entry dropped when at MAX_QUEUE_DEPTH
# ---------------------------------------------------------------------------

def test_05_queue_depth_cap():
    name = "TEST 5 - Queue depth alert (full queue)"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                mgr = persist_manager.PersistenceManager()
                mgr._url = "http://127.0.0.1:19999/sse"
                mgr._token = "fake-token"

                # Fill queue to MAX_QUEUE_DEPTH
                max_depth = persist_manager.MAX_QUEUE_DEPTH  # 100
                entries = []
                from uuid import uuid4
                for i in range(max_depth):
                    entries.append({
                        "id": f"fill-{i:04d}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "attempt_count": 0,
                        "last_attempt": None,
                        "task_id": f"fill-task-{i:04d}",
                        "content": "x" * 1500,
                        "session_title": f"Fill entry {i}",
                        "session_tags": [],
                        "source": "leroy/haze",
                    })
                queue_data = {"version": "1.0", "entries": entries}
                queue_path.write_text(json.dumps(queue_data, indent=2))

                # Verify pre-condition
                pre_data = json.loads(queue_path.read_text())
                assert_true(len(pre_data["entries"]) == max_depth, "Pre-condition failed: queue not at max depth")

                # Add one more entry via _enqueue
                overflow_entry = {
                    "id": "overflow-entry",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "attempt_count": 0,
                    "last_attempt": None,
                    "task_id": "overflow-task",
                    "content": "y" * 1500,
                    "session_title": "Overflow entry",
                    "session_tags": [],
                    "source": "leroy/haze",
                }
                mgr._enqueue(overflow_entry)

                # Queue should still be at MAX_QUEUE_DEPTH
                post_data = json.loads(queue_path.read_text())
                post_entries = post_data.get("entries", [])
                assert_true(
                    len(post_entries) == max_depth,
                    f"Expected queue depth={max_depth} after overflow, got {len(post_entries)}"
                )

                # Oldest entry (fill-0000) should have been dropped
                ids = [e["id"] for e in post_entries]
                assert_true(
                    "fill-0000" not in ids,
                    "Expected oldest entry (fill-0000) to be dropped, but it is still present"
                )

                # Overflow entry should be present
                assert_true(
                    "overflow-entry" in ids,
                    "Expected overflow entry to be present, but it is missing"
                )

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {e}")


# ---------------------------------------------------------------------------
# TEST 6 -- Corrupted queue file: _load_queue() returns empty list gracefully
# ---------------------------------------------------------------------------

def test_06_corrupted_queue():
    name = "TEST 6 - Corrupted queue file"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        queue_path = tmp_dir / "queue.json"
        log_path = tmp_dir / "log.json"

        try:
            # Write invalid JSON to the queue file
            queue_path.write_text("{this is not valid json [[[")

            with patch.object(persist_manager, "QUEUE_FILE", queue_path), \
                 patch.object(persist_manager, "LOG_FILE", log_path), \
                 patch.object(persist_manager, "LOGS_DIR", tmp_dir):

                # _load_queue should return empty list without raising
                result = persist_manager._load_queue()

                assert_true(
                    isinstance(result, list),
                    f"Expected list from _load_queue(), got {type(result)}"
                )
                assert_true(
                    len(result) == 0,
                    f"Expected empty list from corrupted queue, got {len(result)} entries"
                )

            record(name, "PASS")

        except AssertionError as e:
            record(name, "FAIL", str(e))
        except Exception as e:
            record(name, "FAIL", f"Unexpected exception: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Leroy PersistenceManager Test Suite")
    print("=" * 60)

    # Check brain reachability once and report
    brain_up = _forge_brain_reachable()
    print(f"forge-brain reachable: {brain_up} (http://192.168.1.100:8301/)")
    print()

    test_01_happy_path()
    test_02_failure_path()
    test_03_retry_flush()
    test_04_background_thread()
    test_05_queue_depth_cap()
    test_06_corrupted_queue()

    print()
    print("=" * 60)
    print("Results:")
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")

    for r in RESULTS:
        tag = f"[{r['status']}]"
        line = f"  {tag} {r['name']}"
        if r["reason"]:
            line += f" -- {r['reason']}"
        print(line)

    print()
    print(f"  {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
