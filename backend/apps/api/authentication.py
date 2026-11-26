"""
apps/api/authentication.py
--------------------------
API key authentication for external machine clients.

The key is passed in the Authorization header as:
    Authorization: Token sk_<token>

We look it up by its SHA-256 hash so the plaintext never sits in the database.
On success we attach the ``APIClient`` instance to ``request.auth`` and a
synthetic anonymous ``_APIClientUser`` to ``request.user`` so DRF's permission
checks see an authenticated identity without touching the accounts.User table.
"""

from __future__ import annotations

import logging

from rest_framework import authentication, exceptions

from apps.core.utils import get_client_ip

logger = logging.getLogger(__name__)


class _APIClientUser:
    """
    Thin stand-in for a User object when the caller is an APIClient.
    DRF checks ``is_authenticated`` and nothing else at the authentication
    layer; actual access control is done by HasScope permissions.
    """

    is_authenticated = True
    is_active = True

    def __init__(self, client):
        self.client = client
        self.pk = f"api_client:{client.pk}"

    def __str__(self):
        return f"APIClient({self.client.name})"


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    DRF authentication class for API key (sk_…) tokens.

    Returns (user_proxy, api_client) on success so both
    ``request.user`` and ``request.auth`` carry useful objects.
    """

    keyword = "Token"

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith(self.keyword + " "):
            return None  # let the next authenticator try

        plain_key = auth_header[len(self.keyword) + 1:].strip()

        # Only handle keys with the sk_ prefix — JWTs are handled by simplejwt
        if not plain_key.startswith("sk_"):
            return None

        from .models import APIClient  # local import avoids circular at module load

        client = APIClient.authenticate(plain_key)
        if client is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")

        # IP allowlist check
        if client.allowed_ips:
            ip = get_client_ip(request)
            if ip not in client.allowed_ips:
                logger.warning(
                    "API key %s used from disallowed IP %s", client.key_prefix, ip
                )
                raise exceptions.AuthenticationFailed(
                    "This API key is not permitted from your IP address."
                )

        client.touch()
        logger.debug("API key auth: %s", client.name)
        return (_APIClientUser(client), client)

    def authenticate_header(self, request):
        return self.keyword
