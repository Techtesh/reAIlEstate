from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


_ERROR_CODES = {
    400: "REQUEST_VALIDATION_FAILED",
    401: "AUTHENTICATION_REQUIRED",
    403: "ACCESS_DENIED",
    404: "RESOURCE_NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "VERSION_CONFLICT",
    413: "UPLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def problem_exception_handler(exc, context):
    response = exception_handler(exc, context)
    request = context.get("request")
    correlation_id = str(getattr(request, "correlation_id", ""))
    if response is None:
        return Response(
            {
                "type": "https://realestate.invalid/problems/internal-error",
                "title": "An unexpected error occurred",
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "code": "INTERNAL_ERROR",
                "correlationId": correlation_id,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    code = _ERROR_CODES.get(response.status_code, "REQUEST_FAILED")
    details = response.data
    response.data = {
        "type": f"https://realestate.invalid/problems/{code.lower().replace('_', '-')}",
        "title": "Request validation failed" if response.status_code == 400 else "Request failed",
        "status": response.status_code,
        "code": code,
        "correlationId": correlation_id,
        "errors": details,
    }
    return response
