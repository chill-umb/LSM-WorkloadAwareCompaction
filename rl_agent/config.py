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


def _load_latency_limits() -> dict[str, float]:
    """Load formal reward budgets from the guard's shared manifest."""
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
    if manifest.get("metric_definitions_version") != "trigger-v2-logical-v2":
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
    names = (
        "get_latency_avg_ns_limit", "get_latency_p95_ns_limit",
        "scan_latency_avg_ns_limit", "scan_latency_p95_ns_limit",
        "write_latency_avg_ns_limit", "write_latency_p95_ns_limit",
    )
    try:
        limits = {name: float(manifest[name]) for name in names}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"baseline SLO manifest lacks latency limits: {path}") from exc
    if any(not math.isfinite(value) or value <= 0.0
           for value in limits.values()):
        raise RuntimeError(
            f"baseline SLO manifest has invalid formal latency limits: {path}")
    return limits


BASELINE_LATENCY_LIMITS = _load_latency_limits()

STATE_FIELDS = (
    "l0_files_norm",
    "l0_size_norm",
    "l0_score_norm",
    "l0_delay_norm",
    "l0_compaction_trigger_pressure",
    "l0_slowdown_pressure",
    "l0_stop_pressure",
    "pending_compaction_norm",
    "flushed_bytes_norm",
    "compaction_read_norm",
    "compaction_write_norm",
    "l0_compaction_event_norm",
    "stall_norm",
    "default_l0_compaction_needed",
)
STATE_DIM = len(STATE_FIELDS)
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
    "steps_since_compaction",
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

# Global cooperative reward. Every per-level action changes one tree, so every
# head receives this same transition reward rather than an independently
# manufactured per-level potential.
GLOBAL_REWARD_TREE_POINT = _env_float("RL_GLOBAL_REWARD_TREE_POINT", 0.35)
GLOBAL_REWARD_TREE_SCAN = _env_float("RL_GLOBAL_REWARD_TREE_SCAN", 0.25)
GLOBAL_REWARD_TREE_SPACE = _env_float("RL_GLOBAL_REWARD_TREE_SPACE", 0.20)
GLOBAL_REWARD_TREE_DEBT = _env_float("RL_GLOBAL_REWARD_TREE_DEBT", 0.15)
GLOBAL_REWARD_TREE_STALL = _env_float("RL_GLOBAL_REWARD_TREE_STALL", 0.05)
GLOBAL_REWARD_WAF = _env_float("RL_GLOBAL_REWARD_WAF", 0.20)
GLOBAL_REWARD_POINT = _env_float("RL_GLOBAL_REWARD_POINT", 0.15)
GLOBAL_REWARD_SCAN = _env_float("RL_GLOBAL_REWARD_SCAN", 0.10)
GLOBAL_REWARD_LATENCY = _env_float("RL_GLOBAL_REWARD_LATENCY", 0.05)

# Score headroom. With deferral enabled a level's score legitimately exceeds
# 1.0, so the feature needs range above the trigger point; the previous /2.0
# scaling only ever spanned [0, 0.5] because the agent was never consulted
# above score 1.
SCORE_CLAMP = _env_float("RL_SCORE_CLAMP", 3.0)

# DQN hyperparameters
HIDDEN_DIM = _env_int("RL_HIDDEN_DIM", 64)
LEARNING_RATE = _env_float("RL_LEARNING_RATE", 1e-3)
GAMMA = _env_float("RL_GAMMA", 0.99)
# MEASURED DECISION BUDGET (2026-08-02, this machine, default db_runner args):
# a run produces about **0.19 decisions per 1000 operations per level agent**,
# essentially independent of workload size:
#
#     ops        wall     decisions/agent
#     60k        4.6s      ~13
#     250k      27.3s      ~50
#     1M       333.0s     ~185
#
# Decisions track flush/compaction scheduling events, not wall time and not the
# decision tick (the 1M run skipped 6475 ticks and issued only 182 queries),
# because RocksDB only calls NeedsCompaction when the column family is not
# already queued for compaction. Every hyperparameter below is sized against
# that budget: anything gated on a step count larger than ~185 simply never
# happens on a 1M workload.
BATCH_SIZE = _env_int("RL_BATCH_SIZE", 32)
REPLAY_BUFFER_SIZE = _env_int("RL_REPLAY_BUFFER_SIZE", 100_000)
# Was 200, which exceeded the entire per-agent decision budget of a 1M run —
# the buffer never reached the threshold and **training never ran at all**.
MIN_REPLAY_SIZE = _env_int("RL_MIN_REPLAY_SIZE", 32)
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

# Model persistence
SAVE_INTERVAL = _env_int("RL_SAVE_INTERVAL", 500)

# Runtime behavior
ASYNC_TRAINING = os.environ.get("RL_ASYNC_TRAINING", "1") != "0"
# More gradient steps per observation: at ~185 decisions per agent on a 1M run,
# each transition has to be reused heavily or the agent sees almost no gradient
# signal at all. With the analytic prior carrying the policy, these steps are
# fitting a residual rather than learning Q from scratch, which is what makes
# such a small sample budget survivable.
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
# has been seen. 0 disables freezing (legacy behaviour). Sized against the
# measured budget: at 200 it never fired on a 1M run, so the scales drifted for
# the entire run and the freeze was a no-op.
NORM_FREEZE_AFTER = _env_int("RL_NORM_FREEZE_AFTER", 50)

# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------
# The reward is a potential difference plus directly measured costs:
#
#   Phi(s) = W_STALL*stall_risk + W_READ*read_amp + W_SPACE*space_overshoot
#   r      = -(Phi(s') - Phi(s)) - dt * (measured I/O, stall, read-amp costs)
#
# Potential-based shaping (Ng et al., 1999) leaves the optimal policy
# unchanged, and the differential form centres the reward near zero. The
# previous formulation summed eleven always-on penalties and clamped to
# [-1, 1], so the signal was a near-constant negative offset whose clamp
# saturated in exactly the high-pressure states that mattered.
#
# The `dt` factor makes a credit-window return independent of how many
# decisions the window happened to contain; see _compute_reward for the
# measurements that forced it.
REWARD_LEGACY = _env_bool("RL_REWARD_LEGACY", False)
# OFF by default. Standardizing divides the reward by a running standard
# deviation, which makes the target scale arbitrary and workload-dependent —
# exactly what the analytic prior needs it not to be. Q(s,a) = b(s,a) +
# f_theta(s,a) is only meaningful while b and the return live on the same
# scale: with standardization on, raw rewards averaging |0.083| were inflated
# ~5x (the MIN_STD=0.05 floor binds on near-idle levels) before being summed
# into returns averaging |9.46|, against a prior clamped to +-2.0.
#
# With the dt-integrated reward the natural scale is already right: a cost
# rate of ~0.083 integrated over the 4s credit horizon lands at ~0.33, which
# is the same order as the analytic advantage (mean |0.284|).
#
# RL_REWARD_STANDARDIZE=1 restores the old behaviour for ablation.
REWARD_STANDARDIZE = _env_bool("RL_REWARD_STANDARDIZE", False)

_REWARD_DEFAULTS = {
    # Potential terms.
    "POTENTIAL_STALL": 1.00,
    "POTENTIAL_READ": 0.60,
    "POTENTIAL_SPACE": 0.30,
    # Directly measured costs.
    "COST_IO": 0.30,
    "COST_STALL": 0.70,
    "COST_STOP": 1.00,
    "COST_READ_AMP": 0.40,
    # Retained safety signal, re-keyed to observed stalls rather than to
    # RocksDB's own trigger (which made the agent imitate the baseline).
    "LATE_NO_COMPACTION": 0.30,
    # Legacy weights, used only when REWARD_LEGACY is set.
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

# Module-level aliases kept so existing scripts and the legacy L0 reward path
# (reward.py) keep working unchanged.
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
PRIOR_W_READ = _env_float("RL_PRIOR_W_READ", 0.6)
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
