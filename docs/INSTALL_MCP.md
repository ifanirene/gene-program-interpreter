# Installing the literature MCP servers

The research subagents (Claude Agent SDK, one per program) do **all** literature
research through MCP tools — deterministic code never researches (see
`docs/ARCHITECTURE.md`, guardrail 1). This guide sets up the three read-only
servers `pubmed`, `biorxiv`, `openalex`. The canonical config the SDK sessions are
handed lives in `research/mcp_servers.json`.

Distilled from `literature-tools-for-claude-code.md`.

## Required environment variables

Set these in the repo `.env` (loaded via `os.environ`); `mcp_servers.json` only
carries `${VAR}` placeholders, never real values.

- `NCBI_API_KEY` — optional but recommended for PubMed; lifts the E-utilities rate limit.
- `PUBMED_EMAIL` — contact address for NCBI Entrez.
- `OPENALEX_API_KEY` — **REQUIRED since 2026-02-13.** Keyless OpenAlex calls now return
  409/429. Free at https://openalex.org/settings/api.

```bash
export NCBI_API_KEY="..."       # optional, recommended
export PUBMED_EMAIL="you@example.com"
export OPENALEX_API_KEY="..."   # REQUIRED for OpenAlex
```

## Option A (easiest) — remote `life-sciences` plugins

Zero local install, no keys on your side (Anthropic hosts them). Add the marketplace
first (`/plugin marketplace add ...`), then:

```
/plugin install pubmed@life-sciences
/plugin install biorxiv@life-sciences
```

## Option B — local servers via `claude mcp add`

Run the community servers locally (each runs with your local privileges — pin a
version/commit and review before use).

```bash
# PubMed — Python, NCBI Entrez (search + abstracts + full-text XML)
claude mcp add pubmed -- uvx --from mcp-simple-pubmed mcp-simple-pubmed

# bioRxiv / medRxiv — Python/FastMCP (search + get_biorxiv_metadata by DOI; no key)
claude mcp add biorxiv -- uvx --from git+https://github.com/JackKuo666/bioRxiv-MCP-Server biorxiv-mcp-server

# OpenAlex — Node (full OpenAlex query grammar; needs OPENALEX_API_KEY)
npm install -g openalex-mcp
claude mcp add openalex -- npx openalex-mcp
```

These match `research/mcp_servers.json` exactly (server names `pubmed`, `biorxiv`,
`openalex`, so the allowlist `mcp__pubmed__*`, `mcp__biorxiv__*`, `mcp__openalex__*`
resolves).

## Confirm connection BEFORE any research (spec §8 reliability gate)

Never start a research run until the servers report connected:

```bash
claude mcp list   # servers registered
```

Then inside a Claude Code session:

```
/mcp
```

Verify all three (`pubmed`, `biorxiv`, `openalex`) show **connected** and list their
tools. If any is down, fix it first — a research run against a disconnected literature
server produces silently incomplete evidence.
