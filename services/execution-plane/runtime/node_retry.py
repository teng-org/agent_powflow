from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeRetryPolicy:
    max_attempts: int = 1
    initial_delay_ms: int = 250
    backoff_multiplier: float = 2.0
    max_delay_ms: int = 5_000
    retry_on: str = "transient"

    @classmethod
    def from_node(cls, node: dict) -> "NodeRetryPolicy":
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        retry = config.get("retry") if isinstance(config.get("retry"), dict) else {}
        return cls(
            max_attempts=max(1, min(int(_value(retry, "max_attempts", 1)), 5)),
            initial_delay_ms=max(0, min(int(_value(retry, "initial_delay_ms", 250)), 30_000)),
            backoff_multiplier=max(1.0, min(float(_value(retry, "backoff_multiplier", 2.0)), 4.0)),
            max_delay_ms=max(0, min(int(_value(retry, "max_delay_ms", 5_000)), 60_000)),
            retry_on=str(_value(retry, "retry_on", "transient")).strip().lower(),
        )

    def delay_ms(self, failed_attempt_no: int) -> int:
        exponent = max(0, failed_attempt_no - 1)
        return min(self.max_delay_ms, int(self.initial_delay_ms * (self.backoff_multiplier ** exponent)))

    def should_retry(self, exc: Exception, attempt_no: int) -> bool:
        if attempt_no >= self.max_attempts or _is_hard_failure(exc):
            return False
        if bool(getattr(exc, "retryable", False)):
            return True
        if _is_transient_exception(exc):
            return True
        return self.retry_on == "any"


def _is_hard_failure(exc: Exception) -> bool:
    if str(exc) == "TASK_CANCELLED":
        return True
    try:
        from runtime.provider_idempotency import ProviderInvocationConflict
        if isinstance(exc, ProviderInvocationConflict):
            return True
    except ImportError:
        pass
    try:
        from runtime.safe_http import UnsafeHttpRequest
        if isinstance(exc, UnsafeHttpRequest) and not bool(getattr(exc, "retryable", False)):
            return True
    except ImportError:
        pass
    try:
        from runtime.workflow_secrets import WorkflowSecretError
        if isinstance(exc, WorkflowSecretError):
            return True
    except ImportError:
        pass
    return isinstance(exc, (ValueError, TypeError, KeyError))


def _is_transient_exception(exc: Exception) -> bool:
    try:
        from runtime.provider_idempotency import ProviderInvocationInProgress
        if isinstance(exc, ProviderInvocationInProgress):
            return True
    except ImportError:
        pass
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except ImportError:
        pass
    return False


def _value(source: dict, key: str, default):
    value = source.get(key)
    return default if value is None else value
