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
# Run the generic fallback
uvx --from git+https://github.com/yanting-yang/cli node_state

# Run a cluster subcommand
uvx --from git+https://github.com/yanting-yang/cli node_state killarney
```

### Run from a local checkout

```bash
# Summarize all nodes with the generic fallback
uv run node_state

# Use a cluster subcommand
uv run node_state killarney
```

### Run as a Slurm batch job

```bash
sbatch node_state.sbatch
```

The report is written to Slurm's standard `slurm-<job-id>.out` file.

## Supported clusters

With no subcommand, `node_state` runs the generic fallback. Use a cluster
subcommand to run its specialized reporter, for example `node_state killarney`.
Every reporter groups nodes by CPU count, memory in whole GB, and GPU
configuration, then prints a per-type table of total, allocated, and available
resources. Supported subcommands are:

- `killarney` — multi-node L40S and H100 cluster
- `vulcan` — multi-node L40S cluster
- `rcl` — B200 and B300 servers with full GPUs and MIG slices

The fallback reports effective CPUs, memory, and GPU TRES from `scontrol show node`.
It does not run feasibility probes or apply site-specific GPU and account rules.
An unsupported explicit cluster name is rejected with a usage error.

### killarney

Killarney advertises both an untyped `gres/gpu` total and a per-model L40S or
H100 count for each node. The implementation keeps only the per-model count so
GPUs are not double counted in the resource tables.

Between the summaries and the probes it prints the partition table from
`sinfo --Format=Partition,Gres,Nodes,Time`, listing each partition's GRES, node
count, and time limit. That is what makes the `Partition` column of the
feasibility table readable: Killarney routes by duration, and interactive work
lands in `gpubase_interac` rather than a `_b1`-`_b5` batch partition.

It then runs `sbatch --test-only` requests for every GPU count
available on one node (1-8 H100s and 1-4 L40Ss), plus CPU-only requests. Each
request is checked at 3 hours, 12 hours, 1 day, 3 days, and 7 days. It leaves
`--partition` unset so Killarney can route each request to the appropriate
hardware and duration partition. It also tests interactive `srun` feasibility
for 1-4 L40Ss and one CPU-only request at 3 hours. Both commands use
`--test-only`, so no job starts. All results share one table whose `Command`,
`Time`, selected partition, and estimated start columns make the routing visible.

The probes request 4 CPUs and 32 GB of memory by default. Override those resources
and sort the table from earliest to latest estimated start with:

```bash
uv run node_state killarney --cpus-per-task 12 --mem 96G --sort-by-start
```

Rows without an estimated start are placed after runnable rows when sorting is
enabled.

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

The **Account limits** section reports QOS caps from `scontrol show assoc_mgr`, with
a separate table for each QOS matching your default account. They matter because
the resource tables above show the whole node — including capacity your account is
not allowed to request. `Jobs running` is the QOS `MaxJobsPU` and `Jobs submitted`
is `MaxSubmitJobsPU`, so in the example
above only one job runs at a time while any number may sit queued (extras pend with
reason `QOSMaxJobsPerUserLimit`). The per-job rows show `MaxTRESPJ` caps. Per-user
`MaxTRESPU` rows show the total resources a user may hold across running jobs, with
the user's current allocation in the `In use` column.

For example, an `rcl` account using the `normal` QOS can have no per-job resource
caps while still having these per-user caps:

| Resource | Per-user limit |
| --- | ---: |
| CPUs | 64 |
| Memory (GB) | 512 |
| GPUs, all types combined | 4 |
| `nvidia_b200` | 1 |
| `nvidia_b200_2g.45gb` | 3 |
| `nvidia_b200_3g.90gb` | 1 |

The combined GPU limit and each GPU-type limit apply together. These values are
read from Slurm on each run, so the report follows changes to the account or QOS.

`node_state` looks up your default association's account, then prints every QOS
listing that account under `Account Limits`. These matches come from the controller's
cache; they do not identify your default QOS or establish which QOSs you may use.
Because a QOS applies the same per-user limits to everyone, limits can be read from
another user's entry when you have no jobs tracked yet. The report never uses
another user's allocation as yours. Running and submitted job counts come from
`squeue`, filtered to your user and the table's QOS across all accounts. Missing job
limits or usage are marked `?`. If the limits cannot be read (no accounting, or
`scontrol show assoc_mgr` is restricted), the section is replaced by a one-line note
and the rest of the report is unaffected.

Note that `sacctmgr` and `sacct` may fail from a login shell with "Connection refused"
because `slurmdbd` listens on the controller; this does not mean accounting is off, and
`scontrol show assoc_mgr` still works because it reads slurmctld's in-memory cache.

Two more `rcl` details are worth knowing when reading these numbers:

- **CPU totals are the effective count.** The example node reserves cores via
  `CoreSpecCount`, so its 224 CPUs are reported as the 192 the scheduler will
  actually hand out.
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

The `(1/1)` in the header is the number of summarized nodes over the comparison
total. Hardware summaries include every node reported by Slurm.
