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

### Run as a Slurm batch job

```bash
sbatch node_state.sbatch
sbatch node_state.sbatch -x DOWN DRAIN
```

The report is written to Slurm's standard `slurm-<job-id>.out` file. Arguments
after the script name are forwarded to `node_state`.

At startup, `node_state` reads the cluster name from `scontrol show config` and runs
the implementation for that cluster. Currently supported:

- `vulcan` - groups nodes by CPU count, memory in whole GB, and GPU configuration

After the resource summary, the Vulcan implementation runs one `sbatch --test-only`
request per GPU node for `aip-xli135`, one L40S GPU, 16 CPUs, 128 GB of memory,
and three hours. It prints a feasibility row for every GPU node, followed by a
resource table containing only the GPU nodes that can run the request. Test-only
requests are not submitted as jobs.

It then prints a per-type table of total, allocated, and available resources:

```
224 CPUs / 2011 GB / 16x gpu / 4x nvidia_b200 / ... (1/1):
States: MIXED=1
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
