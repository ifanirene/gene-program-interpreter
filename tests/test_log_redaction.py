"""Secrets must not reach the logs — or an agent's context.

A real run wrote ``NCBI_API_KEY`` into ``runs/*.log`` in cleartext **46 times** (195x in
another), plus ``PUBMED_EMAIL``. The mechanism: ``httpx`` logs every request URL at INFO, the
NCBI E-utilities carry ``api_key=`` and ``email=`` in the query string, and seven modules call
``logging.basicConfig`` on the root logger. ``runs/`` was not gitignored at the time, so the key
was one ``git add`` away from being published.

Suppressing httpx is necessary but NOT sufficient, which is the subtle part:
``httpx.HTTPStatusError`` embeds the full request URL in its exception *message*, and that
message is formatted into our own WARNING/ERROR records — and, worse, can be handed back into a
research agent's context. So the redaction must also rewrite records we emit ourselves.
"""

from __future__ import annotations

import io
import logging

from gpi.log_redaction import install_log_redaction, redact_text

SECRET = "933bc595deadbeefcafe1234"
NCBI_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    f"?db=pubmed&term=SOX17&api_key={SECRET}&email=someone@example.com"
)


def _capture(emit) -> str:
    """Run ``emit`` with redaction installed and return everything that reached a handler."""
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        emit()
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    return stream.getvalue()


def test_api_key_never_reaches_a_handler() -> None:
    out = _capture(lambda: logging.getLogger("gpi.gene_summaries").info("HTTP Request: GET %s", NCBI_URL))
    assert SECRET not in out
    assert "api_key=<redacted>" in out


def test_redaction_reaches_records_from_child_loggers() -> None:
    """A filter attached to the root *logger* never sees records from ``gpi.*`` — Python walks
    ancestors' *handlers*, not their *filters*. If someone 'simplifies' the implementation down
    to a root filter, this is the test that fails."""
    out = _capture(lambda: logging.getLogger("gpi.enrichment.deeply.nested").warning(NCBI_URL))
    assert SECRET not in out


def test_the_secret_inside_an_exception_message_is_redacted() -> None:
    """``HTTPStatusError`` carries the URL in its text. This is the path that suppressing httpx
    does not close, and the one that can leak into an agent's context."""
    def emit() -> None:
        try:
            raise RuntimeError(f"Client error '429 Too Many Requests' for url '{NCBI_URL}'")
        except RuntimeError:
            logging.getLogger("gpi.pipeline").exception("request failed")

    out = _capture(emit)
    assert SECRET not in out


def test_email_is_redacted_because_it_is_pii() -> None:
    out = _capture(lambda: logging.getLogger("gpi").info(NCBI_URL))
    assert "someone@example.com" not in out


def test_non_secret_query_params_survive() -> None:
    """Redaction that eats the whole URL makes logs useless for debugging. Keep the rest."""
    out = _capture(lambda: logging.getLogger("gpi").info(NCBI_URL))
    assert "db=pubmed" in out
    assert "term=SOX17" in out
    assert "eutils.ncbi.nlm.nih.gov" in out


def test_redaction_is_idempotent() -> None:
    """Already-redacted text must not be re-mangled, and installing twice must be safe."""
    once = redact_text(NCBI_URL)
    assert redact_text(once) == once
    install_log_redaction()
    install_log_redaction()
    assert SECRET not in _capture(lambda: logging.getLogger("gpi").info(NCBI_URL))


def test_httpx_request_logging_is_silenced() -> None:
    """The bulk of the 46 leaked lines came straight from httpx at INFO."""
    install_log_redaction()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
