# AI Search History

Find what you said, why a decision changed, or whether an AI agent actually ran the tests it claimed to run.

AI conversations outlive their context windows. This project helps Codex and Claude Code find the original messages, tool calls and results in local history, then answer with source references. An assistant recap is a lead; the original record is the evidence.

## Choose a mode

| Mode | How it works | Choose it when… |
| --- | --- | --- |
| **Skill only** | `history-evidence` teaches your agent to search files, JSON and SQLite using ordinary tools. No Python package required. | You want the smallest install or need to inspect different history formats. |
| **Skill + CLI** | The agent uses `convo` to retrieve bounded transcript passages, source references and coverage limits. | You want a dedicated local retrieval tool for repeated history questions. |

Both work with Codex and Claude Code. The standalone skill also includes [source guides](shareable-skills/history-evidence/references/provider-matrix.md) for other agents and stores; those guides do not imply equivalent CLI support. The CLI includes local Claude Desktop source parsing, but no Desktop plugin or MCP server.

## Install

Clone once, then choose either route. The package is installed from source; it is not on PyPI.

```bash
git clone https://github.com/AntoineGenesi/AI-search-history.git
cd AI-search-history
```

**Skill only:** copy the whole skill folder, including its references. Choose your agent's directory:

```bash
skills_dir="$HOME/.agents/skills"       # Codex
# skills_dir="$HOME/.claude/skills"     # Claude Code
mkdir -p "$skills_dir"
mkdir "$skills_dir/history-evidence" &&
  cp -R shareable-skills/history-evidence/. "$skills_dir/history-evidence/"
```

The destination must be new; this leaves an existing skill untouched. Start a new agent session and ask it to use `history-evidence`.

**Skill + CLI:** requires Python 3.8+; current pipx requires Python 3.10+.

```bash
pipx install .
convo install-agent-skill --agent codex
# Or: convo install-agent-skill --agent claude
```

This installs the CLI guidance under `~/.agents/skills` for Codex or `~/.claude/skills` for Claude Code. See [installation options](INSTALL.md) for virtual environments and custom paths.

## Use it

Ask your agent normally, naming a project, phrase, date or event:

> Use history-evidence to find my original instructions about the release checklist.

> Use convo to check whether the agent ran tests before claiming the parser fix was done.

> What happened in the parent thread after my message about the deployment failure? Use convo.

With the CLI, you can also retrieve evidence directly from your project directory:

```bash
convo ask "What did I say about the release checklist?" --cwd "$PWD" --format text
```

The workflow is: find candidates → read the original passages → answer with references and missing scope. The CLI retrieves evidence; your agent interprets it. Missing matches do not prove something never happened.

## Performance

A historical 17-question comparison measured model-facing byte proxies against manual search:

| Mode | Fewer proxy bytes |
| --- | ---: |
| Skill only | 42.6% |
| Skill + CLI | 52.9% |

Skill-only answers received 14 passes, 2 partials and 1 failure; 10 runs separately failed the benchmark's process gates. These are historical measurements, not billing savings or current-version guarantees. A newer 23-question diagnostic does not establish a clean overall winner. [Results and limits](docs/performance.md).

Retrieval can miss paraphrases and incomplete histories. Keep the returned scope and caps alongside the answer.

[Workflow guide](docs/workflow.md) · [Contributor entry](docs/INDEX.md) · [MIT license](LICENSE)
