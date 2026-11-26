"""Shared HTTP request and outbound URL safety helpers."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from django.conf import settings

_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def _parse_trusted_networks() -> list[ipaddress._BaseNetwork]:
    raw = getattr(settings, "TRUSTED_PROXIES", ()) or ()
    networks: list[ipaddress._BaseNetwork] = []
    for entry in raw:
        text = (entry or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy(addr: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    if not addr or not networks:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def get_client_ip(request) -> str:
    """Return the client IP, trusting proxy headers only from configured proxies."""
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    networks = _parse_trusted_networks()
    if not _is_trusted_proxy(remote_addr, networks):
        return remote_addr

    if getattr(settings, "TRUST_CF_CONNECTING_IP", False):
        cf_ip = (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip()
        if cf_ip:
            try:
                ipaddress.ip_address(cf_ip)
                return cf_ip
            except ValueError:
                pass

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        for hop in reversed(hops):
            if not _is_trusted_proxy(hop, networks):
                return hop
        if hops:
            return hops[0]

    return remote_addr


def validate_http_url_syntax(url: str) -> tuple[str, int]:
    """Validate URL scheme, host, and literal IP without performing DNS."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError("Use an http:// or https:// address.")
    if not parts.hostname:
        raise ValueError("That web address is incomplete.")
    if parts.username or parts.password:
        raise ValueError("Remove the username and password from the address.")

    host = parts.hostname.lower().rstrip(".")
    if host in _LOCAL_HOSTNAMES or host.endswith((".local", ".internal")):
        raise ValueError("That address points to a local host, so it cannot be used.")

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError("That address points to a private network, so it cannot be used.")

    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def validate_public_http_url(url: str) -> None:
    """Validate URL syntax and verify all resolved DNS addresses are globally routable."""
    host, port = validate_http_url_syntax(url)
    try:
        answers = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("That web address could not be found.") from exc

    for answer in answers:
        address = answer[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("That web address could not be read.") from exc
        if not ip.is_global:
            raise ValueError(
                "That address points to a private network, so it cannot be used."
            )
