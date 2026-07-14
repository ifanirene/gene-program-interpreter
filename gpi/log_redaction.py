"""Keep secrets out of the logs — and out of anything we hand back to an agent.

Two independent leaks are closed here.

1. ``httpx`` / ``httpcore`` / ``urllib3`` log every request URL at INFO. Seven modules call
   ``logging.basicConfig(level=INFO)`` on the ROOT logger, so those URLs land in the run log —
   and an NCBI E-utilities URL carries ``api_key`` (secret) *and* ``email`` (PII) in its query
   string. Dropping those loggers to WARNING removes the bulk of the leak.

2. Silencing them is **not sufficient**. ``httpx.HTTPStatusError`` embeds the full request URL
   in its exception *message*, which we then format into our own WARNING/ERROR records — and
   which can be fed back into a research agent's context. So every record is also scrubbed:
   :func:`redact_text` rewrites secret query-string values to ``api_key=<redacted>``.

:func:`redact_text` is the reusable primitive — ``gpi.progress`` also runs step errors through
it before they reach ``progress.json``, which the skill reads back into an agent's context.
:func:`install_log_redaction` wires it into ``logging``.

Stdlib-only by design: this module is imported by ``gpi.progress``, which every subprocess step
module imports on the hot path.
"""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any, Optional

__all__ = ["REDACTED", "RedactingFilter", "install_log_redaction", "redact_text"]

REDACTED = "<redacted>"

# Query-string / ``key=value`` parameters whose VALUE is a secret or PII. ``api_key`` precedes
# the bare ``key`` so it wins the alternation; the lookbehind below is what actually stops
# ``key`` from matching *inside* ``api_key`` (``_`` is a word char, so there is no boundary).
_SECRET_PARAMS = (
    "api[_-]?key",
    "access[_-]?token",
    "auth[_-]?token",
    "session[_-]?token",
    "token",
    "secret",
    "password",
    "passwd",
    "key",
    "email",
    "tool",
)

_PARAM_RE = re.compile(
    r"(?<![\w-])(" + "|".join(_SECRET_PARAMS) + r")(\s*=\s*)([^&\s#\"'<>]+)",
    re.IGNORECASE,
)

# A secret can also arrive as a command-line *flag*, where no ``=`` separates it from its
# value. The case that matters: ``str(CalledProcessError)`` repr's the failing argv
# (``'--api-key', 'ABC123'``), and the pipeline stores that string as a step error in
# progress.json — which the skill reads straight back into an agent's context.
# ``(?![\w-])`` after the flag name is load-bearing: without it, ``--keyword endothelial``
# would match as ``--key`` + ``word`` and corrupt a legitimate log line.
_FLAG_RE = re.compile(
    r"(?<![\w-])(--?(?:api[-_]?key|access[-_]?token|auth[-_]?token|token|secret"
    r"|password|passwd|email|key))(?![\w-])"
    r"(['\"]?[=,]?\s*['\"]?)(?!-)([^\s'\",\]}]+)",
    re.IGNORECASE,
)

# Bare Anthropic-style keys: an SDK error can echo the credential with no ``key=`` in front.
_BARE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")

# httpx/httpcore/urllib3 all log the full request URL at INFO. Children (e.g.
# ``urllib3.connectionpool``) inherit the level, so the three parents are enough.
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore", "urllib3")


def redact_text(text: str) -> str:
    """Return ``text`` with secret query-string values and bare API keys redacted.

    Idempotent: an already-redacted ``api_key=<redacted>`` is left alone, because ``<`` cannot
    start a value. Never raises — a redaction failure must not take the caller down with it.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        out = _PARAM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        out = _FLAG_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
        return _BARE_KEY_RE.sub(REDACTED, out)
    except Exception:
        return text


def _redact_arg(value: Any) -> Any:
    """Redact one %-format argument.

    Only ``str`` and exception objects are rewritten — coercing anything else would break
    ``%d``/``%f`` formatting. The exception case is the important one: ``logger.error("%s", exc)``
    keeps the httpx URL (and its ``api_key``) *inside the exception object* until format time,
    where a msg-only scrub would never see it.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, BaseException):
        original = str(value)
        redacted = redact_text(original)
        return redacted if redacted != original else value
    return value


def _redacted_traceback(exc_info: Any) -> Optional[str]:
    try:
        return redact_text("".join(traceback.format_exception(*exc_info)).rstrip("\n"))
    except Exception:
        return None


class RedactingFilter(logging.Filter):
    """Scrub a record in place: message, %-args, and any traceback.

    A logging filter that raises breaks the whole program, so every branch is guarded and the
    record is always allowed through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)

            args = record.args
            if isinstance(args, tuple):
                record.args = tuple(_redact_arg(a) for a in args)
            elif isinstance(args, dict):
                record.args = {k: _redact_arg(v) for k, v in args.items()}

            if isinstance(record.exc_text, str):
                record.exc_text = redact_text(record.exc_text)
            elif record.exc_info:
                # Pre-render the traceback ourselves, redacted. ``logging.Formatter.format``
                # reuses a non-empty ``exc_text`` verbatim rather than re-formatting
                # ``exc_info``, so this is the one hook where a third-party exception message
                # (an httpx URL, say) can be scrubbed before any handler sees it.
                text = _redacted_traceback(record.exc_info)
                if text is not None:
                    record.exc_text = text
        except Exception:
            pass
        return True


_FILTER = RedactingFilter()
_installed = False


def install_log_redaction() -> None:
    """Idempotent. Safe to call many times, from any process.

    Called by ``run_pipeline()`` for the driver process and by each subprocess step module that
    has its own CLI entry point (a child does not inherit the parent's logging config).
    """
    global _installed

    for name in _NOISY_HTTP_LOGGERS:
        try:
            logging.getLogger(name).setLevel(logging.WARNING)
        except Exception:
            pass

    if _installed:
        return

    try:
        root = logging.getLogger()
        if _FILTER not in root.filters:
            root.addFilter(_FILTER)

        # A Logger's filters only run for records logged *through that logger*: a record from a
        # child logger (``gpi.enrichment``, ``httpx``, ...) reaches root's HANDLERS via
        # ``callHandlers`` without ever passing root's filters. So the filter above covers only
        # bare ``logging.info(...)`` calls. The record factory runs for EVERY record from EVERY
        # logger at construction time, and survives the handler swaps that ``basicConfig`` and
        # the Rich renderer perform — that is what actually closes the leak.
        old_factory = logging.getLogRecordFactory()
        if not getattr(old_factory, "_gpi_redacting", False):
            def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
                record = old_factory(*args, **kwargs)
                _FILTER.filter(record)
                return record

            factory._gpi_redacting = True  # type: ignore[attr-defined]
            logging.setLogRecordFactory(factory)

        _installed = True
    except Exception:
        pass
