# AGENTS.md

## Project overview

A Python CLI (project name `cli`) with utilities for inspecting a Slurm cluster. Each utility is exposed as its own console script so it can be run directly (e.g. `uv run node_state`); new commands should follow the same pattern rather than being nested under a single umbrella executable.

Current commands:

- `node_state` — a [Textual](https://textual.textualize.io/) dashboard in the style of `htop`/`slurmtop`: partitions, the caller's account/QOS limits, a grid of estimated start times from `--test-only` probes, and the caller's jobs. One tab per cluster: the local one (if Slurm runs here), then each config entry with an `ssh` host, read over ssh. Only the local cluster is checked at startup; a remote one is first contacted when its tab is opened (`ClusterView.activate`), and only the visible tab refreshes. When output is not a terminal it prints the same panels once instead. It takes no options: the cluster is detected from `scontrol`, and the probe request is edited in the dashboard. `M` turns on an hourly email of the report (see **Hourly email** below), which stops when the dashboard exits. It works on any cluster: everything comes from Slurm, and the few site rules Slurm cannot report live in data profiles.

## Layout

- [pyproject.toml](pyproject.toml) — project metadata; console scripts live under `[project.scripts]`
- [src/node_state/cli.py](src/node_state/cli.py) — entry point (no options besides `--help`); lists the clusters (`find_clusters`), then runs the dashboard or, when output is not a terminal, the text report of the local cluster (remote ones, after `connect`, only when Slurm is not available here)
- [src/node_state/hosts.py](src/node_state/hosts.py) — where commands run: `Local` (subprocess) or `Remote` (ssh). `run_batch` runs a refresh's commands in parallel, in one ssh session for a remote; `run_stream` reports probe results as they land, over a few ssh sessions
- [src/node_state/slurm.py](src/node_state/slurm.py) — the `scontrol`/`squeue`/`sacctmgr`/`sbatch`/`srun` command lines (`*_COMMAND`, `*_command`, `test_command`) and the pure parsers for their output
- [config.example.toml](config.example.toml) — the shared, documented config: per-cluster profiles as data, with the email tables commented out; `src/node_state/config.example.toml` is a symlink to it so the build copies it into the package (keep the link: without it an installed copy has no profiles)
- `config.toml` — the user's own config, beside the template and read instead of it when present (`profiles.config_path`): `[mail]` (`profiles.load_mail`, whose address becomes `Settings.mail_user`), `[smtp]` (`profiles.load_smtp`, the hourly email's server and password) and `[clusters.*]`. Git-ignored and never packaged, since it holds the SMTP password: never commit it, print it or copy it into the package
- [src/node_state/profiles.py](src/node_state/profiles.py) — `Profile` (walltimes, GPU count mode, ssh host) and `Smtp`, and loading/validating the config file
- [src/node_state/snapshot.py](src/node_state/snapshot.py) — `ClusterInfo.detect(host)`; `Collector(info, host)` builds a `Snapshot`: partition and hardware usage, account/QOS `Scope`s and their `Limit`s (QOS and association), and the caller's jobs; `Cluster` pairs a tab name, profile and host
- [src/node_state/probes.py](src/node_state/probes.py) — the estimates `Grid`: request sizes × walltimes, run concurrently with `--test-only`
- [src/node_state/views.py](src/node_state/views.py) — plain-text cells shared by both front ends
- [src/node_state/tui.py](src/node_state/tui.py) — the Textual app (`NodeStateApp`: tabs, global keys), one `ClusterView` per tab (its state, panels, workers and keys) and the probe-request dialog
- [src/node_state/report.py](src/node_state/report.py) — the text report printed when output is not a terminal; `print_cluster` prints one cluster's and returns its snapshot
- [src/node_state/mailer.py](src/node_state/mailer.py) — the hourly email: builds it (`digest`, `job_changes`, `compose`), sends it (`send`) and keeps the thread and last jobs in `~/.local/state/node_state/mail.json` (`send_report`); `NodeStateApp` runs it on a thread worker from an hourly timer
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
- Nothing outside `hosts.py` starts a process; everything else receives a host and calls `run`/`run_batch`/`run_stream`. Commands never read the terminal (`stdin=DEVNULL`, `ssh -T`), or ssh would steal the dashboard's keystrokes; only `Remote.login` uses it, under `App.suspend()` when an opened remote tab cannot connect (automatically once per opening, then with `l`), or before a text report. `act` runs a command that acts (`sbatch`, `scancel`) and returns its status and combined output, so a rejection can be shown.
- Every duration a panel shows (time limits, wall caps, walltime columns, waits, job times) is Slurm's `D-HH:MM:SS`, via `slurm.format_duration` (minutes) or `slurm.normalize_time` (squeue strings). `∞` marks no limit.

### Supporting a new cluster

Usually nothing is needed: the cluster name comes from `scontrol show config`, and partitions, GPU types, time tiers, accounts and limits are all read from Slurm. Add a profile only for rules Slurm does not report, as a `[clusters.<ClusterName>]` table in [config.example.toml](config.example.toml), which documents each setting (and in your `config.toml`, which is read instead when it exists):

- `walltimes` — when partition `MaxTime`s do not mark routing tiers (e.g. `rcl` routes by GPU request, so one hour is enough).
- `gpu_counts` — `pow2` (default), `all`, or `whole_node` for sites that reject partial GPU nodes (`tamia`). `whole_node` also makes each GPU request take its whole node (`Request.whole_node`: `--exclusive --mem=0`), since holding every GPU with only the request's few CPUs and memory would waste the node.

Do not add per-cluster code paths; if a cluster needs logic rather than data, generalize it in `snapshot`/`probes` behind a profile setting.

Slurm reports the same facts differently per site; the generic code already handles these:

- **Rejection reasons.** Submit filters print banners or notes on every submission (RCL's storage-policy banner, Fir's and Tamia's note on G vs 1000M memory units), rejections included. `probes.clean_message` strips colour codes, `sbatch:`/`srun:` prefixes and `-----`/`=====` rules, and leaves out every line an accepted reply printed too (`Grid.banner`, learned by `learn_banner`; a rejection that landed first is cleaned again). The view and the report share one banner set across a cluster's grids. If no probe is ever accepted, banners stay in the reasons.

- **Reserved cores.** `slurm.parse_nodes` records `cpu_tot` (`CPUTot`) and `cpu_efctv` (`CPUEfctv`, falling back to `CPUTot`). Usage and hardware groups always use `cpu_efctv`, so `CoreSpecCount` cores never show as free.
- **GPU rollups.** `CfgTRES` may list an untyped `gres/gpu=N` alongside per-model entries summing to the same `N`. `parse_tres_gpus` returns both; `Collector` calls `normalize_gpu_rollups` on every node so GPUs are not counted twice.
- **Unavailable nodes.** `slurm.node_available` checks every state word (`IDLE+DRAIN`, `DOWN+NOT_RESPONDING`...); unallocated capacity on such nodes is "unavailable", not free.
- **Partition routing.** Probes never pass `--partition` unless the user adds it; Alliance clusters and `rcl` route jobs by duration or GPU request. Default walltimes are the distinct `MaxTime`s of usable partitions (`plan_walltimes`), which is where routing changes.
- **Probe sizes.** Each GPU type is probed up to the tightest GPU cap in the scope's limits (`max_gpu_counts`: per job, user, account or node, on the model or all GPUs), never above one node's capacity. Walltimes above the scope's wall cap are skipped, not probed.
- **Whole nodes.** `Settings.whole_node` swaps `-c`/`--mem` for `--exclusive --mem=0` (`resource_directives`) on every row and makes `build_grid` probe each GPU type at full node capacity, like the `whole_node` profile mode. The profile mode does the swap for GPU rows only (`Request.whole_node`; `Grid.gpus_by_node` notes it on the panel and in the report), leaving the CPU-only row on `-c`/`--mem`. `Settings.describe()` is the one-line request shown on the estimates panel and in the report.
- **Submitting.** `Result.run` is the submit command as arguments (`slurm.run_command`, the same one `format_run_command` shows), so Submit runs exactly what is displayed and copied. `ConfirmScreen` always asks first, with Cancel focused, before Submit (`sbatch`) or Release (`scancel`); rejected cells and `srun` commands (interactive, so they need a terminal of their own) are refused. Submitting, releasing, copying, switching clusters, account/QOS, sbatch/srun and Partitions/Nodes by hardware have no keys, only clicks (the tabs switch clusters): the `Submit` label in Details carries `@click` meta for `app.submit` (sbatch only), `Release` in a job's details for `app.release_job` with the job id, and every command's `Copy` link (`copy_link`, a `CommandLine` suffix) for `app.copy_command` with the command itself, the command in the Start estimates subtitle for `app.toggle_command`, the Partitions and Nodes by hardware titles for `app.toggle_nodes`, and the estimates' title (the probed account/QOS, clickable only when there is another) for `app.next_scope`; the app actions forward to the visible tab, since a click's default namespace is the widget that drew the text.
- **Dialog keys.** `NodeStateApp.check_action` only blocks `submit` and `release_job` while a dialog is open; blocking every app action would also block Textual's own `focus_next`, so Tab would stop moving between fields.
- **Email.** `Settings.mail_directives()` adds `--mail-type`/`--mail-user` to every run command (`Grid.run_directives`) but not to probes (`Grid.probe_directives`), which carry only what the estimate depends on: the request (`request_directives`) and extra options. The request dialog edits the address per view; it rebuilds `Settings` with `dataclasses.replace`, so fields it does not show survive.
- **Hourly email.** `M` reads `[smtp]` when pressed, sends at once and then from an hourly `set_interval`; the timer dies with the app, so nothing runs after exit (`crontab` is not allowed for users on Killarney's login nodes anyway). Each email covers the local cluster and remote tabs that have data (opened at least once), each with its view's `Settings`; never-opened tabs are not contacted. `report` prints to an `out` stream so the email collects the text without redirecting `sys.stdout`, which Textual owns while it runs. One conversation needs both the same subject and `In-Reply-To`/`References` pointing at a message the client has, so the first email's `Message-ID` is stored per recipient and every later email refers to it (Message-IDs themselves must stay unique: Gmail drops duplicates). The state file is written only after a successful send, so a failed email's changes appear in the next one. The body is also sent as HTML `<pre>`, since Gmail's web client shows plain text in a proportional font and the tables would not line up. `send` refuses to log in without STARTTLS or SSL. `Smtp.password` is kept out of `repr`.
- **Run commands.** Each `Result` keeps the probe as it ran (`probe`, from `slurm.format_test_command`) and its raw `output`, both shown in the estimate's details, apart from the command to submit (`command`, from `format_run_command`, which the `Copy` label copies). Both use `slurm.SBATCH_WRAP` (`--wrap="sleep infinity"`) instead of a batch script, so the submit command is the probed request without `--test-only`, plus email; `srun` probes name the login shell (`Grid.shell`, the remote user's shell on a remote cluster) and `srun` commands run it with `--pty <shell>`. Naming a program matters: Tamia's submit filter only routes an `srun` that names one ("No partition specified" otherwise), and on Killarney the estimates are the same either way. Keep them in step when changing either.
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

The suite is pure `unittest` and needs no Slurm or ssh. Only `hosts` starts processes, so `test_hosts` patches `hosts.subprocess`, and everything else runs against `fakes.FakeHost`, which answers commands by prefix from canned text and probes through a `probe(args)` function. Note that `str.splitlines()` splits on `\x1e` (the record separator `hosts` uses), unlike a pipe; fake pipes use `io.StringIO`. Parsers are pure functions exercised with captured output. `test_tui` drives the app headlessly with Textual's `run_test` pilot (`IsolatedAsyncioTestCase`), with a local and a remote fake cluster. `test_mailer` patches `mailer.smtplib` (or `mailer.send`) and `report.print_cluster`, so it sends no mail and runs no probes, and writes state to a temporary file; `test_tui`'s mail tests patch `profiles.load_smtp` and `mailer.send_report`. `test_profiles` points `CONFIG_PATH` at a missing file, so it checks the template, never your `config.toml`.

End-to-end behaviour against a real cluster still needs a machine where `scontrol` works, and the probes additionally need `sbatch` or `srun`. Both use `--test-only` and never start a job, so running `node_state` is safe on a shared login node; only a confirmed Submit or Release (`ConfirmScreen`) starts or cancels one. Never confirm either from a test script on the real cluster. To check the dashboard's layout from a script, run it in a private tmux server (`tmux -L <name> new-session -d -x 150 -y 44 ...` then `capture-pane -p`) so the user's own tmux clients cannot resize it.
