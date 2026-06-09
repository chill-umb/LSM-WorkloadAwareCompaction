import os

SOCKET_PATH = os.environ.get("RL_COMPACTION_SOCKET_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction.sock"))
MODEL_SAVE_PATH = os.environ.get("RL_MODEL_SAVE_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_model.pt"))
METRICS_LOG_PATH = os.environ.get("RL_METRICS_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_metrics.jsonl"))
IO_LOG_PATH = os.environ.get("RL_IO_LOG_PATH", os.path.expanduser("~/lsm_dqn/rl_compaction_io.jsonl"))

STATE_DIM = 6
ACTION_DIM = 3  # 0=do_nothing, 1=compact_now, 2=delay

# DQN hyperparameters
HIDDEN_DIM = 32
LEARNING_RATE = 1e-3
GAMMA = 0.99
BATCH_SIZE = 64
REPLAY_BUFFER_SIZE = 10_000
MIN_REPLAY_SIZE = 64        # start training after this many transitions
TARGET_UPDATE_INTERVAL = 200  # steps between target network syncs

# Exploration
EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY_STEPS = 5_000

# Model persistence
SAVE_INTERVAL = 500  # save model every N steps

# Runtime behavior
ASYNC_TRAINING = True
TRAIN_STEPS_PER_OBSERVATION = 1

# Reward weights (matching equation 2 in the spec)
W1 = 0.30   # penalty for L0 file count increase
W2 = 0.30   # penalty for PCB increase
W3 = 0.30   # penalty for stall
W4 = 0.10   # penalty for doing a compaction (write cost)
W5 = 0.05   # penalty for bytes compacted (write amplification)
W6 = 0.50   # bonus when pressure is relieved

# Emergency safeguard thresholds (must match C++ hard caps)
L0_HARD_CAP = 20
PCB_HARD_CAP_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB
