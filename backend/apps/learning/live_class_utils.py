"""
apps/learning/live_class_utils.py
---------------------------------
URL validation and provider normalization for live class sessions (Option 1).
"""

from __future__ import annotations

from urllib.parse import urlparse


ALLOWED_DOMAINS: dict[str, tuple[str, ...]] = {
    "google_meet": ("meet.google.com",),
    "zoom": ("zoom.us", "zoomgov.com"),
    "teams": ("teams.microsoft.com", "teams.live.com", "meet.teams.com", "teams.office.com"),
}


def normalize_meeting_url(url: str) -> str:
    """Trim whitespace and ensure the URL includes a scheme."""
    url = (url or "").strip()
    if not url:
        return ""
    # If a scheme is already present (e.g. javascript:, http://, file:)
    if ":" in url:
        prefix = url.split(":", 1)[0].lower()
        if prefix in ("http", "https"):
            return url
        # Keep non-http schemes intact so validation can reject them
        return url
    return "https://" + url


def validate_recording_url(url: str) -> tuple[bool, str]:
    """
    Validate a session recording link.

    Recordings are hosted elsewhere (Drive, YouTube, the meeting provider), so
    any public host is accepted; only the scheme and the host itself are
    constrained.
    """
    norm_url = normalize_meeting_url(url)
    if not norm_url:
        return False, "Paste the recording link."

    try:
        parsed = urlparse(norm_url)
    except Exception:
        return False, "Invalid URL structure."

    if parsed.scheme not in ("http", "https"):
        return False, "Recording link must use HTTP or HTTPS."

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "Invalid host in recording URL."
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False, "Recording link cannot be a local address."
    if parsed.username or parsed.password:
        return False, "Recording link must not contain a username or password."

    return True, ""


def validate_meeting_url(url: str, platform: str = "google_meet") -> tuple[bool, str]:
    """
    Validate that the given URL is safe and corresponds to the declared platform.

    Returns:
        (is_valid, error_message)
    """
    norm_url = normalize_meeting_url(url)
    if not norm_url:
        return False, "Meeting URL cannot be empty."

    try:
        parsed = urlparse(norm_url)
    except Exception:
        return False, "Invalid URL structure."

    if parsed.scheme not in ("http", "https"):
        return False, "Meeting link must use HTTP or HTTPS."

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "Invalid host in meeting URL."

    # Prevent loopback / localhost in non-testing scenarios if desirable
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0") and platform != "other":
        return False, "Meeting URL cannot be a localhost address."

    allowed = ALLOWED_DOMAINS.get(platform)
    if allowed:
        matched = any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in allowed
        )
        if not matched:
            domains_str = ", ".join(allowed)
            return False, f"URL does not match {platform.replace('_', ' ').title()} domain (expected: {domains_str})."

    return True, ""
