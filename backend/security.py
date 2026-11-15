import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    """Raised when a URL could reach a non-public or unsupported destination."""


ALLOWED_PORTS = {80, 443}
NON_NETWORK_SCHEMES = {"data", "blob", "about"}


def _validate_syntax(url: str):
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise UnsafeURL("Please provide a valid URL (maximum 2048 characters).")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURL("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeURL("Invalid URL: hostname is missing.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURL("URLs containing embedded credentials are blocked.")

    try:
        port = parsed.port
    except ValueError:
        raise UnsafeURL("Invalid URL port.")
    effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    if effective_port not in ALLOWED_PORTS:
        raise UnsafeURL("Only standard HTTP (80) and HTTPS (443) ports are allowed.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost"} or hostname.endswith((".localhost", ".local", ".internal", ".test")):
        raise UnsafeURL("Local and internal hostnames are blocked.")
    if "." not in hostname and not _is_ip_literal(hostname):
        raise UnsafeURL("Single-label hostnames are blocked.")

    return parsed, hostname, effective_port


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _check_ip(value: str):
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        raise UnsafeURL("Hostname resolved to an invalid IP address.")
    if not address.is_global:
        raise UnsafeURL("Private, reserved, or non-public IP addresses are blocked.")


def validate_url(url: str) -> str:
    """Synchronous validation for non-async callers."""
    _, hostname, port = _validate_syntax(url)

    if _is_ip_literal(hostname):
        _check_ip(hostname)
        return url

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeURL("Could not resolve this hostname.")

    if not infos:
        raise UnsafeURL("Hostname did not resolve to an address.")

    # Reject the entire host if ANY answer is non-public.
    for info in infos:
        _check_ip(info[4][0])
    return url


async def validate_url_async(url: str) -> str:
    """Run DNS resolution off the event loop."""
    return await asyncio.to_thread(validate_url, url)


async def validate_request_url(url: str) -> bool:
    """Validate a browser request. Returns True for safe browser-internal schemes."""
    scheme = urlparse(url).scheme.lower()
    if scheme in NON_NETWORK_SCHEMES:
        return True
    await validate_url_async(url)
    return True
