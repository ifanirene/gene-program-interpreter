# Literature research tools → Claude Code

This bundle ports the literature-research capability from Claude Science to your Claude Code.
Two mechanisms are involved, and they install differently:

- **`literature-review` skill** — a SKILL.md folder. Drops in directly. (Included in this bundle.)
- **MCP servers** for PubMed / bioRxiv / OpenAlex / arXiv — you add running servers via `claude mcp add`. (Recommendations below.)

---

## 1. Install the `literature-review` skill

Unzip `literature-review.zip` into your Claude Code skills directory:

```bash
# user-global (available in every project)
unzip literature-review.zip -d ~/.claude/skills/

# OR project-local (checked into one repo)
unzip literature-review.zip -d .claude/skills/
```

You should end up with `~/.claude/skills/literature-review/SKILL.md` and `.../kernel.py`.
Claude Code auto-discovers it — no config edit needed.

### Important: the OpenAlex key

`kernel.py` calls the OpenAlex API in `search_openalex()` and `expand_citations()`.
OpenAlex has **required an API key since 2026-02-13** (keyless calls return 409/429).
In Claude Science the key is injected automatically; in Claude Code you must supply it yourself:

```bash
export OPENALEX_API_KEY="your_key"   # free at https://openalex.org/settings/api
```

Put it in your shell profile so the kernel picks it up. The other helpers degrade gracefully:
- `litrev_contact()` (CrossRef/doi.org polite-pool email) returns `None` if unavailable — fine.
- `verify_dois()`, `crossref_lookup()`, `expand_citations` backward-refs via CrossRef, and `style_pass()` (pure-regex prose lint) work with **stdlib only**, no key.

`fetch_article_fulltext` does **not** port — it is a Claude Science platform built-in tied to your
institutional EZproxy/publisher credentials. There is no skill/MCP equivalent; the closest analog
would be a small custom MCP server pointed at your own proxy.

---

## 2. Add MCP servers for PubMed / bioRxiv / OpenAlex / arXiv

The `mcp-pubmed`, `mcp-biorxiv`, `mcp-literature` connectors you see in Claude Science are
**server-side** connectors Anthropic hosts; their in-session "docs" are method references, not
installable packages. For Claude Code you attach equivalent community servers. Vetted picks below
(all cover the same public APIs — PubMed E-utilities, bioRxiv API, OpenAlex, arXiv).

> Caveat: these are third-party community servers, not the exact implementations behind the
> Claude Science connectors. Tool names and coverage differ somewhat. Pin a version/commit and
> review the code before use — an MCP server runs with your local privileges.

### Easiest path — the `life-sciences` plugin marketplace (remote, zero-config)

There is a Claude Code plugin marketplace that ships **remote** MCP servers (nothing to run locally):

```
/plugin install pubmed@life-sciences
/plugin install biorxiv@life-sciences
```

Remote servers need no Python/Node install or API keys on your side. Confirm the marketplace is
added in your Claude Code (`/plugin marketplace add ...`) before installing; inspect what each
plugin exposes with `/mcp` after install.

### PubMed (local)

```bash
# Python, NCBI Entrez — search + abstracts + full-text XML (set your NCBI creds for higher rate)
export PUBMED_EMAIL="you@example.com"
export NCBI_API_KEY="your_ncbi_key"     # optional but recommended
claude mcp add pubmed -- uvx --from mcp-simple-pubmed mcp-simple-pubmed
```
Alternatives with richer toolsets: `chrismannina/pubmed-mcp` (citation export in BibTeX/RIS/APA…),
`Augmented-Nature/PubMed-MCP-Server` (16 tools spanning E-utilities + PMC).

### bioRxiv / medRxiv (local)

```bash
# Python/FastMCP — search + get_biorxiv_metadata by DOI; no API key needed
claude mcp add biorxiv -- uvx --from git+https://github.com/JackKuo666/bioRxiv-MCP-Server biorxiv-mcp-server
```
`openpharma-org/biorxiv-mcp` (Node) is a good alternative — single `biorxiv_info` tool with 7
methods including preprint→published mapping and funder-by-ROR search, mirroring the Claude Science
connector closely.

### OpenAlex (local)

```bash
# Node — full OpenAlex query grammar over works/authors/sources/institutions/…
npm install -g openalex-mcp
claude mcp add openalex -- npx openalex-mcp
```
Richer option: `oksure/openalex-research-mcp` (31 tools — citation traversal, review/seminal-paper
finders, `batch_resolve_references`, topic-trend analysis). Set a contact email env var
(`OPENALEX_MAILTO` / `OPENALEX_EMAIL`, per that server's README) for the polite pool.

### arXiv (local or remote)

```bash
# Remote (official-ish, zero-config): full-text search, PDF Q&A, repo reading
claude mcp add --transport http alphaxiv https://api.alphaxiv.org/mcp/v1

# OR local Python (blazickjp) — search + metadata + full-text; use `uv tool install`, NOT pip/npm
uv tool install arxiv-mcp-server
claude mcp add arxiv -- arxiv-mcp-server
```

---

## Verify

After adding servers, list and inspect them:

```bash
claude mcp list          # servers registered
```
Inside a Claude Code session, run `/mcp` to see connection status and the tools each server exposes.
For the skill, ask Claude "what skills are available" or trigger it with a literature task.

## Scope note

| Capability | Claude Science | Ported to Claude Code |
|---|---|---|
| `literature-review` methodology skill | built in | ✅ this zip (needs `OPENALEX_API_KEY`) |
| PubMed search/metadata/PMC | `mcp-pubmed` connector | ✅ community MCP server |
| bioRxiv/medRxiv | `mcp-biorxiv` connector | ✅ community MCP server |
| OpenAlex + arXiv | `mcp-literature` connector | ✅ community MCP servers |
| Credentialed full-text by DOI (EZproxy) | `fetch_article_fulltext` built-in | ❌ no equivalent (custom server only) |
