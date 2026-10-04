#!/usr/bin/env bash
# Gate N2, the static comparator (PATHWAYS C; PREREGISTRATION D-13 §4-5,
# D-14 §3), unattended, after 24 and the q-bar amendment. Per workload, in its
# own process as in 24, and per point (T, base, K0) of Theta_s from the
# contract, K0 restricted to admissible values (A-Impl-6, with F the
# configured WRITE_BUFFER_SIZE and K_slow the configured L0_SLOWDOWN_TRIGGER;
# gate_n2_plan.k_cap says why; the plan lists the points excluded):
#   1. the native and static:uniform_0_75 arms (levels Gate N1 left
#      undecided add no repeats: D-19 withdrew D-16 §5's Gate N2 decision);
#   2. 23 on the point's settled native runs, once it has D-13 §5's initial
#      five: the two measured profiles; a profile 23 refuses (D-14 §3) is
#      skipped with its reason, and the rest goes on; any other failure of 23
#      stops the workload;
#   3. static:survival_weighted and static:last_level_emptying, each with the
#      vector measured at its own point only;
# then the profiles not run, listed again, and 04 over the workload.
# gate_n2_plan.py sets the order: per T, steps 1-3, each repeat by repeat
# across the points (its docstring says why).
# Session (CMP-8): one per workload, n2-<workload>, for every point and T;
# 03 keeps a results folder in its session, so a later top-up stays in it.
# Run length: Gate N1's rung (gate_n1_reports.py, as 24), at every point.
#   NVME=/mnt/nvme scripts/dbbench_pipeline/25_gate_n2_chain.sh
#   25_gate_n2_chain.sh plan    the steps and their cost; runs nothing
#   N2_CONFIGS=<file>           only the configurations the file names, one
#                               per line: <workload> <T> <base MiB> <K0>
#                               <profile> <repeats>, repeats being the total
#                               wanted. For a top-up (T in 2/6/10), or the
#                               cross-T check (T 14/20, at most one
#                               configuration per mode and workload): a
#                               measured profile at a point 23 has not
#                               measured runs that point's five natives first
#   N2_REPEATS=5                repeats per configuration (D-13 §5), >= 2; below
#                               five, a screen: the native arms still run five
#                               (the profiles need them), the others fewer
#   N2_T="2 6 10"               the sweep's T values
#   N2_WORKLOADS="assoc powerlaw"
#   N2_RUN_LENGTH_<workload>="<size M> <load %>"   instead of Gate N1's rung
#   NVME (absolute; a relative one is read from the repo root, as in 24),
#   RESUME, ALLOW_ROOT_DISK, MIN_FREE_GB as 24
#   N2_EXPLORATORY=1            PREREGISTRATION D-24 §2's exploratory screen:
#                               ranking only, no claim, never pooled with
#                               Gate N2. Accepts provisional prices (still
#                               measured on this db_bench), and runs as D-22
#                               (j)'s diagnostic runs (DIAGNOSTIC_RUN=1).
#                               Session explore-n2-<workload>, results under
#                               $NVME/explore-n2-<workload>, databases under
#                               $NVME/explore-n2-dbs, an EXPLORATORY marker
#                               (prices sha256 and schema, the db_bench) in
#                               every result folder. Everything else as above
set -Eeuo pipefail

# Files the user gives are read from where 25 was started, before the cd.
for var in N2_CONFIGS PRICES_FILE; do
  [[ -z "${!var:-}" ]] || printf -v "$var" '%s' "$(realpath -m -- "${!var}")"
done
PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"
STAGE=25
# shellcheck source=chain_common.sh
source "$PIPELINE_DIR/chain_common.sh"
# A relative NVME is read from the repository root, as 24 and 03 read it.
NVME="$(realpath -m -- "$NVME")"
WORKLOADS="${N2_WORKLOADS:-assoc powerlaw}"
# D-24 §2: the exploratory screen has its own sessions and folders, so the
# evaluator can never pool it with the formal sweep's.
EXPLORATORY="${N2_EXPLORATORY:-0}"
[[ "$EXPLORATORY" =~ ^[01]$ ]] || fail "N2_EXPLORATORY must be 0 or 1"
PREFIX=n2
explore_check=() diagnostic=()
if (( EXPLORATORY )); then
  PREFIX=explore-n2
  explore_check=(--exploratory)
  diagnostic=(DIAGNOSTIC_RUN=1)
fi
if [[ "$DBBENCH_BUILD_DIR" = /* ]]; then
  DB_BENCH="$DBBENCH_BUILD_DIR/db_bench"
else
  DB_BENCH="$PROJECT_ROOT/$DBBENCH_BUILD_DIR/db_bench"
fi

run_length() {  # $1=workload; prints "<size M> <load %>"
  local override="N2_RUN_LENGTH_$1"
  if [[ -n "${!override:-}" ]]; then
    [[ "${!override}" =~ ^[0-9]+\ [0-9]+$ ]] || fail "$override must be \"<size M> <load %>\""
    echo "${!override}"
  else
    "$PY" "$PIPELINE_DIR/gate_n1_reports.py" "$NVME/n1-$1"
  fi
}

plan() {  # $1=workload $2=size
  "$PY" "$PIPELINE_DIR/gate_n2_plan.py" plan "$1" --nvme "$NVME" \
    --size "$2" ${N2_REPEATS:+--repeats "$N2_REPEATS"} ${N2_T:+--t "$N2_T"} \
    ${N2_CONFIGS:+--configs "$N2_CONFIGS"} --prefix "$PREFIX" \
    --write-buffer "$WRITE_BUFFER_SIZE" --slowdown "$L0_SLOWDOWN_TRIGGER"
}

# D-24 §2: the EXPLORATORY marker at $1 (the workload root), checked against
# these prices and this db_bench, and copied into every result folder under
# it and into the folders given after it. Nothing in formal mode.
mark() {  # $1=workload, then folders
  (( EXPLORATORY )) || return 0
  local w="$1"
  shift
  "$PY" "$PIPELINE_DIR/gate_n2_plan.py" explore-marker --prices "$PRICES_FILE" \
    --db-bench "$DB_BENCH" --session "$PREFIX-$w" "$NVME/$PREFIX-$w" "$@"
}

# One workload. Its WORKLOAD_SKEW, mix and profile are in the environment
# (set when the child starts), so every 03 call inherits them.
chain() {
  local w="$1" chosen size load kind id T base k0 k arms arm root planned vectors line
  local compute keep vec not_run=()
  chosen="$(run_length "$w")"
  read -r size load <<< "$chosen"
  planned="$(plan "$w" "$size")"
  if ! grep -qE '^(run|profiles) ' <<< "$planned"; then
    stamp "$w: nothing planned"
    return 0
  fi
  if (( EXPLORATORY )); then
    stamp "$w: EXPLORATORY screen (D-24 §2; ranking only, never pooled with" \
          "Gate N2), ${size}M at ${load}% load, session $PREFIX-$w"
  else
    stamp "$w: Gate N2, ${size}M at ${load}% load, session $PREFIX-$w"
  fi
  mark "$w"
  # The plan on fd 3, so nothing a step runs can read it from stdin.
  while read -r kind id T base k0 k arms <&3; do
    [[ "$kind" == run || "$kind" == profiles ]] || continue
    root="$NVME/$PREFIX-$w/$id"
    # The point's own vectors, checked against the point (loose end of 03)
    # and against the runs they were measured from. At the profiles step 23
    # runs first when the point has none; a failure of 23 that is not one of
    # D-14 §3's refusals stops this workload.
    compute=()
    [[ "$kind" == profiles ]] && compute=(--compute)
    vectors="$("$PY" "$PIPELINE_DIR/gate_n2_plan.py" profiles ${compute[@]+"${compute[@]}"} \
      "$root" "$T" "$base" "$k0" "$WORKLOAD_PROFILE:${size}M:T$T:" "$size")"
    if [[ "$kind" == profiles ]]; then
      sed "s/^/=== $w $id: /" <<< "$vectors"
      while read -r line; do
        [[ "$line" == STATIC_PROFILE_* ]] || not_run+=("$id: $line")
      done <<< "$vectors"
      continue
    fi
    # A measured profile runs only with its own point's vector (D-14 §3);
    # a refused or waiting one is skipped (reported at the profiles step).
    keep=() vec=()
    for arm in $arms; do
      if [[ "$arm" == static:survival_weighted || "$arm" == static:last_level_emptying ]]; then
        line="$(grep "^STATIC_PROFILE_${arm#static:}=" <<< "$vectors")" || continue
        vec+=("$line")
      fi
      keep+=("$arm")
    done
    (( ${#keep[@]} )) || continue
    # The point's folder is marked before 03 runs in it, and the arm
    # folders 03 made after it returns, a failed arm's too.
    mark "$w" "$root"
    env ${vec[@]+"${vec[@]}"} ${diagnostic[@]+"${diagnostic[@]}"} \
      EXPERIMENT_ARMS="${keep[*]}" SIZE_RATIOS="$T" \
      MAX_BYTES_FOR_LEVEL_BASE="$(( base * 1048576 ))" L0_COMPACTION_TRIGGER="$k0" \
      WORKLOAD_SIZES_M="$size" LOAD_PERCENT="$load" REPEATS="$k" RESUME=1 \
      SESSION_ID="$PREFIX-$w" RESULTS_ROOT="$root" DB_ROOT="$NVME/$PREFIX-dbs/$w/$id" \
      CONFIRM_EXPERIMENTS=YES "$PIPELINE_DIR/03_run_experiments.sh" ||
      { k=$?; mark "$w" || true; exit "$k"; }
    mark "$w"
  done 3<<< "$planned"
  if (( ${#not_run[@]} )); then
    stamp "$w: measured profiles not run (refused by 23, or waiting):"
    printf '    %s\n' "${not_run[@]}"
  fi
  # 3: some arm is refused, e.g. one that did not settle (D-13 §6); the
  # others are scored and the refusals listed.
  "$PY" "$PIPELINE_DIR/04_generate_graphs.py" --results "$NVME/$PREFIX-$w" --summary-only || {
    k=$?
    (( k == 3 )) || exit "$k"
    stamp "$w: 04 refused arms; see $NVME/$PREFIX-$w/graphs/refused_arms.json"
  }
  stamp "$w: done"
}

case "${1:-}" in
  workload)
    chain "$2"
    exit 0
    ;;
  plan)
    if (( EXPLORATORY )); then
      echo "# EXPLORATORY screen (PREREGISTRATION D-24 §2): ranking only, never" \
           "pooled with Gate N2; provisional prices accepted; diagnostic runs"
    fi
    for w in $WORKLOADS; do
      chosen="$(run_length "$w")"
      read -r size load <<< "$chosen"
      echo "# workload $w: ${size}M at ${load}% load"
      (( ! EXPLORATORY )) ||
        echo "# session $PREFIX-$w; results $NVME/$PREFIX-$w; databases $NVME/$PREFIX-dbs/$w"
      plan "$w" "$size"
    done
    exit 0
    ;;
  "") ;;
  *) fail "unknown argument $1; see the comment at the top" ;;
esac

# Checks that fail in seconds: the disk (the sweep must not find its
# folders; a named set adds to them), the preflight marker as 03 checks it,
# q-bar and the prices on this db_bench (D-15 §3e), and the plan itself (a
# missing rung, a bad N2_CONFIGS line).
if [[ -n "${N2_CONFIGS:-}" ]]; then
  node_checks
else
  node_checks "$PREFIX-assoc" "$PREFIX-powerlaw" "$PREFIX-dbs"
fi
# With controller/ present, 13 binds the plugin's hash into the marker; check
# it the way 03 does, or every start is refused.
plugin_args=()
[[ ! -d controller ]] || plugin_args=(--plugin "$CONTROLLER_PLUGIN")
"$PY" "$PIPELINE_DIR/preflight_marker.py" check --marker "$PREFLIGHT_MARKER" \
  --db-bench "$DB_BENCH" ${plugin_args[@]+"${plugin_args[@]}"} \
  --arms "native static:uniform_0_75 static:survival_weighted static:last_level_emptying" ||
  fail "no preflight marker for this db_bench and this code: run 13 (README step c)"
"$PY" "$PIPELINE_DIR/gate_n2_plan.py" check --prices "$PRICES_FILE" \
  --db-bench "$DB_BENCH" ${explore_check[@]+"${explore_check[@]}"} $WORKLOADS ||
  fail "refusing to start"
for w in $WORKLOADS; do
  chosen="$(run_length "$w")"
  read -r size load <<< "$chosen"
  plan "$w" "$size" > /dev/null || fail "$w: no plan"
done

status=0
for w in $WORKLOADS; do
  extra=()
  [[ "$w" == powerlaw ]] && extra=("${POWERLAW[@]}")
  # By absolute path: the working folder is now the repo root.
  env ${extra[@]+"${extra[@]}"} "$BASH" "$PIPELINE_DIR/25_gate_n2_chain.sh" workload "$w" ||
    { stamp "$w FAILED; the other workload goes on"; status=1; }
done
(( status == 0 )) || fail "a workload's chain failed"
stamp "done"
