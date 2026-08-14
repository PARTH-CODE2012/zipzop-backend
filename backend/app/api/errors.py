"""One error shape for the whole API.

    {"error": {"code": "...", "message": "...", "details": {...}}}

`code` is stable and machine-readable — clients branch on it. `message` is
English written to be shown to a person and may be reworded at any time.
See docs/05-api-contract.md §1 and §9.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger, request_id_var

log = get_logger(__name__)


class APIError(Exception):
    """Base for every error this API raises deliberately.

    Subclass per error code rather than passing strings around, so the set of
    codes stays greppable and matches the table in the contract.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"
    message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return JSONResponse(status_code=self.status_code, content={"error": body})


# --------------------------------------------------------------------------
# The codes below mirror docs/05-api-contract.md §9. Ones for endpoints that do
# not exist yet are defined anyway — the contract is agreed, and a client can
# be written against them before the endpoint lands.
# --------------------------------------------------------------------------


class InvalidCredentialsError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "INVALID_CREDENTIALS"
    message = "That email and password do not match."


class TokenExpiredError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "TOKEN_EXPIRED"
    message = "Your session has expired."


class TokenRevokedError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "TOKEN_REVOKED"
    message = "Please sign in again."


class ForbiddenError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "You do not have access to this."


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "Not found."


class VersionConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "VERSION_CONFLICT"
    message = "This project was changed somewhere else."


class AssetInUseError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "ASSET_IN_USE"
    message = "This file is used in a project."


class JobNotCancellableError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "JOB_NOT_CANCELLABLE"
    message = "This job has already finished."


class InvalidTimelineError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "INVALID_TIMELINE"
    message = "The timeline could not be saved."


class UnsupportedMediaError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "UNSUPPORTED_MEDIA"
    message = "We cannot read this file format."


class MediaTooLongError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "MEDIA_TOO_LONG"
    message = "This video is longer than we accept."


class FileTooLargeError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "FILE_TOO_LARGE"
    message = "This file is larger than we accept."


class InsufficientCreditsError(APIError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "INSUFFICIENT_CREDITS"
    message = "You do not have enough credits for this."


class StorageQuotaExceededError(APIError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "STORAGE_QUOTA_EXCEEDED"
    message = "Your storage is full."


class PlanLimitExceededError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PLAN_LIMIT_EXCEEDED"
    message = "Your plan does not include this."


class FairUseExceededError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FAIR_USE_EXCEEDED"
    message = "You have passed this month's fair-use limit."


class SubscriptionRequiredError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "SUBSCRIPTION_REQUIRED"
    message = "This feature needs a paid plan."


class CheckoutFailedError(APIError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "CHECKOUT_FAILED"
    message = "The payment provider could not start a checkout."


class ConcurrencyLimitError(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "CONCURRENCY_LIMIT"
    message = "You already have as many jobs running as your plan allows."


class RateLimitedError(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    message = "Too many requests. Try again shortly."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(_request: Request, exc: APIError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Some fields are missing or malformed.",
                    "details": {"fields": exc.errors()},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Keep framework-raised errors in the same envelope as ours, so clients
        # never have to parse two shapes.
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED"}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": codes.get(exc.status_code, "HTTP_ERROR"),
                    "message": str(exc.detail),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_var.get()
        log.exception("unhandled", path=request.url.path, error=type(exc).__name__)
        # Never leak internals to a client. The request id is how support ties
        # a user's report to the log line.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Something went wrong on our side.",
                    "details": {"requestId": request_id},
                }
            },
        )
