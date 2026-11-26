"""
apps/api/schema.py
------------------
OpenAPI schema extensions for drf-spectacular.

Tells drf-spectacular how to describe APIKeyAuthentication in the
generated schema so the Swagger UI shows an "Authorize" button for
the sk_... token header.

This module is auto-discovered by drf-spectacular when it is imported
at startup.  We import it in apps.py AppConfig.ready() to guarantee
the extension is registered before any schema generation.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APIKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.authentication.APIKeyAuthentication"
    name = "APIKeyAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "API key authentication.  "
                "Pass your key as: `Authorization: Token sk_<your-key>`"
            ),
        }
