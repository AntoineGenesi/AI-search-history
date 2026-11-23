# Contributor entry

Start with the [README](../README.md), [installation](../INSTALL.md), and
[workflow](workflow.md). The [performance note](performance.md) separates
historical measurements from current claims. [Release history](release-history.md)
explains this repository's filtered provenance.

The Python package lives in `src/jsonl_history_search/`. `cli.py` owns the
command entry points. `history_evidence.py` retrieves ordinary history
evidence; `history_answer.py` handles chronological boundaries.
`adapters.py` and `sources.py` own local parsing and discovery, and
`source_expansion.py` implements bounded readback. Skills in `skills/codex/`
and `skills/claude/` operate the package. The portable
`shareable-skills/history-evidence/` route uses ordinary tools instead.

Changes should preserve native references, distinguish a tool call from its
returned outcome, and keep source errors and retrieval caps visible. An
assistant summary cannot establish that an action succeeded.

This release includes runtime development history and curated documentation.
Private evaluation inputs, local orchestration, internal reports and test
fixtures are excluded. Research commands that require those omitted inputs
are not a self-contained public benchmark suite.
