"""Bound SDK/auth wall time without an unbounded executor queue.

Timed-out calls cannot be killed safely in Python. At most two daemon workers
remain occupied; further work fails immediately until one returns. Workers may
return data, but must never mutate a repository cache after a caller times out.
"""
from concurrent.futures import Future
from threading import BoundedSemaphore, Thread
from typing import Callable, TypeVar

T = TypeVar("T")


class BoundedIO:
    def __init__(self, workers: int = 2):
        self._slots = BoundedSemaphore(workers)

    def call(self, operation: Callable[[], T], timeout: float) -> T:
        if not self._slots.acquire(blocking=False):
            raise TimeoutError("Remote storage workers are occupied")
        future: Future[T] = Future()

        def run():
            try:
                future.set_result(operation())
            except BaseException as error:
                future.set_exception(error)
            finally:
                self._slots.release()

        try:
            Thread(target=run, daemon=True, name="snapshot-storage").start()
        except BaseException:
            self._slots.release()
            raise
        return future.result(timeout=timeout)


# One budget per API process, shared by all repositories and endpoints.
remote_io = BoundedIO()
