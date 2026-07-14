# cli

Cluster inspection utilities for Slurm. Currently provides one command, `node_state`; more may be added over time.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Access to a Slurm cluster (`scontrol` must be on `PATH`)

## Usage

### Run without cloning (uvx)

Run `node_state` directly from this repository with [uvx](https://docs.astral.sh/uv/guides/tools/) — no clone or install needed:

```bash
uvx --from git+https://github.com/yanting-yang/cli node_state

# Options work the same way
uvx --from git+https://github.com/yanting-yang/cli node_state -x
```

### Run from a local checkout

```bash
# Summarize all nodes (no state filtering)
uv run node_state

# Exclude the preset states: PLANNED DRAIN MAINTENANCE RESERVED ALLOCATED DOWN
uv run node_state -x

# Exclude specific states (case-insensitive substring match)
uv run node_state -x DOWN DRAIN
```

`node_state` runs `scontrol show node`, groups nodes by hardware type (CPU count, memory, GPU configuration), and prints a per-type table of total, allocated, and available resources:

```
224 CPUs / 2011 GB / 16x gpu / 4x nvidia_b200 / ... (1/1):
Resource            | Total | Allocated | Available
---------------------------------------------------
CPU (cores)         | 224   | 88        | 136
Memory (GB)         | 2011  | 904       | 1107
gpu                 | 16    | 8         | 8
...
```

The `(1/1)` in the header is the number of included nodes over the total number of nodes of that hardware type.

## Options

| Option | Description |
|---|---|
| `-x, --exclude-states [STATE ...]` | Exclude nodes whose state matches any given value (case-insensitive substring, e.g. `DRAIN` matches `DRAINING`). With no values, applies the preset list `PLANNED DRAIN MAINTENANCE RESERVED ALLOCATED DOWN`. Omit the flag entirely to include all nodes. |
