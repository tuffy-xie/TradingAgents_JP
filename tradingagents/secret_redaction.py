"""Secret-safe presentation helpers for logs, errors, and persisted artifacts.

Credentials are allowed inside request objects immediately before transport.
They are not allowed to cross an observability or presentation boundary.  This
module provides one provider-agnostic policy for those boundaries while
preserving useful diagnostics such as exception type, host, path, HTTP status,
and non-sensitive query parameters.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

_SENSITIVE_EXACT = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "key",
    "password",
    "proxy_authorization",
    "secret",
    "set_cookie",
    "token",
    "x_api_key",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_password",
    "_secret",
    "_token",
)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_HEADER = re.compile(
    r"(?i)(\b(?:proxy[_-]?authorization|authorization)\s*:\s*)"
    r"(?:bearer\s+|basic\s+)?[^\s,;]+"
)
_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>"
    r"(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"authorization|proxy[_-]?authorization|password|client[_-]?secret|"
    r"x[_-]?api[_-]?key|cookie|set[_-]?cookie|secret|token|key)"
    r"[\"']?\s*(?:=|:)\s*"
    r")"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s,;\]\}\"']+)"
    r"(?P=quote)"
)


def is_sensitive_key(key: object) -> bool:
    """Return whether a mapping/query/header key conventionally holds a secret."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    return normalized in _SENSITIVE_EXACT or normalized.endswith(_SENSITIVE_SUFFIXES)


def sanitize_url(url: str) -> str:
    """Redact credentials in one URL without hiding host, path, or safe params."""
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return sanitize_text_without_urls(url)

        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parts.port}" if parts.port is not None else ""
        auth = f"{REDACTED}@" if parts.username is not None else ""
        netloc = f"{auth}{hostname}{port}"

        query = urlencode(
            [
                (key, REDACTED if is_sensitive_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ],
            doseq=True,
            safe="[]",
        )
        fragment = sanitize_text_without_urls(parts.fragment)
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    except (TypeError, ValueError):
        # A malformed URL must never make the redactor itself fail. The generic
        # assignment policy may preserve less structure, but remains fail-safe.
        return sanitize_text_without_urls(url)


def sanitize_text_without_urls(text: str) -> str:
    """Redact header/assignment forms in text already known not to contain URLs."""
    text = _AUTHORIZATION_HEADER.sub(rf"\1{REDACTED}", text)
    text = _BEARER.sub(rf"\1{REDACTED}", text)
    placeholder = "__TRADINGAGENTS_REDACTED_VALUE__"
    text = text.replace(REDACTED, placeholder)
    text = _ASSIGNMENT.sub(
        lambda match: (
            match.group("prefix")
            + match.group("quote")
            + REDACTED
            + match.group("quote")
        ),
        text,
    )
    return text.replace(placeholder, REDACTED)


def sanitize_text(value: object) -> str:
    """Return a readable string with embedded URLs and secret assignments redacted."""
    text = str(value)
    sanitized_urls: list[str] = []

    def replace_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        trailing = ""
        while candidate and candidate[-1] in ".,;)]}":
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        placeholder = f"__TRADINGAGENTS_SAFE_URL_{len(sanitized_urls)}__"
        sanitized_urls.append(sanitize_url(candidate) + trailing)
        return placeholder

    text = sanitize_text_without_urls(_URL.sub(replace_url, text))
    for index, safe_url in enumerate(sanitized_urls):
        text = text.replace(f"__TRADINGAGENTS_SAFE_URL_{index}__", safe_url)
    return text


def sanitize_data(value: Any) -> Any:
    """Recursively redact secret-bearing mappings, sequences, text, and errors."""
    if isinstance(value, BaseException):
        return safe_exception_text(value)
    if isinstance(value, Mapping):
        return {
            sanitize_text(key) if isinstance(key, str) else key: (
                REDACTED if is_sensitive_key(key) else sanitize_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_data(item) for item in value)
    if isinstance(value, set):
        return {sanitize_data(item) for item in value}
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def safe_exception_text(exc: BaseException) -> str:
    """Format an exception for presentation without exposing request credentials."""
    message = sanitize_text(str(exc))
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def redacted_exception(exc: BaseException) -> BaseException:
    """Create a same-class exception containing only the sanitized message.

    This is used when a failed required vendor call must still raise.  It keeps
    common transport exception classes catchable while deliberately dropping
    attached request/response objects that can retain headers or raw URLs.
    """
    message = sanitize_text(str(exc))
    try:
        return type(exc)(message)
    except Exception:
        return RuntimeError(f"{type(exc).__name__}: {message}")


def _sanitize_log_record(record: logging.LogRecord) -> logging.LogRecord:
    record.msg = sanitize_data(record.msg)
    record.args = sanitize_data(record.args)
    for key, value in tuple(record.__dict__.items()):
        if key not in {"msg", "args", "exc_info"}:
            setattr(record, key, sanitize_data(value))
    if record.exc_info:
        rendered = "".join(traceback.format_exception(*record.exc_info))
        record.exc_text = sanitize_text(rendered).rstrip()
    return record


def _secret_safe_record_factory(previous_factory):
    def factory(*args, **kwargs):
        return _sanitize_log_record(previous_factory(*args, **kwargs))

    factory._tradingagents_secret_safe = True  # type: ignore[attr-defined]
    return factory


def install_secret_safe_logging() -> None:
    """Install process-wide redaction before any LogRecord reaches a handler."""
    current = logging.getLogRecordFactory()
    if not getattr(current, "_tradingagents_secret_safe", False):
        logging.setLogRecordFactory(_secret_safe_record_factory(current))

    logger_class = logging.getLoggerClass()
    if getattr(logger_class, "_tradingagents_secret_safe", False):
        return

    class SecretSafeLogger(logger_class):
        _tradingagents_secret_safe = True

        def makeRecord(self, *args, **kwargs):  # noqa: N802 - logging API name
            # ``extra=`` fields are attached by Logger.makeRecord *after* the
            # global record factory runs, so sanitize once more at this layer.
            return _sanitize_log_record(super().makeRecord(*args, **kwargs))

    logging.setLoggerClass(SecretSafeLogger)
    # Libraries imported before TradingAgents may already own ordinary Logger
    # instances. Retrofit only the exact previous class; custom logger classes
    # retain their behavior and still benefit from the record factory for
    # normal message/args/traceback fields.
    for existing in logging.Logger.manager.loggerDict.values():
        if type(existing) is logger_class:
            with suppress(TypeError):
                existing.__class__ = SecretSafeLogger
