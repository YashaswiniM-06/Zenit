"""Authentication primitives and configuration for the isolated live pilot.

This module does not open a database, read secrets until explicitly requested,
or retain plaintext session/reset tokens. Browser mutations use Origin and
Fetch Metadata checks in addition to SameSite cookies.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import hmac
import math
import os
import secrets
import smtplib
import ssl
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request, Response
import httpx


COOKIE_NAME = "zenit_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
RESET_TTL_SECONDS = 30 * 60
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128

# A 32 MiB working set with three passes bounds parallel authentication memory
# while making password guesses expensive. Explicit maxmem avoids OpenSSL's
# default 32 MiB ceiling, which also counts the scrypt working overhead.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 3
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_DUMMY_SALT = bytes.fromhex("568f4d7a6793e74121d0856723e169452761ee4c15e6db8937c476168376fd62")
# Four hashes consume at most about 128 MiB of working memory. Limiting this
# separately from request rate keeps a burst from exhausting a small host.
_SCRYPT_SLOTS = threading.BoundedSemaphore(4)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _origin(url: str) -> Optional[tuple]:
    """Return a canonical origin, without credentials or untrusted URL details."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port
        return (parsed.scheme.lower(), parsed.hostname.lower(), port or (443 if parsed.scheme.lower() == "https" else 80))
    except (ValueError, TypeError):
        return None


def _valid_base_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 2048 or any(char.isspace() for char in url) or not _origin(url):
        return False
    parsed = urlsplit(url)
    return not parsed.query and not parsed.fragment


@dataclass(frozen=True)
class LiveSettings:
    """Explicitly loaded configuration; secret-bearing fields never enter repr."""

    enabled: bool = False
    database_url: str = field(default="", repr=False)
    geoapify_key: Optional[str] = field(default=None, repr=False)
    cookie_secure: bool = False
    app_base_url: str = "http://localhost:8000"
    smtp_host: str = field(default="smtp.gmail.com", repr=False)
    smtp_port: int = 587
    smtp_username: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    smtp_from: str = field(default="", repr=False)
    resend_api_key: str = field(default="", repr=False)
    mail_from: str = field(default="", repr=False)

    @classmethod
    def from_env(
        cls,
        enabled_override: Optional[bool] = None,
        db_url_override: Optional[str] = None,
        cookie_secure_override: Optional[bool] = None,
    ) -> "LiveSettings":
        """Read current environment, without modifying it or loading a .env file."""
        try:
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError:
            raise ValueError("SMTP_PORT must be an integer") from None
        if not 1 <= smtp_port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        return cls(
            enabled=_env_bool("LIVE_ENABLED", False) if enabled_override is None else bool(enabled_override),
            database_url=os.getenv("DATABASE_URL", "").strip() if db_url_override is None else str(db_url_override),
            geoapify_key=os.getenv("GEOAPIFY_API_KEY", "").strip() or None,
            cookie_secure=_env_bool("COOKIE_SECURE", False) if cookie_secure_override is None else bool(cookie_secure_override),
            app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8000").strip().rstrip("/"),
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
            smtp_port=smtp_port,
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", "").strip(),
            resend_api_key=os.getenv("RESEND_API_KEY", "").strip(),
            mail_from=os.getenv("MAIL_FROM", "").strip(),
        )

    @property
    def maps_enabled(self) -> bool:
        return bool(self.geoapify_key)

    @property
    def email_enabled(self) -> bool:
        return bool(
            _valid_base_url(self.app_base_url) and (
                (self.resend_api_key and self.mail_from)
                or (self.smtp_host and self.smtp_username and self.smtp_password and self.smtp_from)
            )
        )


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError("Password must be 10–128 characters")
    try:
        return password.encode("utf-8")
    except UnicodeError:
        raise ValueError("Password contains invalid Unicode") from None


def _derive_password(password: bytes, salt: bytes) -> bytes:
    with _SCRYPT_SLOTS:
        return hashlib.scrypt(
            password, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
            dklen=_SCRYPT_DKLEN, maxmem=_SCRYPT_MAXMEM,
        )


def hash_password(password: str) -> str:
    password_bytes = _password_bytes(password)
    salt = secrets.token_bytes(32)
    digest = _derive_password(password_bytes, salt)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: Optional[str]) -> bool:
    """Verify in constant time; missing/invalid records still incur scrypt cost.

    Password length is bounded before expensive work. Stored parameters are
    strictly recognized so a malformed record cannot request arbitrary memory.
    """
    try:
        password_bytes = _password_bytes(password)
    except (ValueError, UnicodeError):
        return False
    salt, expected, valid = _DUMMY_SALT, bytes(_SCRYPT_DKLEN), False
    if isinstance(encoded, str) and len(encoded) < 256:
        try:
            algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
            if (
                algorithm == "scrypt" and n == str(_SCRYPT_N) and r == str(_SCRYPT_R)
                and p == str(_SCRYPT_P) and len(salt_hex) == 64 and len(digest_hex) == 64
            ):
                parsed_salt, parsed_digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
                if len(parsed_salt) == 32 and len(parsed_digest) == _SCRYPT_DKLEN:
                    salt, expected, valid = parsed_salt, parsed_digest, True
        except (ValueError, TypeError):
            pass
    actual = _derive_password(password_bytes, salt)
    matched = hmac.compare_digest(actual, expected)
    return bool(valid and matched)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    if not isinstance(token, str) or not 1 <= len(token) <= 512:
        raise ValueError("Invalid authentication token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthRateLimiter:
    """Thread-safe sliding windows scoped to client IP and normalized email.

    This is a process-local pilot limiter. The IP-wide window prevents bypass by
    continually changing an email, and bounded bucket count caps memory use.
    Proxy addresses must already be validated by the ASGI server; forwarded
    headers are deliberately not consulted here.
    """

    def __init__(
        self,
        limit: int = 10,
        window_seconds: float = 600,
        ip_limit: int = 80,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = 10000,
    ):
        if limit < 1 or ip_limit < 1 or window_seconds <= 0 or max_buckets < 1:
            raise ValueError("Rate limiter bounds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.ip_limit = ip_limit
        self.clock = clock
        self.max_buckets = max_buckets
        self._lock = threading.Lock()
        self._buckets = {}

    def _prune(self, bucket: deque, cutoff: float) -> None:
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def check(self, request: Request, email: str, action: str = "login") -> None:
        now = self.clock()
        cutoff = now - self.window_seconds
        host = request.client.host if request.client else "unknown"
        pair_key = ("email", action, host, normalize_email(email))
        ip_key = ("ip", host)
        retry_after = None
        with self._lock:
            # Removing expired buckets bounds memory even when successful
            # account creation produces many distinct email addresses.
            for key, bucket in list(self._buckets.items()):
                self._prune(bucket, cutoff)
                if not bucket:
                    del self._buckets[key]
            ip_bucket = self._buckets.get(ip_key, deque())
            pair_bucket = self._buckets.get(pair_key, deque())
            if len(ip_bucket) >= self.ip_limit:
                retry_after = ip_bucket[0] + self.window_seconds - now
            elif len(pair_bucket) >= self.limit:
                retry_after = pair_bucket[0] + self.window_seconds - now
            elif len(self._buckets) + int(ip_key not in self._buckets) + int(pair_key not in self._buckets) > self.max_buckets:
                retry_after = self.window_seconds
            else:
                ip_bucket.append(now)
                pair_bucket.append(now)
                self._buckets[ip_key] = ip_bucket
                self._buckets[pair_key] = pair_bucket
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts. Please try again later.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )


def guard_unsafe_request(request: Request, settings: LiveSettings) -> None:
    """Reject cross-origin browser mutations, including login and signup.

    CLI clients without browser Origin/Fetch Metadata headers are accepted.
    Browsers that supply Origin must match the configured application origin;
    cross-site Fetch Metadata is always rejected, even with a matching Origin.
    """
    if request.method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        raise HTTPException(status_code=403, detail="Cross-origin requests are not allowed")
    origin = request.headers.get("origin")
    if origin is not None:
        # An Origin header has no path/query/fragment and never carries userinfo.
        actual = _origin(origin)
        expected = _origin(settings.app_base_url)
        try:
            parsed = urlsplit(origin)
            malformed = bool(parsed.path not in {"", "/"} or parsed.query or parsed.fragment)
        except ValueError:
            malformed = True
        if not actual or not expected or actual != expected or malformed:
            raise HTTPException(status_code=403, detail="Cross-origin requests are not allowed")


def set_session_cookie(response: Response, token: str, settings: LiveSettings) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: LiveSettings) -> None:
    response.delete_cookie(
        key=COOKIE_NAME, path="/", secure=settings.cookie_secure,
        httponly=True, samesite="lax",
    )


def send_password_reset(settings: LiveSettings, email: str, token: str) -> None:
    """Send a one-time reset URL through Resend or SMTP STARTTLS.

    Provider/network exceptions are intentionally left for the API to translate to
    a generic error; their messages must never be returned to the client.
    """
    if not settings.email_enabled:
        raise RuntimeError("Password recovery email is not configured")
    # Message headers must not accept control characters supplied by callers.
    sender = settings.mail_from if settings.resend_api_key and settings.mail_from else settings.smtp_from
    if any(char in email for char in "\r\n") or any(char in sender for char in "\r\n"):
        raise ValueError("Invalid email address")
    hash_token(token)  # Bound a caller-supplied token before constructing a URL.
    reset_url = f"{settings.app_base_url}/?reset_token={quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "Reset your ZENIT password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "A password reset was requested for your ZENIT account.\n\n"
        f"Open this link to choose a new password:\n{reset_url}\n\n"
        "This link expires in 30 minutes and can be used once.\n"
        "If you did not request this, you can ignore this message.\n"
    )
    if settings.resend_api_key and settings.mail_from:
        # A fixed HTTPS URL prevents configuration-based SSRF. Resend works on
        # hosts that block outbound SMTP; do not fall back and send twice.
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": sender,
                    "to": [email],
                    "subject": str(message["Subject"]),
                    "text": message.get_content(),
                },
            )
            response.raise_for_status()
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
