# cli

Slurm cluster inspection utilities. Currently one command, `node_state`, which shows
what hardware is free and which jobs you could run right now.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A Slurm login node (`scontrol` on `PATH`; probes also need `sbatch`/`srun`)

## Usage

```bash
# Without cloning
uvx --from git+https://github.com/yanting-yang/cli node_state rcl

# From a checkout
uv run node_state          # generic resource summary for any cluster
uv run node_state rcl      # cluster-specific report: killarney, rcl, tamia, vulcan
```

`killarney`, `rcl` and `tamia` accept options for their feasibility probes:

```bash
uv run node_state rcl -c8 --mem 64G --sort-by-start
```

| Option | Default | Effect |
| --- | --- | --- |
| `-c` | `4` | CPUs requested by each probe |
| `--mem` | `32G` | Memory requested by each probe |
| `--sort-by-start` | off | Sort by estimated start; unrunnable requests last |

Probes use `--test-only`, so no job is ever submitted and it is safe to run on a
shared login node. Run it on the login node: on a compute node, such as inside an
interactive job, the `srun` probes fail with "Requested operation is presently
disabled".

## Clusters

Every report starts with a table per hardware type of total, allocated and available
CPUs, memory and GPUs, headed by the type's node count, e.g. `(1/1)`.

| Subcommand | Adds |
| --- | --- |
| *(none)* | Nothing else; no site-specific rules |
| `killarney` | Partition table, account limits, and `sbatch` probes for 1–8 H100s, 1–4 L40Ss and CPU-only at 3 h, 12 h, 1 d, 3 d and 7 d, plus 3 h `srun` probes for 1–4 L40Ss and CPU-only |
| `tamia` | As Killarney, but GPUs are probed only as whole nodes and only up to the 1-day walltime cap |
| `rcl` | Account limits and one-hour `sbatch`/`srun` probes for each account/QOS you can use |
| `vulcan` | One `sbatch` probe per GPU node (1 L40S, 16 CPUs, 128 GB, 3 h), then a summary of the nodes that can run it (runnable/all GPU nodes) |

Killarney and Tamia route jobs by duration, so their tables include `Time` and
`Partition` columns to show where each request would land.

## Reading an `rcl` report

```
224 CPUs / 2011 GB / 4x nvidia_b200 / 8x nvidia_b200_2g.45gb / 4x nvidia_b200_3g.90gb (1/1):
States: MIXED=1
Resource            | Total | Allocated | Available
---------------------------------------------------
CPU (cores)         | 224   | 132       | 92
Memory (GB)         | 2011  | 848       | 1163
nvidia_b200         | 4     | 2         | 2
nvidia_b200_2g.45gb | 8     | 6         | 2
nvidia_b200_3g.90gb | 4     | 3         | 1

Account limits (account=guests, QOS=limited):
Limit                        | Value    | In use
------------------------------------------------
Jobs running                 | 1        | 0
Jobs submitted               | no limit | 0
CPUs per job                 | 8        | -
Memory per job (GB)          | 64       | -
GPUs per job                 | 1        | -
nvidia_b200 per job          | 1        | -
nvidia_b200_2g.45gb per job  | 1        | -
nvidia_b200_3g.90gb per job  | 1        | -
GPUs per user                | 1        | 0
nvidia_b200 per user         | 1        | 0
nvidia_b200_2g.45gb per user | 1        | 0
nvidia_b200_3g.90gb per user | 1        | 0

Job feasibility (account=guests, QOS=limited):
Runnable: 6/8
Request               | Can run | Estimated start     | Run command
--------------------------------------------------------------------------------------------------------------------------------------------------------------
nvidia_b200:1         | no      | -                   | sbatch --test-only --gres=gpu:nvidia_b200:1 -c4 --mem=32G -t0-01:00:00 --wrap="sleep infinity"
nvidia_b200_2g.45gb:1 | yes     | 2026-10-01T14:40:30 | sbatch --test-only --gres=gpu:nvidia_b200_2g.45gb:1 -c4 --mem=32G -t0-01:00:00 --wrap="sleep infinity"
nvidia_b200_3g.90gb:1 | yes     | 2026-10-01T14:40:30 | sbatch --test-only --gres=gpu:nvidia_b200_3g.90gb:1 -c4 --mem=32G -t0-01:00:00 --wrap="sleep infinity"
cpu                   | yes     | 2026-09-28T14:40:30 | sbatch --test-only -c4 --mem=32G -t0-01:00:00 --wrap="sleep infinity"
nvidia_b200:1         | no      | -                   | srun --test-only --gres=gpu:nvidia_b200:1 -c4 --mem=32G -t0-01:00:00 --pty zsh
nvidia_b200_2g.45gb:1 | yes     | 2026-10-01T14:40:30 | srun --test-only --gres=gpu:nvidia_b200_2g.45gb:1 -c4 --mem=32G -t0-01:00:00 --pty zsh
nvidia_b200_3g.90gb:1 | yes     | 2026-10-01T14:40:30 | srun --test-only --gres=gpu:nvidia_b200_3g.90gb:1 -c4 --mem=32G -t0-01:00:00 --pty zsh
cpu                   | yes     | 2026-09-28T14:40:30 | srun --test-only -c4 --mem=32G -t0-01:00:00 --pty zsh

Blocked requests:
  sbatch nvidia_b200:1 for 0-01:00:00: Limited account 'you': GPUs - only a MIG slice is allowed ...
  srun nvidia_b200:1 for 0-01:00:00: Limited account 'you': GPUs - only a MIG slice is allowed ...

Run command: drop --test-only to submit; sbatch then holds the allocation with 'sleep infinity' until the time limit, and srun opens a Zsh shell.
Open a shell in an sbatch allocation with 'srun --jobid=<jobid> --overlap --pty zsh'; release it with 'scancel <jobid>'.
```

- **Resource tables** show the whole node, including capacity your account cannot
  request. CPUs are the effective count after reserved cores.
- **Account limits** come from your QOS: running and submitted jobs per user, and
  CPU/memory/GPU caps per job and per user, with your current usage under `In use`.
  `no limit` means the QOS sets no cap; `?` means the value could not be read.
- **Job feasibility** follows each limits table. It probes 1 up to N of each GPU type,
  where N is the QOS's tightest GPU cap (never more than one node holds), plus a
  CPU-only request.
- **Run command** is the exact probe. Remove `--test-only` to run it. `-c` and `-t`
  are short for `--cpus-per-task` and `--time`. `--account` and `--qos` are left out
  when Slurm would pick them anyway (your default account, or the account's only
  QOS), as for `guests`/`limited` above.
- **Blocked requests** gives the scheduler's reason for each rejection, which is how
  site rules such as MIG-slice-only access show up.

## When `sacctmgr` fails

On some login nodes `sacctmgr` fails with "Connection refused" because `slurmdbd`
runs on the controller. Accounting is still enforced: `node_state` reads limits
from `scontrol show assoc_mgr` instead. Without `sacctmgr` it cannot list your QOS
assignments, so `rcl` test-submits each cached account/QOS pair and hides the ones
Slurm rejects, while `killarney` falls back to the QOSs cached for your accounts,
which may be incomplete.

## Development

```bash
uv sync
uv run python -m unittest discover -s tests -t tests
```

See [AGENTS.md](AGENTS.md) for the code layout and site-specific Slurm quirks.
