# Alpha Objective Knob — Experiment Runbook

**What this document is.** A step-by-step, copy-pasteable command list for
validating the runtime-tunable read/write objective weight (`objective_alpha`)
on the Chameleon node, plus the reasoning behind each step. It sits between
two other documents and should not duplicate them:

- **`docs/RUNTIME_ALPHA_OBJECTIVE_PLAN.md`** has the design, the theory of why
  each gate exists, and the file-by-file implementation record. Read that
  first if you want to understand *why* the code works the way it does.
- **`docs/PREREGISTRATION.md` D-3** has the dated, falsifiable predictions —
  what we expect to see, recorded *before* running anything, so results can't
  be quietly reinterpreted after the fact. This runbook is how those
  predictions get tested.

This document is the **"what do I actually type"** layer. Update the status
table in §0 as steps complete — that's the fastest way to see where things
stand without re-reading the whole file.

**Fixed 2026-09-22, not yet on the node as of this edit:** a real bug was
found running Gate α-1 — `objective_alpha` had been (wrongly) added to
`experiment_fingerprint`, so any non-default alpha value failed the
manifest-match check against every manifest, unconditionally (`alpha=1.0`
worked by accident, since its fingerprint segment was empty). Fixed by
removing alpha from the fingerprint entirely — it's a reward-only knob, not
database/workload geometry, and doesn't belong there. Full writeup in
`RUNTIME_ALPHA_OBJECTIVE_PLAN.md` §0a. **The commands in this runbook are
unchanged** (the fix is internal to `03_run_experiments.sh`/
`06_select_baseline_slo.py`) — but the node needs the fixed files before
Gate α-1 will pass.

**One reminder before any of this:** everything under `objective_alpha` is
**exploratory tooling, not a Programme 1 acceptance instrument** (PATHWAYS'
gates A–E). Nothing measured here can be cited as evidence for or against
this project's actual paper claims unless a separate, later decision
explicitly promotes it.

---

## 0. Status checklist

| Step | What it proves | Status |
| --- | --- | --- |
| Build (RocksDB + db_bench on the node) | The code compiles on this hardware | done |
| §3: 10M/T=2 baseline manifest | Prerequisite artifact for every gate below | **done** — `baseline_slo/assoc-v1/10M/T2/baseline_slo.json` |
| §4: Gate α-0 (regression) | `alpha=1.0` changes nothing | not started |
| §5: Gate α-1 (flip sanity) | `alpha=0.0` removes read-side credit | not started |
| §6: Gates α-2/α-3 (live reload) | The control file works, mid-run, without corruption | not started |
| §7: Gate α-4 (local smoothness) | The alpha-conditioned network behaves sanely near a trained point | not started |
| §8: Frontier sweep | Does the alpha dial trace a usable read/write tradeoff | **blocked** — needs the `Assoc` Gate 1 hull, which has not been re-measured yet |
| §9: Runtime-switch dynamics | Realistic infrequent-flip usage pattern | deferred, low priority per your stated usage |

---

## 1. What we're actually testing, in plain terms

RocksDB decides *when* to merge (compact) its files. This project's
controller learns that timing decision. Previously, the reward pushed the
controller in exactly one direction: make reads cheaper, and only worry
about writes if they get badly worse than a plain, untouched RocksDB.

`objective_alpha` is a dial from 0 to 1 that changes which of those two
things the controller is told to care about:

- **`alpha = 1`** (the default): behave exactly as before — optimize for
  cheap reads.
- **`alpha = 0`**: optimize for cheap writes instead — the controller becomes
  maximally reluctant to compact, since compacting only helps reads and,
  under `alpha=0`, reads no longer earn it any credit.
- **In between**: a blend of the two.

The dial can also be changed while the system is running (not just set once
at startup), and the network was built to take the current value of the dial
as one of its inputs, so it can (locally, near values it's actually seen)
adjust its behavior when the dial moves instead of getting confused by a
reward that changed without warning.

**Why we're testing it in isolated, small pieces first.** This project has a
documented history (see `PROJECT_HISTORY_AND_SYSTEM_DESCRIPTION.md`) of
losing months to bugs that looked like "the learner behaves badly" but were
actually plumbing errors — a broken JSON parser, a mislabeled statistic, a
silently-skipped rebuild. The gates below (§4–§7) are cheap, fast,
mechanical checks designed to catch exactly that class of bug *before* any
result gets treated as meaningful. Only after they pass does §8's real
research question — does this dial trace out a useful tradeoff — become
worth spending real node-time on.

---

## 2. Build (already done on this node)

For reference / re-checking after a future code change:

```bash
git submodule update --init --recursive
scripts/dbbench_pipeline/00_install_dependencies.sh
scripts/dbbench_pipeline/01_build_rocksdb.sh
scripts/dbbench_pipeline/02_build_db_bench.sh
grep avx512_instruction_count build-dbbench/build_provenance.env  # should be nonzero
```

---

## 3. Prerequisite: a calibrated manifest at 10M/T=2 — DONE

**Why this step exists.** Any arm that runs the RL controller (`rl`,
`prior_only`, `unconstrained_rl`, `unconstrained_prior_only`) needs a
"manifest" file — a small JSON file recording what a plain, untuned RocksDB
achieves on this exact workload/size/ratio, so the controller has a
reference point for its write/space/latency limits. The pipeline refuses to
start a learned arm without one. This is a one-time cost per (size, size
ratio) cell — every gate below reuses the same file.

**This originally targeted 1M/T=2** to keep the gates fast, but
`05_run_baseline_sweep.sh` ignores the caller's `WORKLOAD_SIZES_M` entirely —
it has its own separate variable, `BASELINE_WORKLOAD_SIZES_M`, defaulting to
`10`. The sweep below therefore ran at 10M regardless of what was asked for,
and rather than redo it, the manifest and every gate that follows now target
**10M/T=2** instead. Each 10M run takes about 3.7 minutes (measured:
`elapsed_seconds=221.7` in the sweep's own `metadata.env`) — not as fast as
1M would have been, but still small, and it makes the mid-run flip gates in
§6 easier to time, not harder.

```bash
# 1. Run one baseline config (today's project defaults) three times at 10M/T=2.
# NOTE: BASELINE_WORKLOAD_SIZES_M controls the sweep's own size, NOT
# WORKLOAD_SIZES_M -- setting the latter here does nothing.
SIZE_RATIOS=2 \
BASELINE_WORKLOAD_SIZES_M=10 \
BASELINE_L0_COMPACTION_TRIGGERS=4 BASELINE_L0_SLOWDOWN_TRIGGERS=20 \
BASELINE_L0_STOP_TRIGGERS=36 BASELINE_COMPACTION_PRIORITIES=3 \
BASELINE_LEVEL_BASE_SCALES=1 BASELINE_REPEATS=3 \
BASELINE_RESULTS_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/baseline-sweep \
BASELINE_DB_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/baseline-sweep \
CONFIRM_BASELINE_SWEEP=YES \
  scripts/dbbench_pipeline/05_run_baseline_sweep.sh

# 2. Turn that into a provisional manifest (selection is trivial: one candidate).
# --workload-profile is required here: this script's own default is the old
# "balanced-v1" workload, separate from config.sh's "assoc-v1" default, and
# omitting it silently filters out every result ("no matching regular
# baseline arms") instead of erroring on the mismatch.
scripts/dbbench_pipeline/06_select_baseline_slo.py \
  --baseline-results /home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/baseline-sweep \
  --workload-profile assoc-v1 \
  --size-millions 10 --size-ratio 2 \
  --output baseline_selection/assoc-v1/10M/T2/baseline_slo.json

# 3. Calibrate the safety guard against it; this writes the FINAL manifest.
WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 \
GUARD_RESULTS_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/guard \
GUARD_DB_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/guard \
CONFIRM_GUARD_PROTOCOL=YES \
  scripts/dbbench_pipeline/06_run_guard_protocol.sh

# Confirm it exists:
ls -la baseline_slo/assoc-v1/10M/T2/baseline_slo.json
```

**Done — confirmed on the node.** If a future cell (a different size or
ratio) needs its own manifest, repeat this section with that size/ratio
substituted throughout, and remember the `BASELINE_WORKLOAD_SIZES_M` gotcha.

**If step 3's last stage ("guard holdout") reports a failure**, that's
expected and not a blocker — this project's own history (`PREREGISTRATION.md`
E-1) already found the guard's holdout check fails at small sample counts on
its strict threshold. The manifest file itself is written earlier in that
script, before the holdout check runs. As long as `ls` above finds the file,
move on.

---

## 4. Gate α-0 — regression check

**Why.** `alpha=1.0` is the default and is supposed to be *mechanically
identical* to the reward before this knob existed (the write-rate term's
coefficient is exactly `(1 - alpha) = 0`). If this gate fails, the blend
formula has a bug, full stop — nothing past this point can be trusted.

Every gate below gets its **own top-level `RESULTS_ROOT`/`DB_ROOT`**, not a
shared one. The arm-name suffix (`rl` vs. `rl-alpha0.0`, §5) already stops
different alpha values from colliding *within* one root, but a dedicated
root per gate is a stronger, easier-to-audit guarantee, and it's what you
asked for — nothing here relies on remembering the suffix rule to stay safe.

```bash
RESULTS_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/alpha-gate-0
DB_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/alpha-gate-0

WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 EXPERIMENT_ARMS=rl REPEATS=3 \
OBJECTIVE_ALPHA=1.0 \
DB_ROOT="$DB_ROOT" RESULTS_ROOT="$RESULTS_ROOT" \
CONFIRM_EXPERIMENTS=YES \
  scripts/dbbench_pipeline/03_run_experiments.sh
```

**What to check** (repeat 1, adjust `repeat-01` for the others):

```bash
jq -c '.reward_components.objective_write_term' \
  "$RESULTS_ROOT/10M/T2/repeat-01/rl/io.jsonl" | sort -u
```

**Pass:** every value printed is `0`, `0.0`, or `null`. `null` is not a
failure here — it's `jq`'s printout for a missing key, and the per-level
terminal ("shutdown") log line legitimately has no `objective_write_term` at
all: `_global_reward()` short-circuits on `g["done"]` and returns just
`{"terminal": 1.0}`, skipping the read/write blend entirely. Confirm that's
really all the `null`s are, rather than assuming it:

```bash
jq -c 'select(.reward_components.objective_write_term == null) | .reward_components' \
  "$RESULTS_ROOT/10M/T2/repeat-01/rl/io.jsonl"
```

Every line this prints should be exactly `{"terminal":1.0}`. If anything
else shows up there, that's a real regression, not the expected terminal
case.

---

## 5. Gate α-1 — flip sanity

**Why.** At `alpha=0.0`, the analytic prior's read-relief credit is supposed
to disappear entirely (see `RUNTIME_ALPHA_OBJECTIVE_PLAN.md` §3) — the
controller should have no reason left to compact except to avoid an actual
write stall. This is the plainest possible check that the "prioritize
writes" direction actually does what it says.

Own root again, separate from α-0's:

```bash
RESULTS_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/alpha-gate-1
DB_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/alpha-gate-1

WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 EXPERIMENT_ARMS=rl REPEATS=3 \
OBJECTIVE_ALPHA=0.0 \
DB_ROOT="$DB_ROOT" RESULTS_ROOT="$RESULTS_ROOT" \
CONFIRM_EXPERIMENTS=YES \
  scripts/dbbench_pipeline/03_run_experiments.sh
```

(This writes to `$RESULTS_ROOT/10M/T2/repeat-0N/rl-alpha0.0/` — the
arm-name suffix from non-default alpha still applies here too, it's just no
longer load-bearing for collision-avoidance since the root itself is unique.)

**What to check:**

```bash
jq -c '.reward_components.prior_w_read_effective' \
  "$RESULTS_ROOT/10M/T2/repeat-01/rl-alpha0.0/io.jsonl" | sort -u
jq -c '.reward_components.objective_read_term' \
  "$RESULTS_ROOT/10M/T2/repeat-01/rl-alpha0.0/io.jsonl" | sort -u
```

**Pass:** both print only `0`/`0.0` on every line. As a softer, qualitative
check, the compaction action rate (`jq '.output.action' io.jsonl | sort |
uniq -c`) should be lower than α-0's — the controller should be compacting
less often when it gets no read credit.

---

## 6. Gates α-2 and α-3 — live control-file reload, mid-run

**Why.** Everything above sets `alpha` once at process start. The whole
point of this knob is that it can change *while the system is running*,
read from a file another process writes. These two gates check that
mechanism directly: does a mid-run edit take effect quickly (α-2), and does
it do so cleanly, with no transition showing a mismatched or corrupted state
(α-3)?

One run does both checks — start it in the background, wait until it's
actually producing decisions, then flip the file:

```bash
RESULTS_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/alpha-live
DB_ROOT=/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/alpha-live
mkdir -p "$RESULTS_ROOT" "$DB_ROOT"
RESULT_DIR="$RESULTS_ROOT/10M/T2/rl-alpha1.0live"

WORKLOAD_SIZES_M=10 SIZE_RATIOS=2 EXPERIMENT_ARMS=rl REPEATS=1 \
OBJECTIVE_ALPHA=1.0 OBJECTIVE_ALPHA_LIVE=1 \
DB_ROOT="$DB_ROOT" RESULTS_ROOT="$RESULTS_ROOT" \
CONFIRM_EXPERIMENTS=YES \
  scripts/dbbench_pipeline/03_run_experiments.sh &
PIPELINE_PID=$!

# Wait for real decisions to start landing, then flip mid-run. At 10M
# (~3.7 minutes per run, per §3's measurement) this window is comfortable --
# no need to rush the flip immediately after the first log line appears.
until [ -s "$RESULT_DIR/io.jsonl" ]; do sleep 0.2; done
sleep 10
scripts/dbbench_pipeline/set_objective_alpha.sh 0.0 \
  "$RESULT_DIR/objective_alpha.json"

wait "$PIPELINE_PID"
```

Polling for the log file rather than a fixed `sleep N` before starting is
deliberate — a fixed guess is either too short (misses the run entirely on a
fast machine) or wastes time on a slower one.

**What to check for α-2 (reload latency):**

```bash
jq -c '.constrained_reward.objective_alpha_reload_events' \
  "$RESULT_DIR/server_summary.json"
jq -c 'select(.reward_components.objective_alpha == 0.0) | .ts' \
  "$RESULT_DIR/io.jsonl" | head -1
```

**Pass:** the timestamp of the first `alpha=0.0` decision is within roughly
one decision interval (50 ms, plus normal scheduling jitter) of the reload
event's own timestamp — not seconds later.

**What to check for α-3 (clean transition):**

```bash
jq -c '[.ts, .step, .reward_components.objective_alpha] | @tsv' \
  "$RESULT_DIR/io.jsonl"
```

**Pass:** the `objective_alpha` column is `1.0` for a prefix of lines, then
`0.0` for the rest, with no line in between showing an inconsistent or
missing value. (Per the design note in `RUNTIME_ALPHA_OBJECTIVE_PLAN.md` §4,
this should hold *by construction* — the gate exists to catch a future
regression, not because a failure is expected here.)

---

## 7. Gate α-4 — local smoothness (post-hoc, no new run)

**Why.** The network takes `alpha` as one of its inputs specifically so it
can adjust smoothly if the dial moves, rather than treating a reward change
as unexplained noise. This gate checks that "smoothly" locally, near a value
it actually trained near — **not** across the whole 0–1 range from a single
cold start, which this project's own no-pretraining rule makes unrealistic
to expect (see the plan doc §2a for why that scope limit is deliberate).

**Procedure** (analysis only, using the checkpoint and states already
produced by §6's run): load `$RESULT_DIR/model.pt`, take a handful of real
states logged in `io.jsonl` near the end of the `alpha=0.0` segment, and
query the network's Q-values at that state with the alpha input perturbed to
nearby values (e.g. 0.0, 0.05, 0.1, 0.15) instead of the true one.

**Pass:** the implied action ranking (compact vs. defer) and the Q-value gap
between them change smoothly across those nearby values — no sign flip or
discontinuity between adjacent points.

This is deferred as a small follow-up analysis script once §6's data exists,
rather than written speculatively now.

---

## 8. Frontier sweep — the actual research question

**Currently blocked.** This experiment's comparator is the Pareto hull from
Gate 1 (`docs/PATHWAYS.md` Pathway C) — the set of "what could a human just
get by tuning plain RocksDB's own knobs" points. That hull has not been
re-measured on the `Assoc` workload yet; only the oracle-parity gate that
*authorizes* running it has passed so far. Comparing this sweep against the
old *uniform*-workload hull would repeat exactly the mismatched-workload
mistake `PREREGISTRATION.md` D-1 was recorded to prevent. Gate 1's `Assoc`
hull sweep is a large, separate task (~48 node-hours per `PATHWAYS.md`) and
is not part of this runbook.

**What can run now, without the hull:** the raw sweep itself, so the W/R
measurements exist and are ready to plot the moment the hull does.

```bash
for a in 0 0.25 0.5 0.75 1.0; do
  RESULTS_ROOT="/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/alpha-sweep-a${a}"
  DB_ROOT="/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/db/alpha-sweep-a${a}"
  WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 6 10" EXPERIMENT_ARMS=rl REPEATS=10 \
  OBJECTIVE_ALPHA="$a" \
  DB_ROOT="$DB_ROOT" RESULTS_ROOT="$RESULTS_ROOT" \
  CONFIRM_EXPERIMENTS=YES \
    scripts/dbbench_pipeline/03_run_experiments.sh
done
```

**This also needs its own manifests, at 10M/T={2,6,10}, not the 1M/T=2 one
from §3** — repeat §3's three-step chain with `WORKLOAD_SIZES_M=10
SIZE_RATIOS="2 6 10"` first (this is the real, non-scoped-down version —
budget real time for it, it is not a quick gate).

**Why paired repeats of 10, not 3.** `PREREGISTRATION.md`'s Gate 1 record
found that at five repeats, point-read amplification's confidence interval
can be wider than the 2% margin the acceptance criteria use — ten repeats is
what the project's own measurement showed is actually needed to resolve
differences this small, not a default chosen by convention.

**Once the `Assoc` Gate 1 hull exists**, one `frontier_analysis.py` call per
alpha value plots that alpha's `(W, R)` points against it:

```bash
python3 scripts/dbbench_pipeline/frontier_analysis.py \
  <path to the Assoc Hull-0 sweep results> \
  --size-millions 10 --size-ratio 2 6 10 \
  --policy-results "/home/cc/lsm-workload/LSM-WorkloadAwareCompaction/results/alpha-sweep-a0.25" \
  --policy-arm rl \
  --output alpha-sweep-a0.25-vs-hull.json
```

**What we predict** (recorded in advance, `PREREGISTRATION.md` D-3, so this
is not being written after seeing a result): write amplification should
fall, or at least not rise, as `alpha` drops from 1 to 0, and point-read
amplification should do the opposite. We do **not** predict that any single
alpha value escapes domination by the hull on its own — the dial re-weights
an existing lever, it doesn't add a new one the way Pathway A's capacity
action would.

---

## 9. Runtime-switch dynamics (deferred)

You said you don't expect the dial to change often in real use, so this
experiment — flipping `alpha` mid-run on a realistic schedule (fixed for
~80% of the run, one or two flips near the end) rather than the synthetic
immediate-flip of §6 — is lower priority. Command shape is the same pattern
as §6, just at the 10M scale and with the flip timed later in the run. Not
scripted here until §8's static sweep is confirmed sane.

---

## 10. Where results get recorded

- Update the status table in §0 as each step completes.
- Any deviation from a prediction in `PREREGISTRATION.md` D-3 gets reported
  as a deviation, not quietly reinterpreted — that document's own opening
  rule (§0) is that an entry edited after its outcome is seen is worthless.
- If §8's results end up wanting to be cited anywhere near the actual paper
  claims (Pathway A–E), that requires a new, separate preregistration entry
  and is out of scope for what's already written down.
