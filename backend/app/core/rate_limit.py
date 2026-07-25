from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class FailureState:
    failures: int
    blocked_until: float
    last_seen: float


class LoginAttemptLimiter:
    """单进程登录失败退避器；生产多实例部署应替换为 Redis 实现。"""

    def __init__(
        self,
        *,
        free_failures: int = 3,
        max_delay_seconds: int = 60,
        retention_seconds: int = 900,
    ) -> None:
        self.free_failures = free_failures
        self.max_delay_seconds = max_delay_seconds
        self.retention_seconds = retention_seconds
        self._states: dict[str, FailureState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(ip_address: str, account: str) -> tuple[str, str]:
        return f"ip:{ip_address}", f"account:{account}"

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, state in self._states.items()
            if now - state.last_seen > self.retention_seconds
        ]
        for key in stale:
            self._states.pop(key, None)

    def retry_after(self, ip_address: str, account: str) -> int:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            waits = [
                max(0.0, self._states.get(key, FailureState(0, 0, now)).blocked_until - now)
                for key in self._keys(ip_address, account)
            ]
        wait = max(waits)
        return max(1, int(wait + 0.999)) if wait > 0 else 0

    def record_failure(self, ip_address: str, account: str) -> int:
        now = time.monotonic()
        max_delay = 0
        with self._lock:
            self._prune(now)
            for key in self._keys(ip_address, account):
                previous = self._states.get(key, FailureState(0, 0, now))
                failures = previous.failures + 1
                exponent = max(0, failures - self.free_failures)
                delay = min(2 ** (exponent - 1), self.max_delay_seconds) if exponent else 0
                self._states[key] = FailureState(
                    failures=failures,
                    blocked_until=now + delay,
                    last_seen=now,
                )
                max_delay = max(max_delay, delay)
        return max_delay

    def record_success(self, ip_address: str, account: str) -> None:
        with self._lock:
            for key in self._keys(ip_address, account):
                self._states.pop(key, None)


login_attempt_limiter = LoginAttemptLimiter()
