# AGENTS.md

## Project overview

A Python CLI (project name `cli`) with utilities for inspecting a Slurm cluster. Each utility is exposed as its own console script so it can be run directly (e.g. `uv run node_state`); new commands should follow the same pattern rather than being nested under a single umbrella executable.

Current commands:

- `node_state` — prints total/allocated/available resource tables for each hardware type. With no argument it uses the generic fallback; an optional registered cluster name selects a site-specific reporter that may add job-feasibility and account-limit reports.

## Layout

- [pyproject.toml](pyproject.toml) — project metadata; console scripts live under `[project.scripts]`
- [src/node_state/cli.py](src/node_state/cli.py) — argument parsing, `CLUSTER_RUNNERS`, dispatch, and entry point for `node_state`
- [src/node_state/clusters/common.py](src/node_state/clusters/common.py) — `scontrol`/`sbatch` invocation, output parsing, and table rendering shared by all clusters
- [src/node_state/clusters/fallback.py](src/node_state/clusters/fallback.py) — generic resource summary for unregistered clusters
- [src/node_state/clusters/killarney.py](src/node_state/clusters/killarney.py) — reporter for `killarney`
- [src/node_state/clusters/vulcan.py](src/node_state/clusters/vulcan.py) — reporter for `vulcan`
- [src/node_state/clusters/rcl.py](src/node_state/clusters/rcl.py) — reporter for `rcl`
- [tests/](tests/) — `unittest` suite; one module per source module

## Commands

```bash
uv sync                      # install/refresh the environment
uv run node_state            # generic summary (needs scontrol)
uv run node_state killarney  # Killarney-specific summary and probes
uv run python -m unittest discover -s tests -t tests   # run the tests
```

Note the `-t tests` on the test command: `tests/` has no `__init__.py`, so discovery fails without it.

## Conventions and gotchas

- Python 3.12 only (`requires-python = "==3.12.*"`), managed with `uv`; build backend is `uv_build`.
- Command names use underscores (`node_state`), not hyphens.

### Adding a cluster

The no-argument command uses [fallback.py](src/node_state/clusters/fallback.py). Add `src/node_state/clusters/<name>.py` exposing `main(args)` only when a cluster needs site-specific normalization, probes, or account reporting, then register it in `CLUSTER_RUNNERS` in [cli.py](src/node_state/cli.py). Registry keys become the accepted subcommand names, and every runner receives the parsed `argparse.Namespace`. Build on [common.py](src/node_state/clusters/common.py) (`fetch_nodes`, `group_by_hardware`, `print_summary`, `run_sbatch_test`, `run_srun_test`, `fetch_partitions`/`parse_sinfo_table`, plus the assoc/QOS helpers below) rather than re-parsing `scontrol` output; cluster modules should hold only what is genuinely site-specific.

Slurm reports the same facts differently per site, so check these before trusting the defaults:

- **Reserved cores.** `common.parse_nodes` records both `cpu_tot` (`CPUTot`) and `cpu_efctv` (`CPUEfctv`, falling back to `CPUTot`). Where `CoreSpecCount` reserves cores, pass `cpu_key="cpu_efctv"` to `node_hw_key`/`group_by_hardware`/`print_summary` or available CPUs will be overstated — `rcl` does this via its `CPU_KEY` constant.
- **GPU rollups.** `CfgTRES` may list an untyped `gres/gpu=N` alongside per-model entries summing to the same `N`. `parse_tres_gpus` returns both; a cluster that advertises both must call `normalize_gpu_rollups` or every GPU is counted twice (as `killarney` and `rcl` do).
- **Partition routing.** Do not hardcode `--partition` in feasibility probes without checking; `killarney` and `rcl` route jobs by resource request.
- **Account/QOS caps.** Node tables show the hardware, not what an account may request. `rcl` reports these via `common.fetch_assoc_mgr` + `parse_default_account` / `parse_qos_records` / `qos_names_for_account` / `qos_user_limits`.

### Reading account limits

`sacctmgr` and `sacct` fail from a login shell when `slurmdbd` listens on the controller rather than locally ("Connection refused" to `localhost:6819`). That is **not** evidence that accounting is disabled — check `AccountingStorageEnforce` in `slurm.conf`, which is world-readable. Use `scontrol show assoc_mgr flags=assoc,qos` instead: it reads slurmctld's in-memory cache and works unprivileged.

Two quirks of that output shape the parser in [common.py](src/node_state/clusters/common.py):

- Association records carry **no** `QOS=` field, and `users=` does **not** filter the QOS section. The user's default account is therefore matched against each QOS's `Account Limits` block (`qos_names_for_account`), and `rcl` prints every matching QOS. These cache matches do not identify the default QOS or establish which QOSs the user may use.
- `MaxJobsPU` / `MaxSubmitJobsPU` are only rendered inside per-user entries under `User Limits`, and a user with no tracked jobs has no entry at all. Since a QOS applies one value to every user, `qos_user_limits` falls back to another user's entry for the limits — take only the limit, never the parenthesised usage, which is that other user's. If no entry supplies a limit, show `?`. Live usage for the caller comes from `squeue` via `fetch_user_job_counts`, scoped to the user and each QOS across all accounts.

### Testing

The suite is pure `unittest` with no third-party dependencies, so it runs anywhere `uv sync` succeeds — no Slurm needed. Everything that shells out (`scontrol`, `sbatch`, `srun`) lives in `common`, so tests patch `common.subprocess` rather than a cluster module's. `parse_nodes` / `parse_tres_gpus` are pure functions that can be exercised with captured `scontrol show node` text.

End-to-end behaviour against a real cluster still needs a machine where `scontrol` works, and the feasibility probes additionally need `sbatch` or `srun`. Both use `--test-only` and never start a job, so running `node_state` is safe on a shared login node.
