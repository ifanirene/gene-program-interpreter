"""``gpi dashboard`` — the optional live browser view; a pure read-side consumer of the feed.

Incident it prevents: a monitoring add-on that quietly reaches into the run path, ships a
non-self-contained page that breaks offline, or serves the snapshot from a different origin than
the page (so ``fetch()`` is blocked) is worse than no dashboard at all. These tests pin the
contract: the page is fully inline (no ``<script src=>``), it renders all the pipeline steps and
the tool-cascade wiring, ``write_dashboard`` is deterministic, and the served dir exposes the page
AND both feed files (``progress.json`` + ``progress.jsonl``) same-origin. Everything is offline;
no test binds the real 8899.
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
import urllib.error
import urllib.request

from gpi import dashboard


def test_render_is_self_contained(tmp_path) -> None:
    """The page must run with zero external scripts (a strict-CSP / offline host would otherwise
    show a dead shell): no ``<script src=>``, and it must carry the whole live contract inline —
    the theme attr, the two feeds it polls, the reduced-motion guard, the report link, the tool
    cascade, and every canonical step name (rendered from the real ``steps`` array at runtime)."""
    html = dashboard.render_dashboard_html()
    assert "<script src=" not in html  # fully inline; nothing to fetch from a CDN
    assert 'data-theme="dark"' in html
    assert 'fetch("progress.json"' in html
    assert 'fetch("progress.jsonl"' in html  # the tool-cascade fold reads the append-only log
    assert "agent_tool_call" in html  # the folded event type that drives the cascade
    assert "prefers-reduced-motion" in html
    assert "report.html" in html
    for step in ("preflight", "string_enrichment", "gene_summaries", "bundle", "research",
                 "verify", "theme", "annotate", "presentation", "html_report"):
        assert step in html, f"missing step in rail: {step}"
    # Scale contract for long/large runs: cap the visible agent cards, and read progress.jsonl
    # incrementally (HTTP Range) rather than re-fetching the whole growing log every tick.
    assert "MAX_LANES" in html          # at most N agent cards; queued collapse to a count
    assert "Range" in html              # incremental byte-range fold, not whole-file re-parse


def test_serpentine_layout_and_live_status_features() -> None:
    """The compact-serpentine redesign must ship its wiring inline: the SVG "snake" rail and its
    JS layout, the executor-code -> word relabel, the richer per-step live status (phase detail +
    k/n bar), and the self-contained brand logo. These are the user-visible behaviours the redesign
    layered on top of the read-only contract pinned above — all still fully inline."""
    html = dashboard.render_dashboard_html()
    # serpentine snake rail (an SVG threaded through the cards) + the JS that lays it out
    assert "rail-svg" in html
    assert "layoutSnake" in html
    # the `executor` field is a substrate code, not a worker count -> rendered as a word
    assert "EXEC_LABELS" in html
    for word in ("deterministic", "batch", "agents"):
        assert word in html, f"missing executor label: {word}"
    # richer per-step live status: the free-text phase line (.ph) + the k/n progress bar
    assert "stepbar" in html
    assert "data-role=\"bar\"" in html
    # brand logo embedded as a self-contained data URI (only when the generated asset is present)
    if dashboard.LOGO_MARK_DATA_URI:
        assert "brand-logo" in html
        assert "data:image/png;base64" in html


def test_write_creates_file(tmp_path) -> None:
    """``write_dashboard`` drops exactly ``<run_dir>/dashboard.html`` with the render output —
    the file the server hands the browser, so it must equal the renderer byte-for-byte."""
    out = dashboard.write_dashboard(tmp_path)
    assert out == tmp_path / "dashboard.html"
    assert out.read_text(encoding="utf-8") == dashboard.render_dashboard_html()


def test_missing_run_dir_is_not_an_error(tmp_path, capsys) -> None:
    """A user may open the dashboard before the run dir exists (or fat-finger the path). Like
    ``gpi watch``, that is a soft, explained no-op returning 0 — never a stack trace, and never a
    bound port left dangling."""
    rc = dashboard.cmd_dashboard([str(tmp_path / "does-not-exist")])
    assert rc == 0
    assert "does not exist yet" in capsys.readouterr().out


def test_serves_page_and_both_feeds_same_origin(tmp_path) -> None:
    """The reason this subcommand exists is that a ``file://`` page can't ``fetch()`` the JSON.
    The served dir must therefore expose the page AND both feed files from one origin. Built the
    way ``cmd_dashboard`` builds it (``directory=``-scoped handler on a ThreadingHTTPServer), bound
    to an ephemeral port — never the real 8899 — and shut down cleanly."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "progress.json").write_text(json.dumps({
        "run_id": "demo", "status": "running", "failed_step": None,
        "steps": [{"name": "preflight", "status": "completed"},
                  {"name": "research", "status": "in_progress", "current": 1, "total": 2}],
        "active_step": "research",
        "research": {"n_programs": 2, "n_done": 0, "n_incomplete": 0, "auth": "subscription",
                     "agents": [{"program_id": "10", "status": "running", "turns": 3,
                                 "current_tool": "pubmed.search"}]},
    }))
    (run / "progress.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": 1.0, "type": "run_start", "run_id": "demo"},
        {"ts": 2.0, "type": "agent_tool_call", "program_id": "10", "tool": "pubmed.search", "turn": 3},
    ]) + "\n")
    (run / "report.html").write_text("<h1>report</h1>")

    dashboard.write_dashboard(run)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(run))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    assert port != 8899  # sanity: we never bind the real default port in a test
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        page = urllib.request.urlopen(base + "/dashboard.html", timeout=5).read().decode("utf-8")
        assert 'fetch("progress.json"' in page
        snap = urllib.request.urlopen(base + "/progress.json", timeout=5).read().decode("utf-8")
        assert json.loads(snap)["run_id"] == "demo"  # same-origin, unmodified
        log = urllib.request.urlopen(base + "/progress.jsonl", timeout=5).read().decode("utf-8")
        assert "agent_tool_call" in log  # the cascade feed is reachable too
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_supports_byte_ranges(tmp_path) -> None:
    """The incremental fold depends on server-side Range support, which the stdlib handler LACKS
    (it returns 200 + the whole file). Pin _RangeHandler: a `bytes=start-` request must yield 206
    with exactly the trailing bytes, and a request at EOF must yield 416 — otherwise the dashboard
    silently re-parses the whole growing log every tick."""
    run = tmp_path / "run"
    run.mkdir()
    body = b"".join(b'{"type":"agent_tool_call","program_id":"%d"}\n' % i for i in range(50))
    (run / "progress.jsonl").write_bytes(body)

    handler = functools.partial(dashboard._RangeHandler, directory=str(run))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        offset = 100
        req = urllib.request.Request(base + "/progress.jsonl", headers={"Range": f"bytes={offset}-"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 206
            assert r.headers.get("Content-Range") == f"bytes {offset}-{len(body) - 1}/{len(body)}"
            assert r.read() == body[offset:]  # only the new bytes, not the whole file

        # At EOF -> 416 (this is the "no new bytes" signal the client treats as a no-op).
        eof = urllib.request.Request(base + "/progress.jsonl", headers={"Range": f"bytes={len(body)}-"})
        try:
            urllib.request.urlopen(eof, timeout=5)
            assert False, "expected 416 at EOF"
        except urllib.error.HTTPError as e:
            assert e.code == 416
    finally:
        httpd.shutdown()
        httpd.server_close()
