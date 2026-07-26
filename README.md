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

## Supported clusters

At startup, `node_state` reads the cluster name from `scontrol show config` and runs
the implementation for that cluster. Every implementation groups nodes by CPU count,
memory in whole GB, and GPU configuration, then prints a per-type table of total,
allocated, and available resources. Currently supported:

- `vulcan` — multi-node L40S cluster
- `rcl` — single B200 node partitioned into MIG slices

Any other cluster exits with `Error: unsupported Slurm cluster '<name>'.`

### vulcan

After the resource summary, the Vulcan implementation runs one `sbatch --test-only`
request per GPU node for `aip-xli135`, one L40S GPU, 16 CPUs, 128 GB of memory,
and three hours. It prints a feasibility row for every GPU node, followed by a
resource table containing only the GPU nodes that can run the request.

### rcl

```
192 CPUs / 2011 GB / 4x nvidia_b200 / 8x nvidia_b200_2g.45gb / 4x nvidia_b200_3g.90gb (1/1):
States: MIXED=1
Resource            | Total | Allocated | Available
---------------------------------------------------
CPU (cores)         | 192   | 88        | 104
Memory (GB)         | 2011  | 690       | 1321
nvidia_b200         | 4     | 3         | 1
nvidia_b200_2g.45gb | 8     | 3         | 5
nvidia_b200_3g.90gb | 4     | 2         | 2

Account limits (account=guests, QOS=limited):
Limit               | Value    | In use
---------------------------------------
Jobs running        | 1        | 0
Jobs submitted      | no limit | 0
CPUs per job        | 8        | -
Memory per job (GB) | 64       | -
GPUs per job        | 1        | -

Job feasibility (sbatch --test-only, 4 CPUs, 32G, 1:00:00):
Runnable: 3/4
Request                | Can run | Estimated start     | Partition
------------------------------------------------------------------
1x nvidia_b200         | no      | -                   | -
1x nvidia_b200_2g.45gb | yes     | 2026-07-26T14:30:52 | mig
1x nvidia_b200_3g.90gb | yes     | 2026-07-26T14:30:52 | mig
CPU only (no GPU)      | yes     | 2026-07-26T14:30:52 | cpu

Blocked requests:
  1x nvidia_b200: Limited account 'you': GPUs — only a MIG slice is allowed ...
```

**Account limits** are the caps that actually govern what you can submit, read from
`scontrol show assoc_mgr`. They matter because the resource tables above show the
whole node — including capacity your account is not allowed to request. `Jobs running`
is the QOS `MaxJobsPU` and `Jobs submitted` is `MaxSubmitJobsPU`, so in the example
above only one job runs at a time while any number may sit queued (extras pend with
reason `QOSMaxJobsPerUserLimit`). The remaining rows are the per-job `MaxTRESPJ` caps.

The governing QOS is resolved from data rather than hardcoded: `node_state` looks up
your default association's account, then finds the QOS listing that account under
`Account Limits`. Because a QOS applies the same per-user limits to everyone, the
numbers are still reported correctly when you have no jobs tracked yet. If the limits
cannot be read (no accounting, or `scontrol show assoc_mgr` is restricted), the section
is replaced by a one-line note and the rest of the report is unaffected.

Note that `sacctmgr` and `sacct` may fail from a login shell with "Connection refused"
because `slurmdbd` listens on the controller; this does not mean accounting is off, and
`scontrol show assoc_mgr` still works because it reads slurmctld's in-memory cache.

Two more `rcl` details are worth knowing when reading these numbers:

- **CPU totals are the effective count.** The node reserves cores via `CoreSpecCount`,
  so its 224 CPUs are reported as the 192 the scheduler will actually hand out.
- **GPUs are counted per model.** `CfgTRES` advertises a `gres/gpu` rollup *and* the
  per-model counts that sum to it; only the per-model counts are shown, so the GPUs
  are not double counted.

The feasibility probe submits one request per configured GPU type plus a CPU-only
request. It deliberately passes no `--partition`, because `rcl` routes jobs to `mig`,
`full`, or `cpu` based on the GPU request; the `Partition` column shows where each
request actually landed. Anything rejected is listed under `Blocked requests` with
the scheduler's own explanation — which is how per-account limits (for example
MIG-slice-only GPU access, or per-job CPU and memory caps) surface.

Test-only requests are not submitted as jobs.

The `(1/1)` in the header is the number of included nodes over the total number of nodes of that hardware type.

## Options

| Option | Description |
|---|---|
| `-x, --exclude-states [STATE ...]` | Exclude nodes whose state matches any given value (case-insensitive substring, e.g. `DRAIN` matches `DRAINING`). With no values, applies the preset list `PLANNED DRAIN MAINTENANCE RESERVED ALLOCATED DOWN`. Omit the flag entirely to include all nodes. |
