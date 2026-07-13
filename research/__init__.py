"""
research — the parallel literature-research subsystem (executor 2).

One isolated Claude Agent SDK session per gene program, fanned out under a
Python asyncio orchestrator with a concurrency semaphore, each searching
external PubMed/OpenAlex/bioRxiv MCP servers, followed by a deterministic
evidence verifier. Literature research happens ONLY here, in the agents, via MCP.
"""

__version__ = "0.1.0"
