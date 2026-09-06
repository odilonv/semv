"""Adaptive rate limiter for Mistral API.

Implements:
- Token bucket algorithm respecting free tier limits (1 RPS, 500k TPM)
- Adaptive throttling based on X-RateLimit-Remaining headers
- Exponential backoff with jitter on 429 errors
- Circuit breaker: pauses after consecutive failures

Usage:
    from semv.rate_limiter import RateLimiter, with_retry

    limiter = RateLimiter()
    await limiter.acquire()  # blocks until a slot is available
    
    # Or use the retry decorator for sync functions:
    result = with_retry(lambda: api_call(), limiter=limiter)
"""

import asyncio
import random
import time
import threading
from dataclasses import dataclass, field

from semv.logger import get_logger

logger = get_logger("rate_limiter")


@dataclass
class RateLimiterConfig:
    """Configuration for the rate limiter."""
    requests_per_second: float = 1.0    # Free tier: ~1 RPS
    tokens_per_minute: int = 500_000    # Free tier: ~500k TPM
    max_retries: int = 10               # Max retry attempts on 429
    base_backoff: float = 2.0           # Base for exponential backoff
    max_backoff: float = 120.0          # Maximum backoff in seconds
    circuit_breaker_threshold: int = 5  # Consecutive failures before circuit break
    circuit_breaker_cooldown: float = 60.0  # Seconds to wait during circuit break


class RateLimiter:
    """Adaptive rate limiter with circuit breaker.

    Thread-safe. Works with both sync and async code paths.
    """

    def __init__(self, config: RateLimiterConfig | None = None):
        self.config = config or RateLimiterConfig()
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None  # Lazy-initialized on first async use

        # Token bucket state
        self._last_request_time: float = 0.0
        self._min_interval: float = 1.0 / self.config.requests_per_second

        # Adaptive state (updated from API headers)
        self._remaining_requests: int | None = None
        self._remaining_tokens: int | None = None
        self._reset_time: float | None = None

        # Circuit breaker state
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0

        # Stats
        self._total_requests: int = 0
        self._total_retries: int = 0
        self._total_throttled: float = 0.0  # Total seconds spent throttled

    def update_from_headers(self, headers: dict):
        """Update rate limiter state from Mistral API response headers.

        Expected headers:
        - x-ratelimit-remaining-requests
        - x-ratelimit-remaining-tokens
        - x-ratelimit-reset (seconds until reset)
        """
        with self._lock:
            if "x-ratelimit-remaining-requests" in headers:
                self._remaining_requests = int(headers["x-ratelimit-remaining-requests"])
            if "x-ratelimit-remaining-tokens" in headers:
                self._remaining_tokens = int(headers["x-ratelimit-remaining-tokens"])
            if "x-ratelimit-reset" in headers:
                self._reset_time = time.monotonic() + float(headers["x-ratelimit-reset"])

            # Adaptive slow-down when quota is low
            if self._remaining_requests is not None and self._remaining_requests < 5:
                logger.debug(
                    "Low quota: %d requests remaining, slowing down",
                    self._remaining_requests,
                )
                self._min_interval = max(self._min_interval, 2.0)
            elif self._remaining_requests is not None and self._remaining_requests > 20:
                # Reset to normal speed
                self._min_interval = 1.0 / self.config.requests_per_second

    def record_success(self):
        """Record a successful API call. Resets circuit breaker."""
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self):
        """Record a failed API call. May trigger circuit breaker."""
        with self._lock:
            self._consecutive_failures += 1
            self._total_retries += 1
            if self._consecutive_failures >= self.config.circuit_breaker_threshold:
                self._circuit_open_until = (
                    time.monotonic() + self.config.circuit_breaker_cooldown
                )
                logger.warning(
                    "Circuit breaker OPEN: %d consecutive failures. "
                    "Pausing for %.0fs",
                    self._consecutive_failures,
                    self.config.circuit_breaker_cooldown,
                )

    def acquire_sync(self):
        """Block until a request slot is available (sync version)."""
        with self._lock:
            now = time.monotonic()

            # Circuit breaker check
            if now < self._circuit_open_until:
                wait = self._circuit_open_until - now
                logger.info("Circuit breaker active, waiting %.1fs...", wait)
                self._total_throttled += wait
                self._lock.release()
                time.sleep(wait)
                self._lock.acquire()
                self._consecutive_failures = 0  # Reset after cooldown

            # Token bucket: enforce minimum interval
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                self._total_throttled += wait
                self._lock.release()
                time.sleep(wait)
                self._lock.acquire()

            self._last_request_time = time.monotonic()
            self._total_requests += 1

    async def acquire_async(self):
        """Block until a request slot is available (async version)."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        lock = self._async_lock

        async with lock:
            now = time.monotonic()

            # Circuit breaker check
            if now < self._circuit_open_until:
                wait = self._circuit_open_until - now
                logger.info("Circuit breaker active, waiting %.1fs...", wait)
                self._total_throttled += wait
                await asyncio.sleep(wait)
                self._consecutive_failures = 0

            # Token bucket
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                self._total_throttled += wait
                await asyncio.sleep(wait)

            self._last_request_time = time.monotonic()
            self._total_requests += 1

    def get_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter.

        Formula: min(base^attempt + random(0, base^attempt), max_backoff)
        """
        exp = min(self.config.base_backoff ** attempt, self.config.max_backoff)
        jitter = random.uniform(0, exp)
        delay = min(exp + jitter, self.config.max_backoff)
        return delay

    @property
    def stats(self) -> dict:
        """Return rate limiter statistics."""
        return {
            "total_requests": self._total_requests,
            "total_retries": self._total_retries,
            "total_throttled_seconds": round(self._total_throttled, 1),
            "remaining_requests": self._remaining_requests,
            "remaining_tokens": self._remaining_tokens,
        }


def with_retry(fn, limiter: RateLimiter, max_retries: int | None = None):
    """Execute a sync function with rate limiting and retry logic.

    Args:
        fn: Callable that performs the API call. Should raise on error.
        limiter: RateLimiter instance.
        max_retries: Override for max retry attempts.

    Returns:
        The return value of fn().

    Raises:
        The last exception if all retries are exhausted.
    """
    retries = max_retries or limiter.config.max_retries
    last_error = None

    for attempt in range(retries + 1):
        limiter.acquire_sync()

        try:
            result = fn()
            limiter.record_success()
            return result

        except Exception as e:
            error_str = str(e)
            is_rate_limit = "429" in error_str or "rate" in error_str.lower()

            if is_rate_limit and attempt < retries:
                limiter.record_failure()
                delay = limiter.get_backoff_delay(attempt)
                logger.warning(
                    "Rate limited (attempt %d/%d). Backing off %.1fs",
                    attempt + 1,
                    retries,
                    delay,
                )
                time.sleep(delay)
                last_error = e
            elif not is_rate_limit:
                # Non-rate-limit error: don't retry
                raise
            else:
                last_error = e

    raise last_error  # type: ignore[misc]
