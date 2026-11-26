"""
apps/api/exceptions.py
-----------------------
Uniform JSON error envelope for all API responses.

Every error from the API — validation, auth, 404, throttle — returns:
    {
        "error": {
            "code": "authentication_failed",
            "message": "Invalid or revoked API key.",
            "detail": null          # present for field-level errors
        }
    }
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """
    Wraps DRF's default exception handler in the standard error envelope.
    Unexpected exceptions (not DRF APIException subclasses) are re-raised
    so Django's 500 machinery and Sentry still catch them.
    """
    response = drf_default_handler(exc, context)
    if response is None:
        # Not a DRF exception — let it propagate to Sentry / 500 handler
        return None

    # Build a consistent envelope
    code = getattr(exc, "default_code", "error")
    if hasattr(exc, "detail"):
        detail = exc.detail
        if isinstance(detail, dict):
            # Validation errors keyed by field
            message = "Validation failed."
        elif isinstance(detail, list) and len(detail) == 1:
            message = str(detail[0])
            detail = None
        else:
            message = str(detail)
            detail = None
    else:
        message = str(exc)
        detail = None

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
        }
    }
    return response
