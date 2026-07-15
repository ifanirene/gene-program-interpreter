"""``gpi dashboard <run_dir>`` — an optional, animated live view of a run in the browser.

Why this exists: the pipeline runs in the background and emits a rich progress feed
(``progress.jsonl`` event log + a reduced ``progress.json`` snapshot rewritten ~1/s by the
tailer in :mod:`gpi.progress`). ``gpi watch`` renders that feed as a terminal card; this is the
*visual* counterpart — a self-contained HTML page that polls the same files and animates the
10-step rail plus the parallel research lanes, styled to read as continuous with ``report.html``.

It is **strictly read-only and purely additive**. It changes nothing in the run path, emits no
events, adds no dependencies (stdlib ``http.server`` only), and does not touch the final report
card. A ``file://`` page cannot ``fetch()`` the JSON (browsers block it), so the subcommand
serves the run dir over ``127.0.0.1`` and prints a URL — that URL is the "link" to hand the user.
The served page reads two already-written files, same origin:

* ``progress.json``  — the atomic snapshot: step rail, liveness, per-agent turn/elapsed/yield.
* ``progress.jsonl`` — the append-only log, folded client-side into each agent's tool-call
  cascade so tools light up pending -> active -> done, like the architecture illustration.

``gpi watch`` remains the authoritative liveness/token source for the agent's chat narration;
this dashboard is an extra browser view and is advisory only (a browser cannot ``os.kill`` a
pid, so it judges liveness by whether the snapshot keeps being rewritten).
"""

from __future__ import annotations

import argparse
import functools
import http.server
from pathlib import Path
from typing import List

from . import __version__

# The canonical pipeline steps (``preflight`` is prepended to STEP_ORDER at run_start;
# see gpi/run_pipeline.py). The page renders its rail from the *actual* ``steps`` array in the
# snapshot — which shrinks under ``--only`` — and only falls back to this list before the first
# snapshot arrives, so the rail is never empty during a cold start.
_CANONICAL_STEPS = [
    "preflight",
    "string_enrichment",
    "gene_summaries",
    "bundle",
    "research",
    "verify",
    "theme",
    "annotate",
    "presentation",
    "html_report",
]

# ------------------------------------------------------------------ CSS (from the style guide)
# Tokens + component classes copied from designs/pipeline-architecture/Pipeline Architecture.html
# so the dashboard reads as continuous with report.html. Only the classes this page uses are kept;
# a handful of live-only rules (bar track, overall header, liveness banner, step meta, failed
# node, lane meta) are added at the end, clearly marked.
_CSS = """
  :root {
    --bg: #f6f7f8; --surface: #ffffff; --surface-soft: #f3f5f5; --surface-sunk: #eef1f1;
    --text: #14201d; --text-soft: #43504c; --muted: #6b7672;
    --border: #e4e7e6; --border-strong: #d2d7d5;
    --accent: #0d9488; --accent-strong: #0f766e; --accent-soft: #e6f6f3; --accent-text: #0b6b61;
    --up: #b8615a; --up-soft: #f4ebe9; --down: #3d7d9e; --down-soft: #e8eff2;
    --ok: #15803d; --ok-soft: #e7f6ec;
    --warn: #b45309; --warn-soft: #fbf1e3;
    --bad: #b91c1c; --bad-soft: #fbeaea;
    --gap: #6b7672; --gap-soft: #eef1f1;
    --shadow: 0 1px 2px rgba(20,32,29,.04), 0 8px 24px -12px rgba(20,32,29,.18);
    --shadow-lift: 0 2px 6px rgba(20,32,29,.06), 0 18px 40px -18px rgba(20,32,29,.28);
    --radius: 14px;
    --font-serif: "Spectral", "Iowan Old Style", Georgia, "Times New Roman", serif;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "Helvetica Neue", sans-serif;
    --font-mono: "IBM Plex Mono", "SF Mono", ui-monospace, Menlo, monospace;
    --rail-w: 58px; --maxw: 1120px;
  }
  :root[data-theme="dark"] {
    --bg: #0c0f0e; --surface: #141917; --surface-soft: #1a201e; --surface-sunk: #0f1413;
    --text: #e8ecea; --text-soft: #b7c0bc; --muted: #8a938f;
    --border: #262d2a; --border-strong: #333b37;
    --accent: #0d9488; --accent-strong: #14b8a6; --accent-soft: #0e2a27; --accent-text: #5eead4;
    --up: #d98e86; --up-soft: #271815; --down: #7fb0c9; --down-soft: #13222a;
    --ok: #4ade80; --ok-soft: #10281a;
    --warn: #fbbf24; --warn-soft: #2a2110;
    --bad: #f87171; --bad-soft: #2a1414;
    --gap: #8a938f; --gap-soft: #1a201e;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 10px 30px -14px rgba(0,0,0,.7);
    --shadow-lift: 0 2px 8px rgba(0,0,0,.5), 0 22px 48px -18px rgba(0,0,0,.8);
  }

  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--font-sans); font-size: 16px; line-height: 1.6;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  }
  a { color: var(--accent-text); text-underline-offset: 2px; }
  .wrap { max-width: var(--maxw); margin: 0 auto; padding: 0 28px; }

  /* controls bar + theme toggle (from the guide) */
  .controls {
    position: sticky; top: 0; z-index: 40;
    background: color-mix(in srgb, var(--bg) 86%, transparent);
    backdrop-filter: saturate(140%) blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .controls-inner {
    max-width: var(--maxw); margin: 0 auto; padding: 12px 28px;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }
  .brand { font-family: var(--font-serif); font-weight: 600; font-size: 18px; letter-spacing: -.01em; }
  .run-id { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }
  .spacer { flex: 1; }
  .btn {
    font-family: var(--font-sans); font-size: 14px; font-weight: 550; text-decoration: none;
    display: inline-flex; align-items: center; gap: 8px;
    padding: 9px 15px; border-radius: 9px; cursor: pointer;
    border: 1px solid var(--border-strong); background: var(--surface); color: var(--text);
    transition: transform .12s ease, box-shadow .12s ease, background .12s ease, border-color .12s ease;
  }
  .btn:hover { border-color: var(--muted); box-shadow: var(--shadow); }
  .btn-primary { background: var(--accent); border-color: var(--accent-strong); color: #fff; }
  :root[data-theme="dark"] .btn-primary { color: #04110f; }
  .btn-primary:hover { background: var(--accent-strong); border-color: var(--accent-strong); }
  .toggle {
    display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
    font-size: 13px; color: var(--text-soft); user-select: none;
    padding: 8px 12px; border-radius: 9px; border: 1px solid var(--border); background: var(--surface);
  }
  .toggle:hover { border-color: var(--border-strong); }

  /* pipeline rail (from the guide) */
  .pipeline { padding: 30px 0 20px; }
  .stage { display: grid; grid-template-columns: var(--rail-w) 1fr; column-gap: 22px; position: relative; }
  .rail { position: relative; display: flex; flex-direction: column; align-items: center; }
  .node {
    width: 46px; height: 46px; flex: 0 0 46px; border-radius: 50%;
    display: grid; place-items: center;
    background: var(--surface); border: 1.5px solid var(--border-strong);
    color: var(--muted); font-family: var(--font-mono); font-size: 13px; font-weight: 600;
    box-shadow: var(--shadow);
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease, background .18s ease, color .18s ease;
    z-index: 2;
  }
  .stage.hl .node { border-color: var(--accent); color: var(--accent-text); background: var(--accent-soft); }
  .stage.is-active .node {
    background: var(--accent); border-color: var(--accent-strong); color: #fff;
    box-shadow: 0 0 0 6px var(--accent-soft), var(--shadow-lift); transform: scale(1.08);
  }
  :root[data-theme="dark"] .stage.is-active .node { color: #04110f; }
  .conn { flex: 1 1 auto; width: 2px; background: var(--border); position: relative; margin: 4px 0 0; overflow: hidden; min-height: 30px; }
  .stage:last-child .conn { display: none; }
  .pulse {
    position: absolute; left: -2px; width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent); opacity: 0; animation: fall 2.6s linear infinite;
  }
  .pulse:nth-child(2) { animation-delay: .9s; }
  .pulse:nth-child(3) { animation-delay: 1.75s; }
  @keyframes fall {
    0% { top: -8px; opacity: 0; } 12% { opacity: .9; } 88% { opacity: .9; } 100% { top: 100%; opacity: 0; }
  }

  /* card (from the guide, trimmed) */
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 20px; margin-bottom: 14px; box-shadow: var(--shadow);
    transition: box-shadow .2s ease, border-color .2s ease;
  }
  .stage.is-active .card { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-soft), var(--shadow-lift); }
  .card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .exec-tag {
    font-family: var(--font-mono); font-size: 11px; font-weight: 500;
    padding: 3px 8px; border-radius: 6px; color: var(--text-soft);
    background: var(--surface-sunk); border: 1px solid var(--border); letter-spacing: .02em; white-space: nowrap;
  }
  .stage-title { font-family: var(--font-serif); font-weight: 600; font-size: 20px; line-height: 1.15; letter-spacing: -.01em; margin: 0; }
  .stage.hl .stage-title { color: var(--accent-text); }

  /* research lanes (from the guide) */
  .lanes { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
  .lane {
    border: 1px solid var(--border); border-radius: 11px; background: var(--surface-soft);
    padding: 12px 13px; position: relative; overflow: hidden;
  }
  .lane.lead { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); box-shadow: 0 0 0 1px var(--accent-soft); }
  .lane-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .lane-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); flex: 0 0 8px; }
  .lane-dot.busy { animation: blink 1.1s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: .3; } }
  .lane-title { font-family: var(--font-mono); font-size: 12px; color: var(--text); font-weight: 600; }
  .lane-title span { color: var(--muted); font-weight: 500; }
  .tools { display: grid; gap: 4px; margin-top: 8px; }
  .tool {
    font-family: var(--font-mono); font-size: 11.5px; color: var(--muted);
    display: flex; align-items: center; gap: 7px; padding: 2px 0; transition: color .2s ease;
  }
  .tool::before { content: ""; width: 5px; height: 5px; border-radius: 1px; background: var(--border-strong); flex: 0 0 5px; transition: background .2s ease, transform .2s ease; }
  .tool.done { color: var(--text-soft); }
  .tool.done::before { background: var(--ok); }
  .tool.active { color: var(--accent-text); font-weight: 600; }
  .tool.active::before { background: var(--accent); transform: scale(1.5); }
  .tool .q { color: var(--muted); font-weight: 400; }

  /* status dot-pills (from the guide; .warn added from the --warn token) */
  .status {
    font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 6px; display: inline-flex; align-items: center; gap: 5px;
  }
  .status::before { content: ""; width: 6px; height: 6px; border-radius: 50%; }
  .status.ok { background: var(--ok-soft); color: var(--ok); } .status.ok::before { background: var(--ok); }
  .status.warn { background: var(--warn-soft); color: var(--warn); } .status.warn::before { background: var(--warn); }
  .status.bad { background: var(--bad-soft); color: var(--bad); } .status.bad::before { background: var(--bad); }
  .status.gap { background: var(--gap-soft); color: var(--gap); } .status.gap::before { background: var(--gap); }

  .bar { height: 9px; border-radius: 5px; background: var(--accent); opacity: .9; transition: width .4s ease; }

  @media (max-width: 680px) {
    :root { --rail-w: 40px; }
    .node { width: 36px; height: 36px; flex-basis: 36px; font-size: 11px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .pulse, .lane-dot.busy { animation: none; }
    .pulse { display: none; }
    .bar { transition: none; }
  }

  /* ---- live-only additions (not in the static guide) ---- */
  .head { padding: 26px 0 6px; }
  .head h1 { font-family: var(--font-serif); font-weight: 500; font-size: clamp(26px, 4vw, 38px); line-height: 1.05; letter-spacing: -.015em; margin: 0; }
  .head .sub { font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); margin-top: 8px; }
  .liveness {
    display: inline-flex; align-items: center; gap: 8px; margin: 14px 0 4px;
    font-family: var(--font-mono); font-size: 12.5px; font-weight: 600;
    padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface-soft); color: var(--text-soft);
  }
  .liveness::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .liveness.run { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 30%, transparent); background: var(--ok-soft); }
  .liveness.run::before { background: var(--ok); animation: blink 1.6s ease-in-out infinite; }
  .liveness.done { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 30%, transparent); background: var(--ok-soft); }
  .liveness.done::before { background: var(--ok); }
  .liveness.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 30%, transparent); background: var(--warn-soft); }
  .liveness.warn::before { background: var(--warn); }
  .liveness.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 30%, transparent); background: var(--bad-soft); }
  .liveness.bad::before { background: var(--bad); }
  .overall { margin: 12px 0 22px; }
  .overall-top { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
  .overall-count { font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--accent-text); }
  .overall-head { font-size: 14px; color: var(--text-soft); }
  .bar-track { height: 9px; border-radius: 5px; background: var(--surface-sunk); overflow: hidden; }
  .step-meta { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); margin: 6px 0 0; }
  .step-meta .d { color: var(--text-soft); }
  .stage.failed .node { border-color: var(--bad); color: var(--bad); background: var(--bad-soft); }
  .lane-meta { font-family: var(--font-mono); font-size: 11px; color: var(--text-soft); }
  .research { margin: 8px 0 20px; }
  .research h2 { font-family: var(--font-serif); font-weight: 600; font-size: 20px; margin: 0 0 4px; }
  .research .rsub { font-family: var(--font-mono); font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .research-foot { font-family: var(--font-mono); font-size: 12px; color: var(--text-soft); margin-top: 14px; }
  .cold {
    margin: 20px 0; padding: 14px 16px; border-radius: 10px;
    border: 1px dashed var(--border-strong); background: var(--surface-soft);
    font-size: 13.5px; color: var(--muted);
  }
"""

# ------------------------------------------------------------------ JS (live consumer, IIFE)
# Polls progress.json (snapshot -> rail/liveness/lanes) and folds progress.jsonl (per-agent tool
# cascade). No external scripts. Mirrors render_card semantics: completed+skipped both count as
# done for the bar; the bar denominator is len(steps), never a hardcoded 10; dollars appear ONLY
# when research.auth === "api"; heartbeat is judged by snapshot-content change, not clock math.
_JS = r"""
(function () {
  "use strict";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* theme (copied from the style guide) */
  var root = document.documentElement;
  var themeToggle = document.getElementById("themeToggle");
  var themeLabel = document.getElementById("themeLabel");
  function applyTheme(t) {
    root.setAttribute("data-theme", t);
    themeLabel.textContent = t === "dark" ? "Light" : "Dark";
    try { localStorage.setItem("gpi-theme", t); } catch (e) {}
  }
  var saved = null;
  try { saved = localStorage.getItem("gpi-theme"); } catch (e) {}
  applyTheme(saved || "dark");
  themeToggle.addEventListener("click", function () {
    applyTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });

  var CANONICAL = __CANONICAL_STEPS__;
  var LABELS = {
    preflight: "Preflight", string_enrichment: "STRING enrichment", gene_summaries: "Gene summaries",
    bundle: "Bundle", research: "Parallel literature research", verify: "Verify citations",
    theme: "Theme", annotate: "Annotate", presentation: "Presentation", html_report: "HTML report"
  };
  function label(name) { return LABELS[name] || name; }
  function pad2(i) { return (i + 1 < 10 ? "0" : "") + (i + 1); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtDur(s) {
    if (s == null || isNaN(s)) return "";
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m" + (s % 60 < 10 ? "0" : "") + (s % 60) + "s";
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h + "h" + (m < 10 ? "0" : "") + m + "m";
  }
  /* program ids arrive as "10" or "P10"; normalize so a lane is always "P10", never "PP10" */
  function pid(id) { return "P" + String(id == null ? "?" : id).replace(/^[Pp]/, ""); }
  function truncate(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  var pipelineEl = document.getElementById("pipeline");
  var researchEl = document.getElementById("research");
  var lanesEl = document.getElementById("lanes");
  var footEl = document.getElementById("researchFoot");
  var livenessEl = document.getElementById("liveness");
  var overallEl = document.getElementById("overallBar");
  var overallCountEl = document.getElementById("overallCount");
  var overallHeadEl = document.getElementById("overallHead");
  var coldEl = document.getElementById("cold");
  var reportBtn = document.getElementById("reportBtn");
  var runIdEl = document.getElementById("runId");

  /* ---- 10-step rail: build once per unique step-name list, then repaint ---- */
  var builtKey = "";
  function buildRail(names) {
    var key = names.join(",");
    if (key === builtKey) return;
    builtKey = key;
    pipelineEl.innerHTML = "";
    names.forEach(function (name, i) {
      var stage = document.createElement("div");
      stage.className = "stage";
      stage.id = "st" + i;
      stage.innerHTML =
        '<div class="rail">' +
          '<button class="node" type="button" aria-label="step ' + (i + 1) + '">' + pad2(i) + "</button>" +
          '<div class="conn"></div>' +
        "</div>" +
        '<div class="card">' +
          '<div class="card-head">' +
            '<span class="exec-tag" data-role="exec"></span>' +
            '<span class="status bad" data-role="err" style="display:none"></span>' +
            '<h2 class="stage-title">' + esc(label(name)) + "</h2>" +
          "</div>" +
          '<p class="step-meta" data-role="meta" style="display:none"></p>' +
        "</div>";
      pipelineEl.appendChild(stage);
    });
  }
  buildRail(CANONICAL);

  function flow(conn, on) {
    var has = conn.childNodes.length > 0;
    if (on && !has && !reduced) conn.innerHTML = '<span class="pulse"></span><span class="pulse"></span><span class="pulse"></span>';
    else if (!on && has) conn.innerHTML = "";
  }

  function paintRail(steps, activeStep) {
    steps.forEach(function (s, i) {
      var stage = document.getElementById("st" + i);
      if (!stage) return;
      var st = s.status;
      stage.className = "stage" +
        (st === "in_progress" ? " is-active" : "") +
        (st === "completed" || st === "skipped" ? " hl" : "") +
        (st === "failed" ? " failed" : "");
      flow(stage.querySelector(".conn"), st === "in_progress");

      var exec = stage.querySelector('[data-role="exec"]');
      var meta = stage.querySelector('[data-role="meta"]');
      var err = stage.querySelector('[data-role="err"]');
      var execTxt = s.executor ? "executor " + s.executor : "";
      if (st === "skipped") execTxt = "skipped";
      exec.textContent = execTxt;
      exec.style.display = execTxt ? "" : "none";

      if (st === "failed" && s.error) { err.textContent = "failed"; err.style.display = ""; }
      else { err.style.display = "none"; }

      var bits = [];
      if (st === "in_progress") {
        if (s.total) bits.push('<span class="d">' + esc(s.current == null ? "" : s.current) + "/" + esc(s.total) + "</span>");
        if (s.detail) bits.push(esc(s.detail));
        if (s.elapsed_s != null) bits.push(fmtDur(s.elapsed_s));
      } else if (st === "failed" && s.error) {
        bits.push('<span class="d">' + esc(truncate(s.error, 160)) + "</span>");
      } else if ((st === "completed" || st === "skipped") && s.elapsed_s != null) {
        bits.push(fmtDur(s.elapsed_s));
      }
      if (bits.length) { meta.innerHTML = bits.join(" · "); meta.style.display = ""; }
      else { meta.style.display = "none"; }
    });
  }

  /* ---- fold progress.jsonl -> ordered tool list per normalized program id ---- */
  function foldTools(text) {
    var byProg = {};
    var lines = text.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (!ln) continue;
      var ev;
      try { ev = JSON.parse(ln); } catch (e) { continue; } /* skip a torn final line */
      if (ev && ev.type === "agent_tool_call") {
        var k = pid(ev.program_id);
        (byProg[k] || (byProg[k] = [])).push({ tool: ev.tool, detail: ev.detail });
      }
    }
    return byProg;
  }

  function paintLanes(research, toolsByProg) {
    var agents = (research && research.agents) || [];
    /* match render_card: lanes are the live agents (running/queued); done roll into the footer */
    var live = agents.filter(function (a) { return a.status === "running" || a.status === "queued"; });
    lanesEl.innerHTML = "";
    live.forEach(function (a, idx) {
      var p = pid(a.program_id);
      var running = a.status === "running";
      var lane = document.createElement("div");
      lane.className = "lane" + (idx === 0 ? " lead" : "");

      var meta = "";
      if (a.status === "queued") {
        meta = "queued · waiting for a slot";
      } else {
        var parts = [];
        parts.push(a.turns != null ? "turn " + a.turns : "starting");
        if (a.elapsed_s != null) parts.push(fmtDur(a.elapsed_s));
        if (a.idle_s != null && a.idle_s >= 30) parts.push("idle " + fmtDur(a.idle_s));
        meta = parts.join(" · ");
      }

      var head =
        '<div class="lane-head">' +
          '<span class="lane-dot' + (running ? " busy" : "") + '"></span>' +
          '<span class="lane-title">agent · <span>' + esc(p) + "</span></span>" +
        "</div>" +
        '<div class="lane-meta">' + esc(meta) + "</div>";

      /* tool cascade: this agent's recent tool calls, folded from progress.jsonl */
      var toolsHtml = "";
      var hist = toolsByProg[p] || [];
      if (running && hist.length) {
        var recent = hist.slice(-6);
        var lastIdx = recent.length - 1;
        toolsHtml = '<div class="tools">' + recent.map(function (t, i) {
          var active = i === lastIdx;
          var q = active && a.current_detail ? ' <span class="q">"' + esc(truncate(a.current_detail, 40)) + '"</span>' : "";
          return '<div class="tool ' + (active ? "active" : "done") + '">' + esc(t.tool) + q + "</div>";
        }).join("") + "</div>";
      } else if (running && a.current_tool) {
        toolsHtml = '<div class="tools"><div class="tool active">' + esc(a.current_tool) +
          (a.current_detail ? ' <span class="q">"' + esc(truncate(a.current_detail, 40)) + '"</span>' : "") + "</div></div>";
      }
      lane.innerHTML = head + toolsHtml;
      lanesEl.appendChild(lane);
    });
    researchEl.style.display = (agents.length || (research && research.n_programs)) ? "" : "none";
    footEl.innerHTML = footer(research);
  }

  function footer(r) {
    if (!r) return "";
    var nprog = r.n_programs || 0;
    var ndone = r.n_done || 0;
    var inc = r.n_incomplete || 0;
    var incS = inc ? ' · <span style="color:var(--warn)">' + inc + " incomplete</span>" : "";
    var turns = 0;
    (r.agents || []).forEach(function (a) { if (a.turns) turns += a.turns; });
    var usage;
    if (r.auth === "api") {
      var cost = typeof r.total_cost_usd === "number" ? " · $" + r.total_cost_usd.toFixed(2) + " API credit" : "";
      usage = turns + " turns" + cost;
    } else {
      usage = turns + " turns · on your Claude subscription (no per-run charge)";
    }
    return ndone + "/" + nprog + " programs done" + incS + " · " + usage;
  }

  /* ---- liveness: judged by snapshot-content change (mirror of watch's mtime heartbeat) ---- */
  var lastText = null, lastChange = 0;
  function paintLiveness(snap) {
    var cls = "liveness", msg;
    if (snap.status === "failed" || snap.failed_step) {
      cls += " bad"; msg = "Run failed" + (snap.failed_step ? " at " + snap.failed_step : "");
    } else if (snap.status === "done") {
      cls += " done"; msg = "Run complete";
    } else {
      var age = Date.now() - lastChange;
      if (age < 15000) { cls += " run"; msg = "Running"; }
      else { cls += " warn"; msg = "No heartbeat for " + fmtDur(age / 1000) + " — check the terminal (gpi watch)"; }
    }
    livenessEl.className = cls;
    livenessEl.textContent = msg;
  }

  function paintOverall(steps, snap) {
    var total = steps.length || 1;
    var done = 0;
    steps.forEach(function (s) { if (s.status === "completed" || s.status === "skipped") done++; });
    overallEl.style.width = Math.round((done / total) * 100) + "%";
    overallCountEl.textContent = "[" + done + "/" + steps.length + "]";
    var active = null;
    steps.forEach(function (s) { if (s.status === "in_progress") active = s; });
    if (snap.status === "done") overallHeadEl.textContent = "done";
    else if (snap.status === "failed") overallHeadEl.textContent = "failed";
    else overallHeadEl.textContent = active ? label(active.name) : "starting…";
  }

  function reportReady(snap) {
    if (snap.status === "done") return true;
    return (snap.steps || []).some(function (s) { return s.name === "html_report" && s.status === "completed"; });
  }

  /* ---- poll loop ---- */
  function tick() {
    fetch("progress.json", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("no snapshot yet");
      return r.text();
    }).then(function (text) {
      var snap;
      try { snap = JSON.parse(text); } catch (e) { throw new Error("snapshot not parseable yet"); }
      if (text !== lastText) { lastText = text; lastChange = Date.now(); }
      coldEl.style.display = "none";

      var steps = (snap.steps && snap.steps.length) ? snap.steps : CANONICAL.map(function (n) { return { name: n, status: "pending" }; });
      buildRail(steps.map(function (s) { return s.name; }));
      paintRail(steps, snap.active_step);
      paintOverall(steps, snap);
      paintLiveness(snap);
      runIdEl.textContent = snap.run_id || "";
      reportBtn.style.display = reportReady(snap) ? "" : "none";

      var research = snap.research || null;
      if (research && (research.n_programs || (research.agents && research.agents.length))) {
        fetch("progress.jsonl", { cache: "no-store" })
          .then(function (r) { return r.ok ? r.text() : ""; })
          .then(function (jt) { paintLanes(research, foldTools(jt || "")); })
          .catch(function () { paintLanes(research, {}); });
      } else {
        researchEl.style.display = "none";
      }
    }).catch(function () {
      /* cold start / not written yet: show the waiting banner, never an error state */
      coldEl.style.display = "";
      livenessEl.className = "liveness";
      livenessEl.textContent = "Waiting for run output…";
    });
  }

  tick();
  setInterval(tick, 1200);
})();
"""


def render_dashboard_html() -> str:
    """Return the self-contained live dashboard page as an HTML string.

    Inline ``<style>`` + inline vanilla ``<script>`` only (no ``<script src=>``). The one external
    reference is the Google-Fonts ``<link>``; every font is a CSS var with system fallbacks, so the
    page degrades gracefully offline. ``<html data-theme="dark">`` is the default; the toggle
    persists to ``localStorage['gpi-theme']``.
    """
    import json

    js = _JS.replace("__CANONICAL_STEPS__", json.dumps(_CANONICAL_STEPS))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en" data-theme="dark">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>gpi · live run</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com" />\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
        '<link href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,400;0,500;0,600;1,400;1,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />\n'
        "<style>" + _CSS + "</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="controls"><div class="controls-inner">'
        '<span class="brand">Gene Program Interpreter</span>'
        '<span class="run-id" id="runId"></span>'
        '<span class="spacer"></span>'
        '<a class="btn btn-primary" id="reportBtn" href="./report.html" style="display:none">View report card →</a>'
        '<label class="toggle" id="themeToggle"><span>◐</span> <span id="themeLabel">Dark</span></label>'
        "</div></div>\n"
        '<div class="wrap">\n'
        '<header class="head"><h1>Live run</h1><div class="sub">polling progress.json · read-only · gpi watch stays authoritative</div></header>\n'
        '<div class="liveness" id="liveness">Waiting for run output…</div>\n'
        '<div class="cold" id="cold" style="display:none">Waiting for run output — a cold first run compiles dependencies for up to ~5 min before the first event; this is normal.</div>\n'
        '<div class="overall">'
        '<div class="overall-top"><span class="overall-count" id="overallCount">[0/10]</span>'
        '<span class="overall-head" id="overallHead">starting…</span></div>'
        '<div class="bar-track"><div class="bar" id="overallBar" style="width:0%"></div></div>'
        "</div>\n"
        '<section class="pipeline" id="pipeline"></section>\n'
        '<section class="research" id="research" style="display:none">'
        '<h2>Parallel literature research</h2>'
        '<p class="rsub">one isolated Agent-SDK session per program, fanned out under a Python semaphore</p>'
        '<div class="lanes" id="lanes"></div>'
        '<div class="research-foot" id="researchFoot"></div>'
        "</section>\n"
        "</div>\n"
        "<script>" + js + "</script>\n"
        "</body>\n"
        "</html>\n"
    )


def write_dashboard(run_dir) -> Path:
    """Write ``render_dashboard_html()`` to ``<run_dir>/dashboard.html`` and return the path."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "dashboard.html"
    out.write_text(render_dashboard_html(), encoding="utf-8")
    return out


def cmd_dashboard(argv: List[str]) -> int:
    """``gpi dashboard <run_dir>`` — write the page, then serve the run dir over http and print a URL.

    Read-only: serves ``<run_dir>`` (which already holds ``progress.json`` / ``progress.jsonl`` and,
    once produced, ``report.html``) so the page can ``fetch()`` them same-origin. Prints exactly one
    machine-readable line — ``Dashboard live -> http://<host>:<port>/dashboard.html`` — so the skill
    can relay the link (and any fallback port) to the user, then blocks until Ctrl-C.
    """
    parser = argparse.ArgumentParser(
        prog="gpi dashboard",
        description="Read-only live HTML view of a run's progress (serves its run dir over http).",
    )
    parser.add_argument("run_dir", help="The run's output directory (contains progress.json).")
    parser.add_argument("--port", type=int, default=8899,
                        help="Port to bind (default 8899; matches .claude/launch.json). "
                             "If taken, an ephemeral port is chosen and printed.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1).")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"gpi dashboard: run directory does not exist yet: {run_dir}")
        return 0

    write_dashboard(run_dir)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(run_dir))
    try:
        httpd = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    except OSError:
        # Port taken (a stale dashboard, another run's server, anything): fall back to an
        # ephemeral port rather than failing, and print whatever port we actually got.
        httpd = http.server.ThreadingHTTPServer((args.host, 0), handler)

    port = httpd.server_address[1]
    # flush=True is load-bearing: the skill launches this in the background with stdout redirected
    # to a file, where Python block-buffers by default. serve_forever() never returns to flush it,
    # so without an explicit flush the "Dashboard live ->" URL the skill must relay would never
    # appear in the log.
    print(f"gpi v{__version__} dashboard: serving {run_dir}", flush=True)
    print(f"Dashboard live -> http://{args.host}:{port}/dashboard.html", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ngpi dashboard: stopped.")
    finally:
        httpd.shutdown()
    return 0
