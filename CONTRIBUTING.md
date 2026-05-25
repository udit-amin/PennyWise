# Contributing to PennyWise

Thanks for considering a contribution. PennyWise is a small project with
a few conventions worth knowing before opening a PR.

## Setup

```bash
git clone https://github.com/udit-amin/PennyWise.git
cd PennyWise
uv sync
cp .env.example .env  # fill in keys; only ANTHROPIC_API_KEY is needed for tests
uv run pytest -q
```

## Architecture rules of thumb

- **Tests must not hit the network.** Use the fixtures under
  `tests/fixtures/` (or add new ones from real responses). Anything that
  needs a live API goes behind a `pytest.mark.live` marker and is
  excluded from the default run.
- **One concern per agent node.** If a node grows to do two things
  (e.g. fetch + transform), split it. The graph in
  `pennywise/graph/workflow.py` should read like a sentence.
- **Pure Python over LLM for math.** Numbers (HHI, weights, P&L) are
  computed in `analytics/`. The LLM only writes prose and picks actions.
- **Structured outputs only.** Every Claude call goes through
  `agents/_llm.py::structured_call`, which uses tool-use to guarantee
  parseable output. No regex on raw model text.

## Coding style

- Type hints everywhere except trivial locals.
- Docstrings explain *why*, not *what* (the code already says what).
- `from __future__ import annotations` at the top of new modules.
- Imports sorted: stdlib, third-party, local — blank lines between.

## Adding a connector

1. Add the HTTP client under `pennywise/connectors/<name>.py`.
2. Record a real response into `tests/fixtures/<name>/`.
3. Write a parser test that loads the fixture and asserts the parsed
   shape — no live calls.
4. Wire it into an agent node only after the parser is green.

## Adding a chat tool

1. Define a `tool_<name>(...)` pure function in `pennywise/chat.py`.
2. Add a matching entry to `TOOL_SPECS` (JSONSchema for inputs) and
   `TOOL_IMPLS` (callable).
3. Add a test in `tests/test_chat.py` patching `_snapshot` if the tool
   reads from the snapshot.
4. The system prompt in `chat.py::SYSTEM` already tells Claude to call a
   tool before stating numbers — no prompt edit needed unless the new
   tool is non-obvious to use.

## Refreshing AMFI market-cap floors

Twice a year (Jan / Jul) AMFI republishes the top-100 and top-250
cutoffs. Update the defaults in `pennywise/config.py` and the comment
in `.env.example` accordingly. The values for H1 2025 are 80,000 Cr /
28,000 Cr.

## Pull requests

- One logical change per PR.
- `uv run pytest -q` must pass.
- Update the README if user-visible behaviour changes.
- For LLM prompt changes, paste a before/after sample in the PR
  description so reviewers can sanity-check the new output shape.
