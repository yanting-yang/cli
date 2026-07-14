# AGENTS.md

## Project overview

A Python CLI (project name `cli`) with utilities for inspecting a Slurm cluster. Each utility is exposed as its own console script so it can be run directly (e.g. `uv run node_state`); new commands should follow the same pattern rather than being nested under a single umbrella executable.

Current commands:

- `node_state` — parses `scontrol show node`, groups nodes by hardware type (CPU count, memory, GPU config), and prints total/allocated/available resource tables.

## Layout

- [pyproject.toml](pyproject.toml) — project metadata; console scripts live under `[project.scripts]`
- [src/node_state/cli.py](src/node_state/cli.py) — argument parsing and entry point for `node_state`
- [src/node_state/node_resources.py](src/node_state/node_resources.py) — `scontrol` output parsing and table rendering

## Commands

```bash
uv sync                      # install/refresh the environment
uv run node_state            # run against the local Slurm cluster (needs scontrol)
uv run node_state -x         # exclude preset states (PLANNED DRAIN MAINTENANCE RESERVED ALLOCATED DOWN)
uv run node_state -x DOWN    # exclude specific states (case-insensitive substring match)
```

## Conventions and gotchas

- Python 3.12 only (`requires-python = "==3.12.*"`), managed with `uv`; build backend is `uv_build`.
- Command names use underscores (`node_state`), not hyphens.
- No test suite yet. Verifying `node_state` requires a machine where `scontrol` works; `parse_nodes` / `parse_tres_gpus` in [node_resources.py](src/node_state/node_resources.py) are pure functions that can be exercised with captured `scontrol show node` text.
- State exclusion is opt-in: `node_resources.main(exclude_states=None)` includes all nodes. The preset exclude list lives in [cli.py](src/node_state/cli.py) (`PRESET_EXCLUDE_STATES`); keep the argparse help text in sync when changing it.
- Exclusion matches states as case-insensitive substrings, so `DRAIN` also matches `DRAINING`, and compound states like `MIXED+PLANNED` match `PLANNED`.
