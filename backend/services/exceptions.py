from __future__ import annotations

from typing import Any, Dict, Optional


class ServiceError(Exception):
    """Base application/service-layer exception."""

    status_code: int = 500
    error_code: str = "service_error"
    default_message: str = "An internal service error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[Dict[str, Any]] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(ServiceError):
    status_code = 400
    error_code = "validation_error"
    default_message = "The request data is invalid."


class NotFoundError(ServiceError):
    status_code = 404
    error_code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(ServiceError):
    status_code = 409
    error_code = "conflict"
    default_message = "The request conflicts with the current state of the resource."


class UnauthorizedError(ServiceError):
    status_code = 401
    error_code = "unauthorized"
    default_message = "Authentication is required."


class ForbiddenError(ServiceError):
    status_code = 403
    error_code = "forbidden"
    default_message = "You do not have permission to perform this action."


class TooManyRequestsError(ServiceError):
    status_code = 429
    error_code = "rate_limited"
    default_message = "Too many requests. Please slow down and try again shortly."


# Backward-compatible aliases used across older batches.
ValidationServiceError = ValidationError
NotFoundServiceError = NotFoundError
ConflictServiceError = ConflictError
UnauthorizedServiceError = UnauthorizedError
ForbiddenServiceError = ForbiddenError
TooManyRequestsServiceError = TooManyRequestsError
