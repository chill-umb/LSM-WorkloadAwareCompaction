#!/usr/bin/env bash
# D-5: measure the L0 proactive band at a FIXED trigger (4) across ratios.
#
# The comparator stage 06 selected is trigger 2 / 4 / 2 at T = 2 / 6 / 10, so
# the band -- which needs trigger >= 3 to exist at all -- was measured only at
# T=6, perfectly confounded with that ratio. This runs the missing cells.
#
# It changes no comparator and no criterion. Results are a named control and
# are never pooled with the programme's arms.
#
# The regular twins already exist in the Hull-0 sweep (9 arms at T=2, 3 at
# T=10), so no baseline is run here.
set -Eeuo pipefail
cd "$(dirname "$0")"
PIPE=scripts/dbbench_pipeline
SWEEP=results/baseline_sweep
SELECTION_ROOT=baseline_selection_band          # provisional, from stage 06
MANIFEST_ROOT=baseline_slo_band                 # final, after guard calibration
RESULTS=results/band-control
mkdir -p gate1

echo "=============================================================="
echo "PHASE 1/4  trigger-4 SELECTION manifests for T=2 and T=10"
echo "=============================================================="
# Stage 06's selection rule is applied UNCHANGED to a one-candidate set: the
# input directory holds only the trigger-4 arms, so min-space -> fastest ->
# lower-W trivially returns trigger 4. The rule is not edited, and the guard
# limits are calibrated from exactly the arms the control runs on.
for T in 2 10; do
  src="$SWEEP/T$T-l0-4-20-36-pri3-base16777216"
  [[ -d "$src" ]] || { echo "missing $src" >&2; exit 1; }
  n=$(find "$src" -name COMPLETED | wc -l)
  echo "[T=$T] $n regular arms at trigger 4 -> manifest"
  mkdir -p "$SELECTION_ROOT/assoc-v1/10M/T$T"
  "$PIPE/06_select_baseline_slo.py" \
    --baseline-results "$src" \
    --workload-profile assoc-v1 \
    --size-millions 10 \
    --size-ratio "$T" \
    --minimum-repeats 3 \
    --level-base-bytes 16777216 \
    --output "$SELECTION_ROOT/assoc-v1/10M/T$T/baseline_slo.json"
  got=$(python3 -c "
import json,sys
fp=json.load(open('$SELECTION_ROOT/assoc-v1/10M/T$T/baseline_slo.json'))['experiment_fingerprint']
print(fp.split(':l0-')[1].split(':')[0])")
  [[ "$got" == "4-20-36" ]] || {
    echo "manifest pinned l0=$got, expected 4-20-36; refusing to run" >&2
    exit 1; }
  echo "[T=$T] manifest pins l0=$got"
done

echo
echo "=============================================================="
echo "PHASE 2/4  guard calibration at trigger 4: 6 oracle arms"
echo "=============================================================="
# Same chain the programme's own cells went through: oracle calibration arms
# run against the SELECTION manifest, then 06_calibrate_live_guard turns it
# into the calibrated manifest the policy arms require. The independent
# holdout is deliberately NOT run -- it scores E-1/E-5, which D-5 does not
# test, and the oracle arm cannot measure E-5 in any case (history 14.17).
CAL_RESULTS=results/band-control-guard/assoc-v1/calibration
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 10" \
EXPERIMENT_ARMS=oracle REPEATS=3 \
RL_RUN_PHASE=calibration DBBENCH_SEED=1001 \
BASELINE_SLO_DIR="$SELECTION_ROOT" \
RESULTS_ROOT="$CAL_RESULTS" \
DB_ROOT=.dbbench_pipeline_dbs/band-control-guard/calibration \
RESUME="${RESUME:-1}" CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

for T in 2 10; do
  mkdir -p "$MANIFEST_ROOT/assoc-v1/10M/T$T"
  "$PIPE/06_calibrate_live_guard.py" \
    --selection-manifest "$SELECTION_ROOT/assoc-v1/10M/T$T/baseline_slo.json" \
    --calibration-results "$CAL_RESULTS" \
    --repeats 3 \
    --output "$MANIFEST_ROOT/assoc-v1/10M/T$T/baseline_slo.json"
  python3 -c "
import json,sys
m=json.load(open('$MANIFEST_ROOT/assoc-v1/10M/T$T/baseline_slo.json'))
assert m.get('guard_calibrated') is True, 'guard not calibrated at T=$T'
assert m['experiment_fingerprint'].split(':l0-')[1].split(':')[0]=='4-20-36'
print('[T=$T] calibrated manifest pins l0=4-20-36')"
done

echo
echo "=============================================================="
echo "PHASE 3/4  policy arms: trigger 4 at T=2 and T=10"
echo "           10M x 2 ratios x 2 arms x 3 repeats = 12 arms"
echo "=============================================================="
WORKLOAD_SIZES_M=10 SIZE_RATIOS="2 10" \
EXPERIMENT_ARMS="prior_only unconstrained_prior_only" \
REPEATS=3 \
BASELINE_SLO_DIR="$MANIFEST_ROOT" \
RESULTS_ROOT="$RESULTS" \
DB_ROOT=.dbbench_pipeline_dbs/band-control \
RESUME="${RESUME:-1}" CONFIRM_EXPERIMENTS=YES \
"$PIPE/03_run_experiments.sh"

echo
echo "=============================================================="
echo "PHASE 4/4  D-5 predictions 1-5"
echo "=============================================================="
python3 - <<'PY' | tee gate1/d5_verdict.txt
import glob, importlib.util, json, statistics, sys
from collections import defaultdict
from pathlib import Path
P = Path("scripts/dbbench_pipeline"); sys.path.insert(0, str(P))
import frontier_analysis as fa
spec = importlib.util.spec_from_file_location("g", P / "04_generate_graphs.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

TWIN = "results/baseline_sweep/T{T}-l0-4-20-36-pri3-base16777216"
POL = "results/band-control"
# T=6 is the already-measured comparator cell; it enters as the reference.
T6 = {"dW": 2.07, "dR": -5.69, "l0": 1.32, "dphi": -0.062}
HEADROOM = {2: 16.6, 6: 14.0, 10: 24.4}   # (R_trig4 - R_trig2) / R_trig4, %

def truthy(v): return v in (True, 1, "true", "1")

def arms(root, T, arm):
    out = {}
    for m in sorted(Path(root).glob("**/COMPLETED")):
        r = g.collect_arm(m.parent)
        if r and r["size_millions"] == 10 and r["size_ratio"] == T and r["arm"] == arm:
            out[int(r["dbbench_seed"])] = r
    return out

def measure(paths):
    """L0 jobs, L1 phi p50, and deep releases below due."""
    jobs = defaultdict(int); phi1 = []; below = 0; deep = 0
    for p in paths:
        d = json.load(open(p))
        lv = (d.get("views", {}).get("workload", {}) or {}).get("levels", {}) or {}
        for k, v in lv.items():
            if v and v.get("jobs"): jobs[int(k)] += v["jobs"]
        for r in d.get("releases", []):
            if truthy(r.get("rl_suspended")) or truthy(r.get("rl_drain")) \
               or truthy(r.get("trivial_move")): continue
            l, ph = r.get("source_level"), r.get("phi")
            if l is None or ph is None or l == 0: continue
            if isinstance(ph, list): ph = ph[l] if l < len(ph) else None
            if ph is None: continue
            deep += 1
            if float(ph) < 1.0: below += 1
            if l == 1: phi1.append(float(ph))
    phi1.sort()
    return jobs, (phi1[len(phi1) // 2] if phi1 else float("nan")), below, deep

rows = {}
print()
print("D-5 CONTROL: L0 trigger 4 at T=2 and T=10 (T=6 is the reference cell)")
print("=" * 88)
print(f"{'cell':<6} {'L0 jobs pol/twin':>17} {'dW %':>20} {'dR %':>20} {'L1 phi delta':>13}")
print("-" * 88)
for T in (2, 10):
    tw = TWIN.format(T=T)
    pol = arms(POL, T, "prior_only"); twn = arms(tw, T, "regular")
    c = fa.paired_comparison(pol, twn)
    pj, pphi, pbelow, pdeep = measure(sorted(glob.glob(
        f"{POL}/10M/T{T}/repeat-*/prior_only/compaction_measurements.json")))
    tj, tphi, _, _ = measure(sorted(glob.glob(
        f"{tw}/10M/T{T}/repeat-*/regular/compaction_measurements.json")))
    if "intervals" not in c:
        print(f"T={T:<4} {c['verdict']} ({len(c['paired_seeds'])} pairs)"); continue
    iw = c["intervals"]["write_amplification"]; ir = c["intervals"]["point_read_amplification"]
    ratio = pj.get(0, 0) / max(tj.get(0, 1), 1)
    f = lambda i: f"{i['mean']*100:+6.2f} [{i['lower']*100:+6.2f},{i['upper']*100:+6.2f}]"
    print(f"T={T:<4} {pj.get(0,0):>7}/{tj.get(0,0):<5} {ratio:>4.2f}x "
          f"{f(iw):>20} {f(ir):>20} {pphi-tphi:>+13.3f}")
    rows[T] = {"dW": iw["mean"]*100, "dR": ir["mean"]*100, "l0": ratio,
               "dphi": pphi - tphi, "below": pbelow, "deep": pdeep}
print(f"T=6    (reference, measured 2026-09-22)      {T6['l0']:.2f}x   "
      f"{T6['dW']:+6.2f}              {T6['dR']:+6.2f}        {T6['dphi']:+.3f}")
print()

def verdict(ok): return "PASS" if ok else "FAIL"
if len(rows) == 2:
    a, b = rows[2], rows[10]
    print(f"1  band appears (L0 jobs >= 1.15x):        "
          f"T=2 {a['l0']:.2f}x  T=10 {b['l0']:.2f}x   "
          f"-> {verdict(a['l0'] >= 1.15 and b['l0'] >= 1.15)}")
    order = abs(b['dR']) > abs(T6['dR']) and abs(T6['dR']) < abs(a['dR']) \
            and abs(b['dR']) > abs(a['dR'])
    inrange = -11 <= a['dR'] <= -3 and -15 <= b['dR'] <= -5
    print(f"2  dR in range and ordered T10 > T2 > T6:  "
          f"T=2 {a['dR']:+.2f}%  T=10 {b['dR']:+.2f}%  "
          f"(headroom {HEADROOM[10]}/{HEADROOM[2]}/{HEADROOM[6]}) "
          f"-> range {verdict(inrange)}, order {verdict(order)}")
    okw = 0.5 <= a['dW'] <= 4 and 1.5 <= b['dW'] <= 7
    print(f"3  dW in range, T10 > T6 > T2:             "
          f"T=2 {a['dW']:+.2f}%  T=10 {b['dW']:+.2f}%  "
          f"-> range {verdict(okw)}, order {verdict(b['dW'] > T6['dW'] > a['dW'])}")
    print(f"4  L1 phi p50 >= 0.02 below twin:          "
          f"T=2 {a['dphi']:+.3f}  T=10 {b['dphi']:+.3f}   "
          f"-> {verdict(a['dphi'] <= -0.02 and b['dphi'] <= -0.02)}")
    print(f"5  zero deep releases below due:           "
          f"T=2 {a['below']}/{a['deep']}  T=10 {b['below']}/{b['deep']}   "
          f"-> {verdict(a['below'] == 0 and b['below'] == 0)}")
    print()
    print("FALSIFICATION (D-5): if 1 fails, the attribution of T=6's dW/dR to")
    print("the band is wrong, and D-4's reading of its prediction-1 failure")
    print("must be withdrawn.")
PY

echo
echo "DONE. Artifacts:"
echo "  $RESULTS/                  12 policy arms"
echo "  $MANIFEST_ROOT/assoc-v1/10M/T{2,10}/  calibrated trigger-4 manifests"
echo "  gate1/d5_verdict.txt       D-5 predictions 1-5"
