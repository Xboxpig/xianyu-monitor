"""Shared retry policy for upstream AI requests."""

DEFAULT_AI_RETRY_ATTEMPTS = 10
AI_RETRY_MAX_DELAY_SECONDS = 32


def ai_retry_delay_seconds(failed_attempt: int) -> int:
    """Return 1, 2, 4, 8, 16, 32, 32... for zero-based attempts."""
    return min(2 ** max(0, failed_attempt), AI_RETRY_MAX_DELAY_SECONDS)
