"""Leroy v2 Phase 8A: Concurrency-Controlled Task Queue.

Replaces unbounded asyncio.create_task() with a managed queue that respects
machine concurrency limits and priority ordering.

Concurrency limits:
  Haze (default): 3 concurrent tasks
  Kush: 1 concurrent task

Priority levels:
  critical = 0 (highest, executes first)
  normal   = 1 (default)
  low      = 2 (backfill)

Tasks enter the queue via enqueue(). A background coroutine (_dispatcher)
picks the highest-priority task when a slot opens and launches it.
"""

import asyncio
import heapq
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

logger = logging.getLogger("leroy-task-queue")


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

class Priority(IntEnum):
    CRITICAL = 0
    NORMAL = 1
    LOW = 2


PRIORITY_MAP = {
    "critical": Priority.CRITICAL,
    "normal": Priority.NORMAL,
    "low": Priority.LOW,
}


# ---------------------------------------------------------------------------
# Concurrency limits per machine
# ---------------------------------------------------------------------------

DEFAULT_CONCURRENCY = {
    "haze": 3,
    "kush": 1,
}

# Override via env: LEROY_MAX_CONCURRENT_HAZE=5, LEROY_MAX_CONCURRENT_KUSH=2
import os
for machine in DEFAULT_CONCURRENCY:
    env_key = f"LEROY_MAX_CONCURRENT_{machine.upper()}"
    env_val = os.environ.get(env_key)
    if env_val:
        try:
            DEFAULT_CONCURRENCY[machine] = int(env_val)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Queue entry
# ---------------------------------------------------------------------------

@dataclass(order=True)
class QueueEntry:
    """A task waiting in the queue. Ordered by (priority, enqueue_time)."""
    priority: int
    enqueue_time: float = field(compare=True)
    task_id: str = field(compare=False)
    spec: str = field(compare=False, repr=False)
    target_machine: str = field(compare=False, default="haze")
    metadata: dict = field(compare=False, default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Task Queue
# ---------------------------------------------------------------------------

class TaskQueue:
    """Concurrency-controlled priority queue for task execution.

    Usage:
        queue = TaskQueue(execute_fn=_execute_task)
        queue.start(loop)   # Start dispatcher coroutine
        queue.enqueue(task_id, spec, priority="normal", target="haze")
    """

    def __init__(self, execute_fn, concurrency: dict | None = None):
        """
        Args:
            execute_fn: Async callable(task_id, spec) that runs the task.
            concurrency: Machine -> max concurrent tasks. Defaults to DEFAULT_CONCURRENCY.
        """
        self._execute_fn = execute_fn
        self._concurrency = concurrency or dict(DEFAULT_CONCURRENCY)
        self._heap: list[QueueEntry] = []
        self._heap_lock = threading.Lock()
        self._active: dict[str, set[str]] = {m: set() for m in self._concurrency}
        self._active_lock = threading.Lock()
        self._wake_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

        # Metrics
        self._enqueued_total = 0
        self._dispatched_total = 0
        self._rejected_total = 0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the dispatcher coroutine on the given event loop."""
        self._loop = loop
        self._wake_event = asyncio.Event()
        self._running = True
        loop.create_task(self._dispatcher())
        logger.info("Task queue started (concurrency: %s)", self._concurrency)

    def stop(self) -> None:
        """Signal dispatcher to stop."""
        self._running = False
        if self._wake_event:
            self._wake_event.set()

    def enqueue(self, task_id: str, spec: str,
                priority: str = "normal",
                target_machine: str = "haze",
                metadata: dict | None = None) -> dict:
        """Add a task to the queue.

        Returns: {queued: bool, position: int, queue_depth: int, target: str}
        """
        pri = PRIORITY_MAP.get(priority, Priority.NORMAL)
        target = target_machine.lower() if target_machine else "haze"

        # Validate target machine
        if target not in self._concurrency:
            # Unknown machine -- default to haze limits
            logger.warning("Unknown target machine '%s' for task %s, defaulting to haze", target, task_id)
            target = "haze"

        entry = QueueEntry(
            priority=pri,
            enqueue_time=time.time(),
            task_id=task_id,
            spec=spec,
            target_machine=target,
            metadata=metadata or {},
        )

        with self._heap_lock:
            heapq.heappush(self._heap, entry)
            depth = len(self._heap)
            self._enqueued_total += 1

        logger.info("Task %s queued (priority=%s, target=%s, depth=%d)",
                     task_id[:8], priority, target, depth)

        # Wake the dispatcher
        if self._wake_event and self._loop:
            self._loop.call_soon_threadsafe(self._wake_event.set)

        return {
            "queued": True,
            "position": depth,
            "queue_depth": depth,
            "target": target,
            "priority": priority,
        }

    def _has_capacity(self, machine: str) -> bool:
        """Check if a machine has capacity for another task."""
        max_concurrent = self._concurrency.get(machine, 1)
        with self._active_lock:
            active = self._active.get(machine, set())
            return len(active) < max_concurrent

    def _claim_slot(self, task_id: str, machine: str) -> bool:
        """Claim an execution slot. Returns True if successful."""
        max_concurrent = self._concurrency.get(machine, 1)
        with self._active_lock:
            active = self._active.setdefault(machine, set())
            if len(active) >= max_concurrent:
                return False
            active.add(task_id)
            return True

    def _release_slot(self, task_id: str, machine: str) -> None:
        """Release an execution slot after task completes."""
        with self._active_lock:
            active = self._active.get(machine, set())
            active.discard(task_id)
        logger.debug("Slot released: %s on %s", task_id[:8], machine)

        # Wake dispatcher to check for queued tasks
        if self._wake_event and self._loop:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def _dispatcher(self) -> None:
        """Background coroutine that dispatches queued tasks when slots open."""
        logger.info("Task queue dispatcher running")
        while self._running:
            dispatched_any = False

            with self._heap_lock:
                # Find tasks that can be dispatched (machine has capacity)
                remaining = []
                to_dispatch = []
                while self._heap:
                    entry = heapq.heappop(self._heap)
                    if self._has_capacity(entry.target_machine):
                        to_dispatch.append(entry)
                    else:
                        remaining.append(entry)

                # Put back entries that couldn't be dispatched
                for r in remaining:
                    heapq.heappush(self._heap, r)

            # Launch tasks outside the lock
            for entry in to_dispatch:
                if self._claim_slot(entry.task_id, entry.target_machine):
                    self._dispatched_total += 1
                    wait_time = time.time() - entry.enqueue_time
                    logger.info("Dispatching task %s (priority=%d, target=%s, waited=%.1fs)",
                                entry.task_id[:8], entry.priority,
                                entry.target_machine, wait_time)
                    asyncio.create_task(
                        self._run_and_release(entry.task_id, entry.spec, entry.target_machine)
                    )
                    dispatched_any = True
                else:
                    # Race condition: slot filled between check and claim
                    with self._heap_lock:
                        heapq.heappush(self._heap, entry)

            if not dispatched_any:
                # Wait for wake signal (new task enqueued or slot released)
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass  # Periodic check even without signal

    async def _run_and_release(self, task_id: str, spec: str, machine: str) -> None:
        """Run a task and release the slot when done."""
        try:
            await self._execute_fn(task_id, spec)
        except Exception as e:
            logger.error("Task %s: execution error in queue wrapper: %s", task_id, e)
        finally:
            self._release_slot(task_id, machine)

    # -------------------------------------------------------------------
    # Status / metrics
    # -------------------------------------------------------------------

    def queue_depth(self) -> int:
        """Current number of tasks waiting in queue."""
        with self._heap_lock:
            return len(self._heap)

    def active_counts(self) -> dict[str, int]:
        """Current active task counts per machine."""
        with self._active_lock:
            return {m: len(tasks) for m, tasks in self._active.items()}

    def capacity(self) -> dict[str, dict]:
        """Current capacity per machine."""
        with self._active_lock:
            return {
                m: {
                    "max": self._concurrency[m],
                    "active": len(self._active.get(m, set())),
                    "available": self._concurrency[m] - len(self._active.get(m, set())),
                }
                for m in self._concurrency
            }

    def metrics(self) -> dict:
        """Queue metrics."""
        return {
            "queue_depth": self.queue_depth(),
            "active": self.active_counts(),
            "capacity": self.capacity(),
            "enqueued_total": self._enqueued_total,
            "dispatched_total": self._dispatched_total,
            "rejected_total": self._rejected_total,
        }

    def queued_tasks(self) -> list[dict]:
        """List tasks currently in the queue (not yet dispatched)."""
        with self._heap_lock:
            return [
                {
                    "task_id": e.task_id,
                    "priority": e.priority,
                    "target_machine": e.target_machine,
                    "enqueue_time": datetime.fromtimestamp(e.enqueue_time, tz=timezone.utc).isoformat(),
                    "wait_seconds": round(time.time() - e.enqueue_time, 1),
                }
                for e in sorted(self._heap)
            ]
