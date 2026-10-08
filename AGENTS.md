# AGENTS.md

## Project overview

A Python CLI (project name `cli`) with utilities for inspecting a Slurm cluster. Each utility is exposed as its own console script so it can be run directly (e.g. `uv run node_state`); new commands should follow the same pattern rather than being nested under a single umbrella executable.

Current commands:

- `node_state` — a [Textual](https://textual.textualize.io/) dashboard in the style of `htop`/`slurmtop`: partitions, the caller's account/QOS limits, a grid of estimated start times from `--test-only` probes, and the caller's jobs. One tab per cluster: the local one (if Slurm runs here), then each `clusters.toml` entry with an `ssh` host, read over ssh. Only the local cluster is checked at startup; a remote one is first contacted when its tab is opened (`ClusterView.activate`), and only the visible tab refreshes. When output is not a terminal it prints the same panels once instead. It takes no options: the cluster is detected from `scontrol`, and the probe request is edited in the dashboard. It works on any cluster: everything comes from Slurm, and the few site rules Slurm cannot report live in data profiles.

## Layout

- [pyproject.toml](pyproject.toml) — project metadata; console scripts live under `[project.scripts]`
- [src/node_state/cli.py](src/node_state/cli.py) — entry point (no options besides `--help`); lists the clusters (`find_clusters`), then runs the dashboard or, when output is not a terminal, the text report of the local cluster (remote ones, after `connect`, only when Slurm is not available here)
- [src/node_state/hosts.py](src/node_state/hosts.py) — where commands run: `Local` (subprocess) or `Remote` (ssh). `run_batch` runs a refresh's commands in parallel, in one ssh session for a remote; `run_stream` reports probe results as they land, over a few ssh sessions
- [src/node_state/slurm.py](src/node_state/slurm.py) — the `scontrol`/`squeue`/`sacctmgr`/`sbatch`/`srun` command lines (`*_COMMAND`, `*_command`, `test_command`) and the pure parsers for their output
- [clusters.toml](clusters.toml) — the per-cluster profiles, as data; `src/node_state/clusters.toml` is a symlink to it so the build copies it into the package (keep the link: without it an installed copy has no profiles)
- [src/node_state/profiles.py](src/node_state/profiles.py) — `Profile` (walltimes, GPU count mode, srun program, message noise) and loading/validating `clusters.toml`
- [src/node_state/snapshot.py](src/node_state/snapshot.py) — `ClusterInfo.detect(host)`; `Collector(info, host)` builds a `Snapshot`: partition and hardware usage, account/QOS `Scope`s and their `Limit`s (QOS and association), and the caller's jobs; `Cluster` pairs a tab name, profile and host
- [src/node_state/probes.py](src/node_state/probes.py) — the estimates `Grid`: request sizes × walltimes, run concurrently with `--test-only`
- [src/node_state/views.py](src/node_state/views.py) — plain-text cells shared by both front ends
- [src/node_state/tui.py](src/node_state/tui.py) — the Textual app (`NodeStateApp`: tabs, global keys), one `ClusterView` per tab (its state, panels, workers and keys) and the probe-request dialog
- [src/node_state/report.py](src/node_state/report.py) — the text report printed when output is not a terminal
- [tests/](tests/) — `unittest` suite; one module per source module, plus `fakes.py` (`FakeHost`, canned command output)

## Commands

```bash
uv sync                          # install/refresh the environment
uv run node_state                # dashboard for the cluster you are on
uv run node_state | less         # one-shot text report of this cluster
uv run python -m unittest discover -s tests -t tests   # run the tests
```

Note the `-t tests` on the test command: `tests/` has no `__init__.py`, so discovery fails without it. `test_report` and `test_tui` import fixtures from `test_snapshot`/`test_report`, which also relies on `-t tests`.

## Conventions and gotchas

- Python 3.12 only (`requires-python = "==3.12.*"`), managed with `uv`; build backend is `uv_build`. Textual is the only runtime dependency, and `cli.py` imports it only for the dashboard.
- Command names use underscores (`node_state`), not hyphens.
- Lint with `uvx ruff check src tests`.
- Nothing outside `hosts.py` starts a process; everything else receives a host and calls `run`/`run_batch`/`run_stream`. Commands never read the terminal (`stdin=DEVNULL`, `ssh -T`), or ssh would steal the dashboard's keystrokes; only `Remote.login` is interactive: under `App.suspend()` when an opened remote tab cannot connect (automatically once per opening, then with `l`), or before a text report.
- Every duration a panel shows (time limits, wall caps, walltime columns, waits, job times) is Slurm's `D-HH:MM:SS`, via `slurm.format_duration` (minutes) or `slurm.normalize_time` (squeue strings). `∞` marks no limit.

### Supporting a new cluster

Usually nothing is needed: the cluster name comes from `scontrol show config`, and partitions, GPU types, time tiers, accounts and limits are all read from Slurm. Add a profile only for rules Slurm does not report, as a `[clusters.<ClusterName>]` table in [clusters.toml](clusters.toml), which documents each setting:

- `walltimes` — when partition `MaxTime`s do not mark routing tiers (e.g. `rcl` routes by GPU request, so one hour is enough).
- `gpu_counts` — `pow2` (default), `all`, or `whole_node` for sites that reject partial GPU nodes (`tamia`).
- `srun_program` — for submit filters that only route an `srun` naming a program (`tamia` needs `bash`).
- `message_noise` — regexes for submit-filter banners in rejection messages; ANSI codes, `sbatch:`/`srun:` prefixes and `-----`/`=====` rules are already stripped.

Do not add per-cluster code paths; if a cluster needs logic rather than data, generalize it in `snapshot`/`probes` behind a profile setting.

Slurm reports the same facts differently per site; the generic code already handles these:

- **Reserved cores.** `slurm.parse_nodes` records `cpu_tot` (`CPUTot`) and `cpu_efctv` (`CPUEfctv`, falling back to `CPUTot`). Usage and hardware groups always use `cpu_efctv`, so `CoreSpecCount` cores never show as free.
- **GPU rollups.** `CfgTRES` may list an untyped `gres/gpu=N` alongside per-model entries summing to the same `N`. `parse_tres_gpus` returns both; `Collector` calls `normalize_gpu_rollups` on every node so GPUs are not counted twice.
- **Unavailable nodes.** `slurm.node_available` checks every state word (`IDLE+DRAIN`, `DOWN+NOT_RESPONDING`...); unallocated capacity on such nodes is "unavailable", not free.
- **Partition routing.** Probes never pass `--partition` unless the user adds it; Alliance clusters and `rcl` route jobs by duration or GPU request. Default walltimes are the distinct `MaxTime`s of usable partitions (`plan_walltimes`), which is where routing changes.
- **Probe sizes.** Each GPU type is probed up to the tightest GPU cap in the scope's limits (`max_gpu_counts`: per job, user, account or node, on the model or all GPUs), never above one node's capacity. Walltimes above the scope's wall cap are skipped, not probed.
- **Run commands.** `slurm.test_command` and `format_run_command` both use `slurm.SBATCH_WRAP` (`--wrap="sleep infinity"`) instead of a batch script, so a displayed `sbatch` command is exactly the probed request; `srun` commands append `--pty <login shell>` (the remote user's shell on a remote cluster). Keep them in step when changing either.
- **Default account/QOS.** `Scope.directives()` leaves out `--account`/`--qos` when Slurm would pick them anyway: the account when it is the caller's only one, and the account's default QOS (`default_qos` mirrors slurmctld: `DefaultQOS`, else the only QOS, else `normal`). With several accounts `--account` is always passed: Fir's submit filter rejects GPU jobs that leave it to the default ("You are associated with multiple _gpu allocations"). `Scope.user_default` still marks the `DefAssoc=Yes` account, which picks the initially probed pair.
- **Preemptible partitions** (`PreemptMode` other than `OFF`, such as Fir's 122-day `cpupreempt`) are not routing tiers and do not add walltime columns.
- **GPU names.** `slurm.gpu_display_names` shortens MIG and vendor names for display (`nvidia_h100_80gb_hbm3_1g.10gb` → `h100 1g.10gb`), keeping full names where short ones would collide; `--gres` always uses the real type.

### Reading account limits

`sacctmgr` works on Killarney and supplies the caller's account/QOS assignments (with `DefaultQOS`).
On some sites, `sacctmgr` and `sacct` fail from a login shell when `slurmdbd` listens on the controller rather than locally ("Connection refused" to `localhost:6819`). That is **not** evidence that accounting is disabled — check `AccountingStorageEnforce` in `slurm.conf`, which is world-readable. Use `scontrol show assoc_mgr flags=assoc,qos` instead: it reads slurmctld's in-memory cache and works unprivileged. Its `users=` filter does not filter, but `accounts=` and `qos=` do: the whole cache is fetched only to discover accounts when `sacctmgr` fails, and later refreshes ask for the caller's accounts and QOS alone.

When `sacctmgr` fails, `discover_scopes` pairs every account from `parse_user_accounts` with every cached QOS and drops each pair whose minimal CPU-only `sbatch --test-only` is rejected as an invalid account or QOS (`accepted_pairs`, all pairs in one stream). A QOS a partition refuses is kept: that reply is not "Invalid".

These quirks of the `assoc_mgr` output shape the parsers in [slurm.py](src/node_state/slurm.py) and `scope_limits` in [snapshot.py](src/node_state/snapshot.py):

- Association records carry **no** `QOS=` field. QOS `Account Limits` blocks track account usage, not permissions: an allowed QOS may omit the caller's account entirely. Cache membership neither identifies the default QOS nor establishes which QOSs the user may use.
- `MaxJobsPA` / `MaxSubmitJobsPA` / `MaxTRESPA` under `Account Limits`, and `MaxWallPJ` (minutes) / `MaxTRESPN` at QOS scope. Account usage covers all users within that QOS; it is separate from the caller's per-user usage. A missing account entry can borrow QOS caps from another account, but never its usage.
- `MaxJobsPU` / `MaxSubmitJobsPU` are only rendered inside per-user entries under `User Limits`, and a user with no tracked jobs has no entry at all. Since a QOS applies one value to every user, `qos_user_limits` falls back to another user's entry for the limits — take only the limit, never the parenthesised usage, which is that other user's. If no entry supplies a limit, show `?`. With no entry and no running jobs in the QOS, the caller's usage is 0. Job usage comes from `squeue`, scoped to the user and each QOS across all accounts.
- Association `Max*` limits are inherited down to the caller's own association, so `scope_limits` reads `Max*` there and only `Grp*` totals from the account and its parents (`association_chain`, stopping at `root`).

### Remote clusters

Measured on Alliance login nodes: starting an ssh session costs ~0.15 s on Fir but ~2.5 s on Tamia (shell start-up), while each Slurm command inside a session is fast. Hence one session per refresh (`run_batch`), probes streamed over `PROBE_WORKERS` sessions (`run_stream`), and `ClusterInfo.detect` doing everything in one session with `login_path=True`: non-login ssh sessions on Fir lack Slurm on `PATH`, so detection reads the login shell's PATH (`bash -lc`) and later scripts export it. The unfiltered `assoc_mgr` cache on Fir is 66 MB (8 s); `Collector` filters it to the caller's accounts and QOS (`assoc_mgr_command`), fetching parent accounts' associations separately (`with_parent_associations`, inserted before `QOS Records`). Only the visible tab refreshes on the timer; hidden tabs are not contacted at all, so a remote control master can expire while its tab is hidden, and reopening it then logs in again.

When the user's `~/.ssh/config` already multiplexes (`ControlMaster`/`ControlPath`, read with `ssh -G`), `Remote` reuses that master, so a login made in another terminal counts. Otherwise it adds its own socket in `$TMPDIR/node_state-<uid>/` with `ControlPersist=10m`.

### Testing

The suite is pure `unittest` and needs no Slurm or ssh. Only `hosts` starts processes, so `test_hosts` patches `hosts.subprocess`, and everything else runs against `fakes.FakeHost`, which answers commands by prefix from canned text and probes through a `probe(args)` function. Note that `str.splitlines()` splits on `\x1e` (the record separator `hosts` uses), unlike a pipe; fake pipes use `io.StringIO`. Parsers are pure functions exercised with captured output. `test_tui` drives the app headlessly with Textual's `run_test` pilot (`IsolatedAsyncioTestCase`), with a local and a remote fake cluster.

End-to-end behaviour against a real cluster still needs a machine where `scontrol` works, and the probes additionally need `sbatch` or `srun`. Both use `--test-only` and never start a job, so running `node_state` is safe on a shared login node. To check the dashboard's layout from a script, run it in a private tmux server (`tmux -L <name> new-session -d -x 150 -y 44 ...` then `capture-pane -p`) so the user's own tmux clients cannot resize it.
