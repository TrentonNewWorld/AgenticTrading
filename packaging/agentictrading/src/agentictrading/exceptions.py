"""Exceptions raised by the NewWorldTrading Python SDK (``ATLClient``).

Every error preserves, where available:

* ``status_code`` - the HTTP status code returned by the backend
* ``message``     - a human-readable error message from the backend
* ``path``        - the request path that failed
* ``code``        - the backend error code (e.g. ``step_already_finalized``)
* ``response``    - the raw decoded response body, for debugging

Backend errors are never silently swallowed: the client raises one of the
classes below so callers can branch on the failure type.
"""

from __future__ import annotations

from typing import Any, Optional


class ATLAPIError(Exception):
    """Base class for all SDK errors returned by the backend or transport."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        path: Optional[str] = None,
        code: Optional[str] = None,
        response: Any = None,
        run_id: Optional[str] = None,
    ) -> None:
        self.status_code = status_code
        self.message = message or "API error"
        self.path = path
        self.code = code
        self.response = response
        self.run_id = run_id
        super().__init__(self._format())

    def _format(self) -> str:
        parts = []
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        if self.code:
            parts.append(f"[{self.code}]")
        parts.append(self.message)
        if self.run_id:
            parts.append(f"{{run {self.run_id}}}")
        if self.path:
            parts.append(f"({self.path})")
        return " ".join(parts)

    def with_run_id(self, run_id: Optional[str]) -> "ATLAPIError":
        """Attach the run id (if not already set) and refresh the message.

        Returns ``self`` so callers can ``raise exc.with_run_id(run.id)`` and
        preserve the original traceback.
        """
        if run_id and not self.run_id:
            self.run_id = run_id
            self.args = (self._format(),)
        return self


class ATLAuthenticationError(ATLAPIError):
    """Raised on HTTP 401/403 - missing or invalid API key, or wrong owner."""


class ATLValidationError(ATLAPIError):
    """Raised on HTTP 400/422 - the request was rejected as invalid."""


class ATLConflictError(ATLAPIError):
    """Raised on HTTP 409 - e.g. finalized step or deadline exceeded."""


class ATLTimeoutError(ATLAPIError):
    """Raised when the HTTP request times out at the transport layer."""


class ATLRunFailedError(ATLAPIError):
    """Raised when a run enters a ``failed`` or ``cancelled`` state."""


__all__ = [
    "ATLAPIError",
    "ATLAuthenticationError",
    "ATLValidationError",
    "ATLConflictError",
    "ATLTimeoutError",
    "ATLRunFailedError",
]
