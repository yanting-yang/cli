# cli

Slurm cluster inspection utilities. Currently one command, `node_state`: a terminal
dashboard, in the spirit of [slurmtop](https://github.com/hunoutl/slurmtop) and
[slmtop](https://github.com/dawnmy/slmtop), that answers "where can I run, under which
account/QOS, and when would it start?"

```
 killarney  fir  tamia
╸━━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 yanting@klogin02  killarney  Slurm 25.05.9                                                     updated 14:41:06 (9s ago) · probed 14:41:08
╭─ Partitions ─────────────────────────────────────────────────────────────────────╮╭─ Accounts & QOS ─────────────────────────────────────╮
│ Partition        Use  Load           Free         Idle   Pend  Max               ││ Account      QOS      Jobs  GPUs  Wall        FS     │
│ gpubase_h100_b1  yes  █████▒▒░  62%  14/80 h100   0/10   36    0-03:00:00        ││ aip-xli135*  interac  0/1   ∞     0-03:00:00  0.08   │
│ gpubase_h100_b2  yes  █████▒░░  66%  14/64 h100   0/8    48    0-12:00:00        ││ aip-xli135*  normal*  0/∞   ∞     ∞           0.08   │
│ gpubase_l40s_b1  yes  ██████▒░  81%  31/672 l40s  0/168  6941  0-03:00:00        ││                                                      │
│ gpubase_l40s_b3  yes  ██████▒░  78%  16/336 l40s  0/84   205   1-00:00:00        ││                                                      │
╰──────────────────────────────────────────────────────────────────────────────────╯╰─────────────────────────── * default · enter: probe ─╯
╭─ Start estimates · aip-xli135/normal ────────────────────────────────────────────╮╭─ My jobs ────────────────────────────────────────────╮
│ Request   0-03:00:00   0-12:00:00   1-00:00:00   3-00:00:00   7-00:00:00         ││ Job                   Name  St  Time  Start / where  │
│ 1x h100    0-08:35:00   0-18:14:00   1-09:39:00  15-08:19:00  64-14:33:00        ││ No jobs in the queue                                 │
│ 8x h100    0-10:36:00   0-20:15:00   1-11:40:00  16-22:36:00  64-14:33:00        ││                                                      │
│ 1x l40s    1-17:02:00   2-06:43:00   0-10:54:00   1-20:17:00   5-20:30:00        ││                                                      │
│ CPU only   1-17:02:00   2-06:43:00   0-10:54:00   1-20:17:00   5-19:38:00        ││                                                      │
╰─────────────────────────────────────────────────────────── sbatch -c4 --mem=32G ─╯╰──────────────────────────────────────────────────────╯
╭─ Details ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ 1x l40s for 1-00:00:00  aip-xli135/normal                                                                                                │
│ Estimated start 2026-10-08 01:35 (0-10:54:00) in partition gpubase_l40s_b3                                                               │
│ $ sbatch --test-only --gres=gpu:l40s:1 -c4 --mem=32G -t1-00:00:00 --wrap="sleep infinity"                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
 r Refresh  p Re-probe  a Account/QOS  e Edit request  m sbatch/srun  c Copy command  n Nodes  q Quit  ] Next cluster  ? Keys  ▏^p palette
```

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A Slurm login node (`scontrol` and `squeue` on `PATH`; estimates need `sbatch`/`srun`)

## Usage

```bash
# Without cloning
uvx --from git+https://github.com/yanting-yang/cli node_state

# From a checkout
uv run node_state                # one tab per cluster: this one, then remote ones
uv run node_state > report.txt   # text report of this cluster when not a terminal
```

There are no options. The first tab is the cluster you are logged in to (from
`ClusterName` in `scontrol show config`), and it is the only one checked at startup.
Every cluster with an `ssh` host in [`clusters.toml`](clusters.toml) (Fir and Tamia
here) gets a tab too, but is only contacted, over ssh, once you open its tab (click it,
or `]`/`[`). Every probe starts as 4 CPUs and 32 GB at each partition time limit with
`sbatch`; change a tab's request with `e` (CPUs, memory, walltimes, extra `sbatch`
options) and `m` (`sbatch`/`srun`). The visible tab refreshes every minute and
re-probes every five.

Probes use `--test-only`, so no job is ever submitted and it is safe to run on a shared
login node. Run it on the login node: on a compute node, such as inside an interactive
job, `srun` probes fail with "Requested operation is presently disabled".

### Remote clusters

Remote tabs run Slurm on the cluster's login node through `ssh`, with your
`~/.ssh/config` (aliases, users, keys). Nothing is sent to a remote cluster until you
open its tab, and only the visible tab refreshes. When you open one and ssh needs a
password or MFA, the dashboard steps aside so ssh can ask in the terminal, then comes
back. It reuses an ssh control master for every later command: yours if your config
sets `ControlMaster`, otherwise its own, which closes 10 minutes after the last use (so
a tab left unopened that long may ask again). If a connection drops later, the tab says
why; press `l` to log in again.

The text report printed when output is not a terminal covers the cluster you are on;
it covers the remote ones only where Slurm is not available.

Each refresh of a remote tab is one ssh session, and the cache of limits is fetched
only for your accounts and QOS, because login nodes can take seconds to start a
session and the full cache can be tens of MB. Commands for a remote cluster are shown
with its host as the prompt (`fir.alliancecan.ca$ sbatch ...`): run them there.

### Keys

| Key | Action |
| --- | --- |
| `]` / `[` | Next / previous cluster tab |
| `1`–`4`, `Tab` | Focus Partitions, Accounts & QOS, Start estimates, My jobs |
| arrows | Move; the Details panel follows the selection |
| `Enter` (Accounts & QOS) | Probe with that account/QOS |
| `a` | Probe with the next account/QOS |
| `e` | Edit the probe request: CPUs, memory, walltimes, extra options such as `--partition` or `--constraint` |
| `m` | Switch between `sbatch` and `srun` probes |
| `c` | Copy the selected estimate's command |
| `n` | Toggle Partitions and Nodes by hardware |
| `p` / `r` | Re-probe / refresh everything now |
| `l` | Log in to a remote cluster again (ssh asks for any password or MFA) |
| `?`, `Ctrl+P` | Key help, command palette (themes) |
| `q` | Quit |

## Reading the dashboard

- **Partitions**: `Use` says whether your accounts, QOS and Unix groups may submit
  there. `Load` is the share of GPUs (CPUs on CPU-only partitions) in use: `█`
  allocated, `▒` unavailable (down, drained, reserved), `░` free. `Free` is free/total
  per GPU type, `Idle` counts wholly idle nodes, `Pend` counts pending jobs from all
  users, and `Max` is the time limit. Details add memory, `AllowAccounts`/`AllowQos`,
  the partition QOS and priority tier.
- **Accounts & QOS**: every account/QOS pair you can submit under; `*` marks what
  commands leave out because Slurm picks it anyway: the account when it is your only
  one (with several, commands always name it, since sites such as Fir require that
  for GPU jobs) and the account's default QOS. `Jobs` is your running jobs over the tightest per-user
  cap, `GPUs` and `Wall` are the tightest GPU and wall-time caps from any scope, and
  `FS` is your fair-share factor. Details list every cap with its usage: QOS per user,
  per account, per job and per node, plus association limits on you and your account.
  `∞` means no cap, `?` unknown.
- **Start estimates**: rows are request sizes (each GPU type up to the tightest GPU cap,
  then CPU only), columns are walltimes. Each cell is how long until the scheduler
  expects the job to start: `now`, a wait such as `0-10:54:00`, `no` when it cannot run
  (Details gives the reason), or `-` above the account/QOS wall-time cap. The earliest
  start in each row is underlined. Here a 1-day L40S job starts sooner than a 3-hour
  one, because short jobs queue in the busier `b1` partition.
- **Details** for an estimate shows the partition it would land in and the exact
  command. Drop `--test-only` to submit it: `sbatch` holds the allocation with
  `sleep infinity` until the time limit; open a shell in it with
  `srun --jobid=<id> --overlap --pty $SHELL` and release it with `scancel <id>`.
  `--account`/`--qos` are left out when Slurm would pick them anyway.
- **My jobs** lists your queued and running jobs, with the scheduler's expected start
  and reason for pending ones.

## Other clusters

Nothing is hardcoded per cluster: partitions, GPU types, time tiers, accounts and
limits are read from Slurm. A profile records only what Slurm cannot report, in
[`clusters.toml`](clusters.toml) at the repository root, keyed by the cluster name. It
covers `rcl` (one 1-hour walltime, since it routes by GPU request, and its
storage-policy banner), `fir` (reached over ssh; its memory-unit note) and `tamia`
(reached over ssh; whole-node GPU requests, a 1-day cap, `srun` needing a program). To
add a cluster, or a tab for one you are not logged in to, add a table there:

```toml
[clusters.mycluster]          # the ClusterName from `scontrol show config`
walltimes = ["0-03:00:00", "1-00:00:00"]
gpu_counts = "all"            # "pow2" (1, 2, 4, ... default), "all" or "whole_node"
srun_program = ["bash"]       # if srun probes fail with "No partition specified"
message_noise = ['NOTE: .*?\.']   # regexes stripped from rejection reasons
ssh = "login.mycluster.org"   # a tab for it from anywhere; host or ~/.ssh/config alias
```

### When `sacctmgr` fails

On some login nodes `sacctmgr` fails with "Connection refused" because `slurmdbd` runs
on the controller. Accounting is still enforced: `node_state` reads limits from
`scontrol show assoc_mgr` instead. Without `sacctmgr` it cannot list your QOS
assignments, so it test-submits each cached account/QOS pair and hides the ones Slurm
rejects.

## Development

```bash
uv sync
uv run python -m unittest discover -s tests -t tests
uvx ruff check src tests
```

See [AGENTS.md](AGENTS.md) for the code layout and site-specific Slurm quirks.
