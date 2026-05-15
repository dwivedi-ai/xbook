class XbookError(Exception):
    """Base class for xbook-specific errors."""


class MissingCookies(XbookError):
    """auth_token or ct0 was not provided."""


class ExpiredCookies(XbookError):
    """Cookies were rejected; user was redirected to login."""


class RateLimited(XbookError):
    """X returned a rate-limit signal."""


class ResponseShapeChanged(XbookError):
    """The GraphQL response no longer matches the expected shape."""


class BrowserNotInstalled(XbookError):
    """The Playwright Chromium binary is not installed yet."""
