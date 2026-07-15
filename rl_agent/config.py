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

SOCKET_PATH = os.environ.get("RL_COMPACTION_SOCKET_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction.sock"))
MODEL_SAVE_PATH = os.environ.get("RL_MODEL_SAVE_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_model.pt"))
METRICS_LOG_PATH = os.environ.get("RL_METRICS_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_metrics.jsonl"))
IO_LOG_PATH = os.environ.get("RL_IO_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_io.jsonl"))

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

# DQN hyperparameters
HIDDEN_DIM = _env_int("RL_HIDDEN_DIM", 64)
LEARNING_RATE = _env_float("RL_LEARNING_RATE", 1e-3)
GAMMA = _env_float("RL_GAMMA", 0.99)
BATCH_SIZE = _env_int("RL_BATCH_SIZE", 64)
REPLAY_BUFFER_SIZE = _env_int("RL_REPLAY_BUFFER_SIZE", 100_000)
MIN_REPLAY_SIZE = _env_int("RL_MIN_REPLAY_SIZE", 64)
TARGET_UPDATE_INTERVAL = _env_int("RL_TARGET_UPDATE_INTERVAL", 200)

# Exploration
EPSILON_START = _env_float("RL_EPSILON_START", 1.0)
EPSILON_END = _env_float("RL_EPSILON_END", 0.05)
EPSILON_DECAY_STEPS = _env_int("RL_EPSILON_DECAY_STEPS", 2_000)

# Model persistence
SAVE_INTERVAL = _env_int("RL_SAVE_INTERVAL", 500)

# Runtime behavior
ASYNC_TRAINING = os.environ.get("RL_ASYNC_TRAINING", "1") != "0"
TRAIN_STEPS_PER_OBSERVATION = _env_int("RL_TRAIN_STEPS_PER_OBSERVATION", 1)

# Credit assignment. A compaction triggered by `compact_now` completes
# asynchronously, so its I/O cost and its L0/pending relief land several
# decisions later. N_STEP aggregates the discounted per-step rewards over the
# next N_STEP decisions before finalizing a transition, so delayed relief
# propagates back to the action that caused it.
#   N_STEP = 1 reproduces the original one-step DQN (use for the ablation baseline).
N_STEP = _env_int("RL_N_STEP", 5)
# Double DQN decouples next-action selection (policy net) from its evaluation
# (target net), reducing Q-value overestimation on the noisy aggregated reward.
DOUBLE_DQN = os.environ.get("RL_DOUBLE_DQN", "1") != "0"

# Adaptive normalization. Scales track observed maxima with a small decay so
# the agent can adapt when the workload regime changes.
NORMALIZER_DECAY = _env_float("RL_NORMALIZER_DECAY", 0.995)

# Reward weights. Positive terms reward pressure relief; negative terms penalize
# L0 pressure, stalls, excess compaction work, and bad trigger decisions.
REWARD_L0_PRESSURE = _env_float("RL_REWARD_L0_PRESSURE", 0.20)
REWARD_SLOWDOWN_PRESSURE = _env_float("RL_REWARD_SLOWDOWN_PRESSURE", 0.20)
REWARD_STOP_PRESSURE = _env_float("RL_REWARD_STOP_PRESSURE", 0.30)
REWARD_L0_GROWTH = _env_float("RL_REWARD_L0_GROWTH", 0.20)
REWARD_PENDING_PRESSURE = _env_float("RL_REWARD_PENDING_PRESSURE", 0.15)
REWARD_PENDING_GROWTH = _env_float("RL_REWARD_PENDING_GROWTH", 0.15)
REWARD_STALL = _env_float("RL_REWARD_STALL", 0.70)
REWARD_STOP = _env_float("RL_REWARD_STOP", 1.00)
REWARD_COMPACTION_IO = _env_float("RL_REWARD_COMPACTION_IO", 0.15)
REWARD_COMPACTION_EVENT = _env_float("RL_REWARD_COMPACTION_EVENT", 0.05)
REWARD_UNNECESSARY_COMPACTION = _env_float("RL_REWARD_UNNECESSARY_COMPACTION", 0.15)
REWARD_LATE_NO_COMPACTION = _env_float("RL_REWARD_LATE_NO_COMPACTION", 0.30)
REWARD_PRESSURE_RELIEF = _env_float("RL_REWARD_PRESSURE_RELIEF", 0.60)
