from types import SimpleNamespace

import pytest

from platform_api.vision_errors import safe_request_error


@pytest.mark.parametrize("status,code,retryable", [
    (401, "VISION_AUTHENTICATION_OR_ACCESS_DENIED", False),
    (403, "VISION_AUTHENTICATION_OR_ACCESS_DENIED", False),
    (404, "VISION_ENDPOINT_OR_DEPLOYMENT_NOT_FOUND", False),
    (429, "VISION_RATE_LIMITED", True),
    (503, "VISION_TRANSIENT_HTTP_ERROR", True),
    (400, "VISION_REQUEST_REJECTED", False),
])
def test_safe_status_classification(status, code, retryable):
    error = RuntimeError("secret-token and private response body")
    error.status_code = status
    error.response = SimpleNamespace(headers={"retry-after": "30"})
    safe = safe_request_error(error)
    assert safe.code == code
    assert safe.status_code == status
    assert safe.retryable is retryable
    assert safe.retry_after == 30
    assert "secret-token" not in str(safe)
    assert "private" not in repr(safe.__dict__)


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "900000", "bad"])
def test_invalid_retry_after_is_ignored(value):
    error = RuntimeError()
    error.response = SimpleNamespace(headers={"retry-after": value})
    assert safe_request_error(error).retry_after is None


def test_retry_after_milliseconds():
    error = RuntimeError()
    error.response = SimpleNamespace(headers={"retry-after-ms": "1500"})
    assert safe_request_error(error).retry_after == 1.5


def test_timeout_is_retryable():
    class APITimeoutError(Exception):
        pass
    assert safe_request_error(APITimeoutError()).retryable is True