import json
import math
import os


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value != "0"


def reward_weight(name: str, level: int = None) -> float:
    """Reward weight for `name`, with an optional per-level override.

    Deep levels have a much weaker causal link to L0 stalls than L0 does, and
    their compaction economics differ, so a single weight tuned for L0 is the
    wrong prior for all of them. `RL_REWARD_<NAME>_L<i>` overrides
    `RL_REWARD_<NAME>` for level i.
    """
    default = _REWARD_DEFAULTS[name]
    if level is not None:
        scoped = os.environ.get(f"RL_REWARD_{name}_L{level}")
        if scoped is not None:
            try:
                return float(scoped)
            except ValueError:
                pass
    return _env_float(f"RL_REWARD_{name}", default)

SOCKET_PATH = os.environ.get("RL_COMPACTION_SOCKET_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction.sock"))
MODEL_SAVE_PATH = os.environ.get("RL_MODEL_SAVE_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_model.pt"))
METRICS_LOG_PATH = os.environ.get("RL_METRICS_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_metrics.jsonl"))
IO_LOG_PATH = os.environ.get("RL_IO_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_io.jsonl"))
SERVER_SUMMARY_PATH = os.environ.get("RL_SERVER_SUMMARY_PATH", "")


METRIC_DEFINITIONS_VERSION = "trigger-v2-logical-v3"


def _load_baseline_manifest() -> dict[str, float]:
    """Load the formal objective's bounds from the guard's shared manifest.

    Returns the constraint targets the reward's hinge terms are measured
    against (PATHWAYS Pathway D): the tuned baseline's write amplification,
    space amplification, sorted-run seeks per scan and stall fraction, and
    the average / p99 latency limits. Empty when no manifest is configured
    (an unconstrained ablation), in which case every hinge is inactive.
    """
    path = os.environ.get("RL_BASELINE_SLO_PATH", "")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load RL baseline SLO manifest {path}: {exc}") from exc
    if manifest.get("schema_version") != 2:
        raise RuntimeError(f"unsupported RL baseline SLO schema in {path}")
    if manifest.get("metric_definitions_version") != METRIC_DEFINITIONS_VERSION:
        raise RuntimeError(
            f"unsupported RL metric definitions in baseline SLO {path}")
    if manifest.get("guard_calibrated") is not True:
        raise RuntimeError(f"RL live guard is not calibrated in {path}")
    expected = os.environ.get("RL_EXPERIMENT_FINGERPRINT", "")
    actual = manifest.get("experiment_fingerprint", "")
    if not expected:
        raise RuntimeError(
            "RL_EXPERIMENT_FINGERPRINT is required with a baseline SLO")
    if actual != expected:
        raise RuntimeError(
            f"RL baseline SLO fingerprint mismatch: expected {expected}, got {actual}")
    # These unprefixed limits are the preregistered formal objective the reward
    # should optimize. The guard_* fields deliberately describe a different
    # instrument: a rolling, in-process classifier calibrated from compact
    # telemetry. Using guard_* here would make a safety tolerance the learning
    # target and silently relax the final whole-run latency objective.
    #
    # Average and p99, not p95: the acceptance metrics are avg + p99 (P0-4);
    # write p95 is diagnostic only (P0-5).
    names = (
        "get_latency_avg_ns_limit", "get_latency_p99_ns_limit",
        "scan_latency_avg_ns_limit", "scan_latency_p99_ns_limit",
        "write_latency_avg_ns_limit", "write_latency_p99_ns_limit",
        "write_amplification_reference", "space_amplification_reference",
        "sorted_run_seeks_per_scan_reference", "stall_fraction_reference",
        # D-6: the space hinge is measured in BYTES, not as a ratio. See
        # SPACE_BYTES_BOUND below for why the ratio form was unusable.
        "expected_physical_sst_bytes",
    )
    try:
        limits = {name: float(manifest[name]) for name in names}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"baseline SLO manifest lacks objective references: {path}") from exc
    if any(not math.isfinite(value) or value < 0.0
           for value in limits.values()):
        raise RuntimeError(
            f"baseline SLO manifest has invalid objective references: {path}")
    if any(limits[name] <= 0.0 for name in names[:6]):
        raise RuntimeError(
            f"baseline SLO manifest has invalid formal latency limits: {path}")
    return limits


BASELINE_LIMITS = _load_baseline_manifest()

# Constraint margins, exactly as the paired evaluator applies them: write and
# scan at 2% paired relative non-inferiority (P0-3, P0-1), stall at zero
# margin (P0-2). The space budget is the swept rung the run executes (P0-6);
# the runner passes it so the learner is trained against the bound it will be
# judged at. Bounds are absolute values of the whole-run metric.
WRITE_RELATIVE_MARGIN = _env_float("RL_WRITE_RELATIVE_MARGIN", 0.02)
SCAN_RELATIVE_MARGIN = _env_float("RL_SCAN_RELATIVE_MARGIN", 0.02)
SPACE_RELATIVE_MARGIN = _env_float("RL_SPACE_RELATIVE_MARGIN", 0.02)
WRITE_BOUND = (BASELINE_LIMITS.get("write_amplification_reference", 0.0)
               * (1.0 + WRITE_RELATIVE_MARGIN))
# D-6 (2026-09-22): the space constraint is settled physical SST bytes against
# the tuned baseline's, at the rung's margin -- the same quantity and the same
# unit the C++ guard already uses (`allowed_physical_sst_bytes`,
# rl_safety_manifest.cc:200/345).
#
# The ratio form it replaces was unusable. The live per-frame signal divides
# physical bytes by `EstimateLiveDataSize`, while stage 06 computes
# `space_amplification_reference` on D-3's measured garbage-free denominator,
# so the two sides were different metrics: the hinge read +0.751 / +0.134 /
# +0.050 on frame one at T = 2 / 6 / 10 regardless of what the policy did, and
# lambda_space would have ascended monotonically in every cell. Pathway D's D-4
# criterion reads monotone lambda divergence as an infeasible cell, so the
# learner would have manufactured a confirmation of the theory out of a unit
# mismatch.
#
# Bytes rather than a repaired ratio because the denominator is a constant of
# the workload, not of the policy: `filluniquerandom` writes 2.9M unique keys
# and `mixgraph` only overwrites them, and D-3 measured the garbage-free size
# stable to 0.0060% across 108 runs. Dividing by a constant adds nothing except
# the estimate's depth-sensitivity, which is the defect D-3 documents.
SPACE_BOUND = (BASELINE_LIMITS.get("space_amplification_reference", 0.0)
               * (1.0 + SPACE_RELATIVE_MARGIN))   # diagnostic only since D-6
SPACE_BYTES_BOUND = (BASELINE_LIMITS.get("expected_physical_sst_bytes", 0.0)
                     * (1.0 + SPACE_RELATIVE_MARGIN))
SCAN_SEEKS_BOUND = (BASELINE_LIMITS.get("sorted_run_seeks_per_scan_reference", 0.0)
                    * (1.0 + SCAN_RELATIVE_MARGIN))
STALL_FRACTION_BOUND = BASELINE_LIMITS.get("stall_fraction_reference", 0.0)

ACTION_DIM = 2  # 0=do_nothing, 1=compact_now
ACTION_NAMES = {0: "do_nothing", 1: "compact_now"}

# Multi-level (protocol v2): one DQN agent per LSM level. Each level's state
# combines its own observables, the next level's observables (zeros for the
# last level), and global pressure signals. See rl_agent/multilevel.py.
ML_MAX_LEVELS = _env_int("RL_ML_MAX_LEVELS", 16)
ML_STATE_FIELDS = (
    # -- own level ------------------------------------------------------
    "score_norm",              # min(score, SCORE_CLAMP) / SCORE_CLAMP
    "fullness",                # L0: files/trigger; L>=1: bytes/target
    "files_norm",
    "bytes_norm",
    "arrival_rate_norm",       # bytes arriving in level, per second
    "compaction_io_rate_norm",  # compaction I/O from level, per second
    "compaction_event_rate_norm",
    "seconds_since_compaction",  # wall-clock, saturating at 30 s
    "due_age_norm",            # wall-clock age of the current due episode
    "pressure_integral_norm",  # integral of max(score - 1, 0)
    "gate_open",
    "blocked_rate_norm",
    "backoff_flag",
    # -- next level -----------------------------------------------------
    "next_fullness",
    "next_score_norm",
    "next_files_norm",
    "overlap_ratio",           # next-level overlap bytes / own bytes
    "overlap_norm",
    # -- pressure (defined for every level, no dead inputs) --------------
    "slowdown_pressure",
    "stop_pressure",
    "stall_flag",
    "stop_flag",
    "pending_norm",
    "default_needed",
    # -- read path ------------------------------------------------------
    "read_rate_norm",          # (gets + seeks) per second
    "l0_hit_fraction",         # share of gets served from L0
    # SST file reads per read operation — physical read amplification.
    # Replaces `non_last_read_fraction`, which was the ratio *between* the
    # non-last and last level classes and measured p50 = 1.00 / mean 0.978
    # across a whole 5M run: a constant input, and one that also fed the
    # deep-level read term of the reward.
    "file_reads_per_op_norm",
    "point_probe_amp_norm",    # logical SST probes / point Get
    "scan_work_amp_norm",      # (returned + internal skipped) / returned
    "space_amp_norm",          # physical SST bytes / live logical bytes
    "structural_dirty_flag",   # source generation is newer than built view
)
ML_STATE_DIM = len(ML_STATE_FIELDS)

# ---------------------------------------------------------------------------
# Reward: the constrained objective of PATHWAYS Pathway D
# ---------------------------------------------------------------------------
# Every per-level action changes one tree, so every head receives the same
# whole-tree transition reward (multilevel.MultiLevelProcessor._global_reward):
#
#   r = shaping
#       - dt * ( REWARD_READ * point_probes_per_get
#              + lambda_W * [W_window  - W_bound]^+
#              + lambda_S * [S         - S_bound]^+
#              + lambda_L * latency_excess_over_avg_and_p99_limits
#              + lambda_scan * [seeks_per_scan - seeks_bound]^+
#              + lambda_stall * [stall_fraction - stall_bound]^+ )
#
# The objective is point-read amplification, charged as a rate; everything
# else is a constraint and enters ONLY through a hinge above its bound
# (Proposition D.2), so the learner earns nothing for space or write headroom
# it is not judged on. Space is therefore not a minimand anywhere.
#
# shaping = Phi_prev - gamma^dt * Phi_now with Phi = -REWARD_STRUCTURAL_RUNS
# * (L0 files + non-empty deeper levels): the absolute sorted-run count,
# which is what a point lookup probes. Potential-based, so policy-invariant
# (Proposition D.1, gamma^tau form). It is in absolute runs, not runs over
# the L0 trigger, so removing one L0 file is worth the same at trigger 2 as
# at trigger 16.
#
# The multipliers follow dual ascent on the slow timescale (Proposition D.3):
# lambda <- clip(lambda + LAMBDA_LR * violation, 0, LAMBDA_MAX) once per
# frame. A lambda that grows without plateauing is D-4's infeasibility
# signal, so it is logged in every reward component record.
REWARD_READ = _env_float("RL_REWARD_READ", 1.0)
REWARD_STRUCTURAL_RUNS = _env_float("RL_REWARD_STRUCTURAL_RUNS", 0.5)
LAMBDA_WRITE_INIT = _env_float("RL_LAMBDA_WRITE_INIT", 1.0)
LAMBDA_SPACE_INIT = _env_float("RL_LAMBDA_SPACE_INIT", 1.0)
LAMBDA_LATENCY_INIT = _env_float("RL_LAMBDA_LATENCY_INIT", 1.0)
LAMBDA_SCAN_INIT = _env_float("RL_LAMBDA_SCAN_INIT", 1.0)
LAMBDA_STALL_INIT = _env_float("RL_LAMBDA_STALL_INIT", 1.0)
LAMBDA_LR = _env_float("RL_LAMBDA_LR", 0.01)
LAMBDA_MAX = _env_float("RL_LAMBDA_MAX", 100.0)
# Write amplification is a ratio of byte totals. Over one 50 ms window it is
# undefined whenever no Put landed, and over the whole run it is a constant
# that no single action can move. It is therefore measured over an
# exponentially weighted window of this length, which is what the hinge sees.
WAF_WINDOW_SECONDS = _env_float("RL_WAF_WINDOW_SECONDS", 10.0)

# Score headroom. With deferral enabled a level's score legitimately exceeds
# 1.0, so the feature needs range above the trigger point; the previous /2.0
# scaling only ever spanned [0, 0.5] because the agent was never consulted
# above score 1.
SCORE_CLAMP = _env_float("RL_SCORE_CLAMP", 3.0)

# DQN hyperparameters
HIDDEN_DIM = _env_int("RL_HIDDEN_DIM", 64)
LEARNING_RATE = _env_float("RL_LEARNING_RATE", 1e-3)
GAMMA = _env_float("RL_GAMMA", 0.99)
# DECISION CADENCE. Protocol v2 pins the observation and decision interval at
# 50 ms (RL_OBSERVE_INTERVAL_MS == RL_DECISION_INTERVAL_MS), so the controller
# sees ~20 frames per second, each carrying one decision per populated level,
# for the whole measured phase: on the order of 10^5 frames per 10M run. The
# controller is suspended during the bulk load (db_bench `rlsuspend`), so the
# first frame is the first measured operation. Any constant below that is
# expressed in decisions is sized against that cadence; anything that must be
# invariant to it is expressed in wall-clock seconds instead.
BATCH_SIZE = _env_int("RL_BATCH_SIZE", 32)
REPLAY_BUFFER_SIZE = _env_int("RL_REPLAY_BUFFER_SIZE", 100_000)
# ~1000 finalized transitions arrive within the first minute across the
# populated levels; training on fewer fits the first few seconds of a run.
MIN_REPLAY_SIZE = _env_int("RL_MIN_REPLAY_SIZE", 1000)
# A batch larger than the warmup threshold would make random.sample() raise on
# the first training step, so the two are tied together here rather than left
# to whoever edits one of them.
MIN_REPLAY_SIZE = max(MIN_REPLAY_SIZE, BATCH_SIZE)
TARGET_UPDATE_INTERVAL = _env_int("RL_TARGET_UPDATE_INTERVAL", 200)
# Polyak (soft) target updates. A hard sync every 200 steps fires once or twice
# in a run this short, leaving the bootstrap target frozen for most of it.
# tau=0 restores the hard-sync behaviour for ablation.
TARGET_TAU = _env_float("RL_TARGET_TAU", 0.01)

# Exploration. Uniform random actions were tolerable when the agent could only
# waste I/O; with deferral enabled an action can degrade the database, so the
# default is Boltzmann sampling over the prior-composed Q-values — exploration
# guided by LSM physics from step 0 rather than blind.
EXPLORATION = os.environ.get("RL_EXPLORATION", "boltzmann").strip().lower()
# Decisions over which exploration anneals. This must be a fraction of the run
# budget above, not a round number: at 1000 against a 185-decision 1M run the
# temperature only fell 1.00 -> 0.905, i.e. the agent stayed essentially random
# for the whole run — the same defect as the original 2000-step default, just
# with a different constant. 60 anneals within a 1M run; experiment_runner.sh
# overrides it from the workload's actual op count.
EXPLORATION_DECAY_STEPS = _env_int("RL_EXPLORATION_DECAY_STEPS", 60)
# D6. Anneal on wall time instead of decision count when this is positive.
#
# A decision-count schedule has now been invalidated twice by repairs to the
# control loop: the original 2000-step default, and then the 0.11-decisions-per
# -1000-operations constant the pipeline derived from a 2.6/s observation rate
# that the Phase 1a repair raised to ~20/s. Any schedule keyed to decision count
# is wrong again the moment the cadence changes -- and event-driven triggering
# would make the decision count workload-dependent rather than merely faster.
# Wall time is what actually determines how much of the run remains.
#
# The schedule SHAPE is unchanged: linear to a floor, not an exponential
# half-life. A half-life never reaches its floor, and the acceptance check for
# this fix is "temperature reaches its floor around one third of the way through
# the run", so only the clock is being replaced here, not the curve.
EXPLORATION_ANNEAL_SECONDS = _env_float("RL_EXPLORATION_ANNEAL_SECONDS", 0.0)
BOLTZMANN_TEMP_START = _env_float("RL_BOLTZMANN_TEMP_START", 1.0)
BOLTZMANN_TEMP_END = _env_float("RL_BOLTZMANN_TEMP_END", 0.05)
EPSILON_START = _env_float("RL_EPSILON_START", 1.0)
EPSILON_END = _env_float("RL_EPSILON_END", 0.05)
EPSILON_DECAY_STEPS = _env_int("RL_EPSILON_DECAY_STEPS", EXPLORATION_DECAY_STEPS)

# Model persistence. The save runs on the decision path; at ~20 decisions per
# second per level this is roughly one checkpoint every fifteen minutes.
SAVE_INTERVAL = _env_int("RL_SAVE_INTERVAL", 20_000)

# Runtime behavior
ASYNC_TRAINING = os.environ.get("RL_ASYNC_TRAINING", "1") != "0"
# Gradient steps per training request. With ASYNC_TRAINING the requests of
# all levels in a frame coalesce into one event, so this bounds the trainer's
# duty cycle rather than multiplying the frame rate. With the analytic prior
# carrying the policy, these steps fit a residual rather than Q from scratch.
TRAIN_STEPS_PER_OBSERVATION = _env_int("RL_TRAIN_STEPS_PER_OBSERVATION", 8)

# Diagnostics only — NOT the evaluation protocol. The reported result must be a
# cold online run, since the research claim is adaptation with no prior workload
# knowledge. These exist to answer "does the agent learn anything given more
# data?", which is a debugging question.
RESUME = _env_bool("RL_RESUME", False)
EVAL_MODE = _env_bool("RL_EVAL_MODE", False)

# Reproducibility. 0 = unseeded (legacy behavior). Any other value seeds
# python/numpy/torch at server startup so a run's decision trajectory is
# repeatable; sweeps should set distinct seeds per repeat.
SEED = _env_int("RL_SEED", 0)

# Model architecture. DUELING enables a value/advantage decomposition head
# (opt-in so existing results remain comparable).
DUELING = os.environ.get("RL_DUELING", "0") != "0"

# Shared trunk with per-level output heads, one pooled replay buffer.
#
# The sample budget is the binding constraint on this system: a level agent
# receives 550-615 decisions on a 5M run and 110-120 on a 1M run (measured from
# the recorded decision counts, 2026-08-06), and that is the entire online
# dataset an independent per-level network gets. Pooling puts every level's
# transitions through one trunk, so the shared representation trains on ~2750
# samples per 5M run instead of ~550, while each level keeps its own head, its
# own credit windows and its own exploration schedule.
#
# RL_SHARED_TRUNK=0 restores independent per-level networks for ablation.
SHARED_TRUNK = _env_bool("RL_SHARED_TRUNK", True)

# Multi-level exploration guardrail: mask `compact_now` for a level whose
# RocksDB score is below this floor (or that has no files). Prevents fresh
# high-epsilon deep-level agents from randomly compacting near-empty levels,
# which is never sensible and dominates early-run cost. 0 disables masking.
#
# This must agree with the C++ bridge's `RL_OPTIONAL_MIN_SCORE`, which decides
# whether a below-threshold compact action is granted an optional token at all.
# If the mask were looser than the gate, every action in the gap between them
# would be offered to the learner and then silently dropped at admission — the
# transition is recorded as the executed defer, so nothing is corrupted, but a
# whole band of the action space would be unreachable while appearing
# available. One environment variable therefore feeds both sides.
ML_MIN_COMPACT_SCORE = _env_float(
    "RL_ML_MIN_COMPACT_SCORE", _env_float("RL_OPTIONAL_MIN_SCORE", 0.10))

# Multi-level stall attribution: when enabled, the global stall/stop penalty is
# scaled by the level's own fullness, so a near-empty deep level is not charged
# for an L0-caused stall (lightweight version of per-level stall attribution).
ML_STALL_SCALE_BY_PRESSURE = os.environ.get("RL_ML_STALL_SCALE_BY_PRESSURE", "1") != "0"

# Credit assignment. A compaction triggered by `compact_now` completes
# asynchronously, so its I/O cost and its L0/pending relief land several
# decisions later. N_STEP aggregates the discounted per-step rewards over the
# next N_STEP decisions before finalizing a transition, so delayed relief
# propagates back to the action that caused it.
#   N_STEP = 1 reproduces the original one-step DQN (use for the ablation baseline).
N_STEP = _env_int("RL_N_STEP", 5)
# Wall-clock credit horizon. A count-based window is the wrong unit: at a 50 ms
# cadence, N_STEP=5 looks 250 ms ahead while a compaction takes seconds, so the
# window captured the action's cost but never its relief. A decision finalises
# once this much wall time has elapsed. Set to 0 to use the count-based N_STEP.
# Measured on the 1M run: decisions arrive ~1.65s apart (median), and the
# realised credit lag at 2000ms was 2.85s — barely more than one decision of
# lookahead, i.e. effectively one-step credit. 4000ms spans 2-3 decisions.
# Tune from the `credit_lag_s` diagnostic rather than by guessing; it is logged
# per decision.
CREDIT_HORIZON_MS = _env_int("RL_CREDIT_HORIZON_MS", 4_000)
# Discount expressed per second rather than per step. Decision intervals vary,
# so a fixed per-step gamma discounts wall-clock time inconsistently (an SMDP,
# not an MDP). 0 disables and falls back to the per-step GAMMA.
GAMMA_PER_SEC = _env_float("RL_GAMMA_PER_SEC", 0.95)
# Double DQN decouples next-action selection (policy net) from its evaluation
# (target net), reducing Q-value overestimation on the noisy aggregated reward.
DOUBLE_DQN = os.environ.get("RL_DOUBLE_DQN", "1") != "0"

# Adaptive normalization. Scales track observed maxima with a small decay so
# the agent can adapt when the workload regime changes.
NORMALIZER_DECAY = _env_float("RL_NORMALIZER_DECAY", 0.995)
# ...but a scale that keeps moving means the same raw observation encodes to a
# different vector over time, so replayed transitions were recorded against an
# encoding that no longer exists. Freeze the scales once enough of the workload
# has been seen. 0 disables freezing.
#
# Wall-clock, not a frame count: the previous 50-frame freeze fired 2.5 s
# into the run, before any read-path scale (point probes per Get, seeks,
# read rate) had seen a representative value, so those features saturated at
# their floor for the whole measured phase and the state carried no
# information about the primary objective. Thirty seconds of controlled time
# spans hundreds of compactions at every populated level.
NORM_FREEZE_SECONDS = _env_float("RL_NORM_FREEZE_SECONDS", 30.0)

# ---------------------------------------------------------------------------
# Legacy per-level reward (RL_REWARD_LEGACY=1), retained as a Gate 6 ablation
# only. The live reward is the constrained whole-tree form above.
# ---------------------------------------------------------------------------
REWARD_LEGACY = _env_bool("RL_REWARD_LEGACY", False)

_REWARD_DEFAULTS = {
    "LATE_NO_COMPACTION": 0.30,
    "L0_PRESSURE": 0.20,
    "SLOWDOWN_PRESSURE": 0.20,
    "STOP_PRESSURE": 0.30,
    "L0_GROWTH": 0.20,
    "PENDING_PRESSURE": 0.15,
    "PENDING_GROWTH": 0.15,
    "STALL": 0.70,
    "STOP": 1.00,
    "COMPACTION_IO": 0.15,
    "COMPACTION_EVENT": 0.05,
    "UNNECESSARY_COMPACTION": 0.15,
    "PRESSURE_RELIEF": 0.60,
}

# Module-level aliases for the legacy reward path
# (multilevel._compute_reward_legacy).
REWARD_L0_PRESSURE = reward_weight("L0_PRESSURE")
REWARD_SLOWDOWN_PRESSURE = reward_weight("SLOWDOWN_PRESSURE")
REWARD_STOP_PRESSURE = reward_weight("STOP_PRESSURE")
REWARD_L0_GROWTH = reward_weight("L0_GROWTH")
REWARD_PENDING_PRESSURE = reward_weight("PENDING_PRESSURE")
REWARD_PENDING_GROWTH = reward_weight("PENDING_GROWTH")
REWARD_STALL = reward_weight("STALL")
REWARD_STOP = reward_weight("STOP")
REWARD_COMPACTION_IO = reward_weight("COMPACTION_IO")
REWARD_COMPACTION_EVENT = reward_weight("COMPACTION_EVENT")
REWARD_UNNECESSARY_COMPACTION = reward_weight("UNNECESSARY_COMPACTION")
REWARD_LATE_NO_COMPACTION = reward_weight("LATE_NO_COMPACTION")
REWARD_PRESSURE_RELIEF = reward_weight("PRESSURE_RELIEF")

# ---------------------------------------------------------------------------
# Physics-informed analytic prior:  Q(s,a) = b(s,a) + f_theta(s,a)
# ---------------------------------------------------------------------------
# The residual head is zero-initialised, so the policy at step 0 IS the
# analytic LSM-physics policy and learning only ever adds corrections. This is
# how the agent is competent immediately without any pre-training, which the
# research claim (online adaptation, no prior workload knowledge) requires.
ANALYTIC_PRIOR = _env_bool("RL_ANALYTIC_PRIOR", True)
PRIOR_W_STALL = _env_float("RL_PRIOR_W_STALL", 1.0)
# Deep levels: INERT since PREREGISTRATION D-4 (2026-09-22). A level below
# L0 is one sorted run whatever its size (A4), so it carries no read term;
# the depth charge this weight used to scale was dropped there (trivial
# move, Corollary A.3). Kept as an ablation knob only.
PRIOR_W_READ = _env_float("RL_PRIOR_W_READ", 0.6)
# L0: a proactive (below-trigger) compaction must remove at least this many
# sorted runs beyond what native RocksDB would remove one flush later, or it
# earns no read relief. Absolute runs, not a fraction of the trigger: at
# trigger 2 the only below-threshold state is one file, which this leaves
# with zero relief (history 14.10).
PRIOR_MIN_RUN_REDUCTION = _env_float("RL_PRIOR_MIN_RUN_REDUCTION", 2.0)
# L0 gets its own, much larger read weight. Its runs overlap, so every extra L0
# file is probed by every lookup and flushes queue behind it; a deeper level is
# one sorted run whatever its size. With a single shared weight the agent came
# out inverted on the write-heavy 1M workload — compacting L1 88% of the time
# while holding L0 back at 41%. The picker's wall-clock/pressure safety
# envelope bounds L0 deferral structurally; this makes the policy prefer L0
# progress before that safety mechanism has to override it.
PRIOR_W_READ_L0 = _env_float("RL_PRIOR_W_READ_L0", 2.0)
PRIOR_W_WORK = _env_float("RL_PRIOR_W_WORK", 0.8)
PRIOR_W_PREMATURE = _env_float("RL_PRIOR_W_PREMATURE", 0.5)
PRIOR_CLAMP = _env_float("RL_PRIOR_CLAMP", 2.0)
# Price a deep-level compaction by its MARGINAL cost — one file plus the
# next-level files it overlaps, expressed as a write-amplification ratio —
# rather than by the cost of merging the whole level.
#
# The old form made an overfull level look progressively more expensive to fix
# (compacting L1 scored +0.82 at target but only +0.58 at 3x), which is
# backwards and is why L1 sat at 1.50x target for 63% of the 5M run while
# contributing 72% of the excess read-probe cost. Without this the read-side
# fix is inert: the relief term grows with fullness and the work term cancels
# it exactly.
#
# Side effect, measured by replaying the recorded 5M states: L1's advantage
# rises (+0.12) as intended, but L2/L3/L4 fall (-0.40/-0.26/-0.08) because
# compacting an UNDERFULL level into one with heavy overlap has poor write
# amplification, which the old form under-priced. That is defensible but was
# not the goal, so it is gated: RL_PRIOR_MARGINAL_WORK=0 restores the
# whole-level form for ablation.
PRIOR_MARGINAL_WORK = _env_bool("RL_PRIOR_MARGINAL_WORK", True)
RESIDUAL_WEIGHT_DECAY = _env_float("RL_RESIDUAL_WEIGHT_DECAY", 0.0)
