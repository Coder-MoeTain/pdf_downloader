"""Application errors with user-safe messages and secret-safe logs."""

from __future__ import annotations


class CyberScholarError(Exception):
    """Base error. `public_message` is safe to show in the UI."""

    status_code: int = 400
    public_message: str = "The request could not be completed."

    def __init__(self, message: str | None = None, *, public_message: str | None = None) -> None:
        detail = message or public_message or self.public_message
        super().__init__(detail)
        if public_message:
            self.public_message = public_message


class AuthorizationError(CyberScholarError):
    status_code = 403
    public_message = "You do not have permission to do that."


class SetupRequiredError(CyberScholarError):
    status_code = 403
    public_message = "First-run setup is required."


class CsrfError(CyberScholarError):
    status_code = 403
    public_message = "This request could not be verified. Reload the page and try again."


class UnsafeUrlError(CyberScholarError):
    status_code = 400
    public_message = "The URL is not allowed."


class DownloadValidationError(CyberScholarError):
    status_code = 400
    public_message = "The file could not be downloaded safely."


class OaLookupError(CyberScholarError):
    status_code = 502
    public_message = "Open-access lookup failed."


class ProviderUnavailable(CyberScholarError):
    status_code = 503
    public_message = "That research source is temporarily unavailable."


class ProviderRateLimited(CyberScholarError):
    status_code = 429
    public_message = "That research source is rate-limiting requests. Try again shortly."


class DatabaseBusyError(CyberScholarError):
    status_code = 503
    public_message = "The library database is busy. Wait a moment and try again."


class ConfigurationError(CyberScholarError):
    status_code = 500
    public_message = "The application is not configured for this environment."
