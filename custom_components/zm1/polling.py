"""Adaptive polling policy for zM1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdaptivePollingPolicy:
    """Track UDP reliability and choose the next polling interval."""

    configured_interval: int
    min_interval: int
    max_interval: int
    recovery_successes: int = 3
    _interval: int = 0
    _failures: int = 0
    _successes_after_failure: int = 0

    def __post_init__(self) -> None:
        self.configured_interval = int(self.configured_interval)
        self.min_interval = int(self.min_interval)
        self.max_interval = int(self.max_interval)
        if self.max_interval < self.min_interval:
            self.max_interval = self.min_interval
        if self.recovery_successes < 1:
            self.recovery_successes = 1
        self._interval = self.base_interval

    @property
    def base_interval(self) -> int:
        """Return the clamped configured polling interval."""
        return max(self.min_interval, min(self.configured_interval, self.max_interval))

    @property
    def interval(self) -> int:
        """Return the currently selected polling interval."""
        return self._interval

    @property
    def failures(self) -> int:
        """Return consecutive failed update attempts."""
        return self._failures

    def record_success(self) -> int:
        """Record a successful update and return the next interval."""
        if self._failures == 0:
            self._interval = self.base_interval
            return self._interval

        self._successes_after_failure += 1
        if self._successes_after_failure >= self.recovery_successes:
            self._failures = 0
            self._successes_after_failure = 0
            self._interval = self.base_interval
        return self._interval

    def record_failure(self) -> int:
        """Record a failed update and return the backed-off interval."""
        self._failures += 1
        self._successes_after_failure = 0
        multiplier = 2 ** min(self._failures, 4)
        self._interval = min(
            self.max_interval, max(self.base_interval, self.base_interval * multiplier)
        )
        return self._interval
