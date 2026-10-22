"""Safe provider failure metadata; never log response bodies or exception messages."""

import math

from .integrations import ProviderUnavailable


class VisionRequestError(ProviderUnavailable):
    def __init__(self, code, status_code=None, retryable=False, retry_after=None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


class AllVisionProvidersFailed(VisionRequestError):
    def __init__(self, attempts):
        super().__init__("VISION_ALL_PROVIDERS_FAILED", retryable=True)
        self.attempts = list(attempts)


def safe_request_error(exc):
    status = getattr(exc, "status_code", None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    names = {cls.__name__ for cls in type(exc).__mro__}
    if status in (401, 403):
        code = "VISION_AUTHENTICATION_OR_ACCESS_DENIED"
    elif status == 429:
        code = "VISION_RATE_LIMITED"
    elif status == 404:
        code = "VISION_ENDPOINT_OR_DEPLOYMENT_NOT_FOUND"
    elif status in (408, 409) or (status is not None and status >= 500):
        code = "VISION_TRANSIENT_HTTP_ERROR"
    elif status is not None:
        code = "VISION_REQUEST_REJECTED"
    elif names & {"APITimeoutError", "TimeoutException", "TimeoutError"}:
        code = "VISION_TIMEOUT"
    elif names & {"APIConnectionError", "ConnectError"}:
        code = "VISION_CONNECTION_ERROR"
    elif isinstance(exc, ProviderUnavailable):
        code = "VISION_AUTHENTICATION_NOT_CONFIGURED"
    else:
        code = "VISION_CLIENT_ERROR"
    retryable = code in {"VISION_RATE_LIMITED", "VISION_TRANSIENT_HTTP_ERROR", "VISION_TIMEOUT", "VISION_CONNECTION_ERROR"}
    delay = None
    try:
        headers = exc.response.headers
        raw = headers.get("retry-after")
        delay = float(raw) if raw is not None else float(headers.get("retry-after-ms")) / 1000
        delay = delay if math.isfinite(delay) and 0 <= delay <= 300 else None
    except (AttributeError, TypeError, ValueError):
        pass
    return VisionRequestError(code, status, retryable, delay)
