"""Protocol-v3 candidate-aware online controller.

The controller exposes one global tree transition, one global reward, and a
variable parametric action set. A response contains per-level actions for wire
compatibility, but at most one candidate is compacted per decision.
"""

from __future__ import annotations

import json
import math
import os
import random
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F

import config
from model import ParametricCandidateDQN


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0) -> int:
    """Parse wire identifiers without a float round-trip.

    Snapshot epochs are 64-bit structural hashes and routinely exceed 2^53;
    converting them through float silently changed the epoch in the response,
    causing the C++ client to reject every otherwise-valid v3 decision.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 10)
    except (TypeError, ValueError):
        return default


def _ratio(numerator: float, denominator: float, empty: float = 0.0) -> float:
    return numerator / denominator if denominator > 0.0 else empty


def _clip(value: float, high: float = 1.0) -> float:
    return max(0.0, min(high, value))


def _log_bytes(value: float) -> float:
    # Stable scale from one byte through one TiB; values above remain visible
    # but bounded rather than moving an online normalizer underneath replay.
    return _clip(math.log1p(max(0.0, value)) / math.log1p(1 << 40))


@dataclass
class CandidateSet:
    features: np.ndarray
    mask: np.ndarray
    prior: np.ndarray
    files: np.ndarray
    levels_present: List[int]


@dataclass
class Transition:
    state: np.ndarray
    chosen_level: int
    chosen_action: int
    chosen_candidate: np.ndarray
    chosen_prior: float
    reward: float
    next_state: np.ndarray
    next_candidates: np.ndarray
    next_mask: np.ndarray
    next_prior: np.ndarray
    done: bool
    discount: float


class CandidateReplay:
    def __init__(self, capacity: int):
        self.data = deque(maxlen=capacity)

    def push(self, transition: Transition) -> None:
        self.data.append(transition)

    def sample(self, count: int) -> List[Transition]:
        return random.sample(self.data, count)

    def __len__(self) -> int:
        return len(self.data)


class SLOMask:
    """Deterministic rolling safety mask sourced from baseline_slo.json."""

    OPS = ("get", "scan", "write")

    def __init__(self, path: str = config.BASELINE_SLO_PATH):
        self.manifest: dict = {}
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as source:
                    self.manifest = json.load(source)
            except (OSError, ValueError):
                self.manifest = {}
        self.streaks = {"read": 0, "write": 0}

    def _latency_limit(self, operation: str, percentile: str) -> float:
        limits = self.manifest.get("latency_limits_ns", {})
        entry = limits.get(operation, {}) if isinstance(limits, dict) else {}
        if isinstance(entry, dict):
            return _number(entry.get(percentile), 0.0)
        return 0.0

    @property
    def expected_space(self) -> float:
        return _number(
            self.manifest.get("expected_physical_space_bytes",
                              self.manifest.get("expected_physical_space", 0)),
            0.0,
        )

    def _operation_breach(self, msg: dict, operation: str) -> bool:
        count = _number(msg.get(f"{operation}_latency_count"), 0.0)
        if count < config.SLO_MIN_SAMPLES:
            return False
        for percentile in ("avg", "p95"):
            limit = self._latency_limit(operation, percentile)
            observed = _number(msg.get(f"{operation}_latency_{percentile}_ns"))
            if limit > 0.0 and observed > 1.02 * limit:
                return True
        return False

    def update(self, msg: dict) -> Dict[str, bool]:
        read_now = self._operation_breach(msg, "get") or self._operation_breach(
            msg, "scan")
        write_now = self._operation_breach(msg, "write")
        self.streaks["read"] = self.streaks["read"] + 1 if read_now else 0
        self.streaks["write"] = self.streaks["write"] + 1 if write_now else 0
        physical = _number(msg.get("physical_sst_bytes"))
        expected = self.expected_space
        return {
            "space": expected > 0.0 and physical > 1.02 * expected,
            "read": self.streaks["read"] >= config.SLO_HYSTERESIS_WINDOWS,
            "write": self.streaks["write"] >= config.SLO_HYSTERESIS_WINDOWS,
        }


class CandidateController:
    def __init__(self):
        self.device = torch.device("cpu")
        self.online = ParametricCandidateDQN(
            config.CANDIDATE_STATE_DIM, config.CANDIDATE_FEATURE_DIM,
            config.CANDIDATE_HIDDEN_DIM, config.ML_MAX_LEVELS,
        ).to(self.device)
        self.target = ParametricCandidateDQN(
            config.CANDIDATE_STATE_DIM, config.CANDIDATE_FEATURE_DIM,
            config.CANDIDATE_HIDDEN_DIM, config.ML_MAX_LEVELS,
        ).to(self.device)
        self.target.load_state_dict(self.online.state_dict())
        self.optimizer = torch.optim.Adam(
            self.online.parameters(), lr=config.LEARNING_RATE)
        self.replay = CandidateReplay(config.REPLAY_BUFFER_SIZE)
        self.slo = SLOMask()
        self.step = 0
        self.decision_id = 0
        self.last_loss: Optional[float] = None
        self.last_reward = 0.0
        self.last_diagnostics: dict = {}
        self._previous: Optional[dict] = None
        self._previous_phi: Optional[float] = None
        self._previous_fallback_count = 0

    def save(self, path: str) -> None:
        """Diagnostic end-of-run checkpoint; never loaded by cold experiments."""
        torch.save({"schema_version": 3, "step": self.step,
                    "model": self.online.state_dict()}, path)

    # ------------------------------------------------------------------
    # Encoding and analytic prior
    # ------------------------------------------------------------------
    def _latency_ratio(self, msg: dict, operation: str, percentile: str) -> float:
        limit = self.slo._latency_limit(operation, percentile)
        observed = _number(msg.get(f"{operation}_latency_{percentile}_ns"))
        return _clip(_ratio(observed, limit), 4.0) / 4.0 if limit > 0 else 0.0

    def encode_state(self, msg: dict) -> np.ndarray:
        physical = _number(msg.get("physical_sst_bytes"))
        live = _number(msg.get("live_logical_bytes"))
        logical_write = _number(msg.get("user_logical_write_bytes"))
        compaction_write = _number(msg.get("compaction_bytes_written"))
        flush = _number(msg.get("flushed_bytes"))
        get_count = _number(msg.get("get_latency_count"))
        scan_count = _number(msg.get("scan_latency_count"))
        returned = _number(msg.get("scan_returned_entries"))
        skipped = _number(msg.get("scan_internal_skipped"))
        seeks = _number(msg.get("scan_sorted_run_seeks"))
        dt = _number(msg.get("interval_micros")) / 1e6
        levels = msg.get("levels", []) or []
        runs = sum(_number(level.get("files")) for level in levels)
        nonempty = sum(1 for level in levels if _number(level.get("files")) > 0)
        fullness = []
        for level in levels:
            number = int(_number(level.get("level")))
            if number == 0:
                capacity = max(1.0, _number(msg.get("l0_compaction_trigger"), 4))
                fullness.append(_number(level.get("files")) / capacity)
            else:
                fullness.append(_ratio(_number(level.get("bytes")),
                                       _number(level.get("target_bytes"))))
        l0_files = _number(levels[0].get("files")) if levels else 0.0
        values = [
            _log_bytes(physical), _log_bytes(live),
            _clip(_ratio(physical, live), 4.0) / 4.0,
            _log_bytes(_number(msg.get("pending_compaction_bytes"))),
            _clip(_ratio(l0_files, _number(msg.get("l0_compaction_trigger"), 4))),
            _clip(_ratio(l0_files, _number(msg.get("l0_slowdown_trigger"), 20))),
            _clip(_ratio(l0_files, _number(msg.get("l0_stop_trigger"), 36))),
            _clip(_ratio(flush + compaction_write, logical_write), 10.0) / 10.0,
            _clip(_ratio(_number(msg.get("point_sst_probes")), get_count), 10.0) / 10.0,
            _clip(_ratio(returned + skipped, returned,
                         1.0 if scan_count > 0 else 0.0), 10.0) / 10.0,
            _clip(_ratio(seeks, scan_count), 10.0) / 10.0,
            _clip(_ratio(_number(msg.get("stall_duration_micros")),
                         _number(msg.get("interval_micros")))),
            self._latency_ratio(msg, "get", "avg"),
            self._latency_ratio(msg, "get", "p95"),
            self._latency_ratio(msg, "scan", "avg"),
            self._latency_ratio(msg, "scan", "p95"),
            self._latency_ratio(msg, "write", "avg"),
            self._latency_ratio(msg, "write", "p95"),
            _clip(runs / 64.0), _clip(nonempty / config.ML_MAX_LEVELS),
            _clip(max(fullness, default=0.0), 4.0) / 4.0,
            _log_bytes(compaction_write), _log_bytes(logical_write),
            _clip(math.log1p(dt) / math.log(11.0)),
        ]
        return np.asarray(values, dtype=np.float32)

    @staticmethod
    def _candidate_features(level: dict, candidate: Optional[dict]) -> np.ndarray:
        if candidate is None:
            values = [1.0, _clip(_number(level.get("level")) / 15.0)] + [0.0] * 16
            return np.asarray(values, dtype=np.float32)
        source = max(1.0, _number(candidate.get("source_bytes")))
        entries = _number(candidate.get("num_entries"))
        deletions = _number(candidate.get("num_deletions"))
        values = [
            0.0,
            _clip(_number(candidate.get("source_level")) / 15.0),
            _clip(_number(candidate.get("output_level")) / 15.0),
            _log_bytes(source),
            _log_bytes(_number(candidate.get("expanded_source_bytes"))),
            _log_bytes(_number(candidate.get("overlap_bytes"))),
            _clip(_number(candidate.get("overlap_ratio")), 8.0) / 8.0,
            _log_bytes(_number(candidate.get("estimated_read_bytes"))),
            _log_bytes(_number(candidate.get("estimated_write_bytes"))),
            _clip(_ratio(deletions, entries)),
            _clip(_ratio(_number(candidate.get("compensated_size")), source), 4.0) / 4.0,
            _clip(_number(candidate.get("projected_source_fullness")), 4.0) / 4.0,
            _clip(_number(candidate.get("projected_output_fullness")), 4.0) / 4.0,
            1.0 if candidate.get("empties_source_level") else 0.0,
            _clip(_number(candidate.get("priority_rank")) / 8.0),
            _clip(len(candidate.get("expanded_source_files", []) or []) / 8.0),
            _clip(len(candidate.get("overlap_files", []) or []) / 8.0),
            1.0 if candidate.get("conflict") else 0.0,
        ]
        return np.asarray(values, dtype=np.float32)

    @staticmethod
    def analytic_prior(level: dict, candidate: Optional[dict]) -> float:
        if candidate is None:
            return 0.0
        expanded = max(1.0, _number(candidate.get("expanded_source_bytes")))
        read_bytes = _number(candidate.get("estimated_read_bytes"))
        write_bytes = _number(candidate.get("estimated_write_bytes"))
        entries = _number(candidate.get("num_entries"))
        deletions = _number(candidate.get("num_deletions"))
        garbage = _ratio(deletions, entries)
        run_removal = 1.0 if candidate.get("empties_source_level") else _clip(
            len(candidate.get("expanded_source_files", []) or []) /
            max(1.0, _number(level.get("files"))))
        projected_relief = _clip(
            _number(level.get("score")) -
            _number(candidate.get("projected_source_fullness")), 2.0) / 2.0
        tree_growth = _clip(
            _number(candidate.get("projected_output_fullness")) - 1.0, 2.0) / 2.0
        io_cost = _clip((read_bytes + write_bytes) / (20.0 * expanded), 1.0)
        overlap_cost = _clip(_number(candidate.get("overlap_ratio")) / 10.0)
        priority = 1.0 - _clip(_number(candidate.get("priority_rank")) / 8.0)
        return float(np.clip(
            0.9 * garbage + 0.7 * run_removal + 0.6 * projected_relief +
            0.1 * priority - 0.7 * io_cost - 0.25 * overlap_cost -
            0.5 * tree_growth,
            -2.0, 2.0,
        ))

    def build_candidates(self, msg: dict) -> CandidateSet:
        actions = config.CANDIDATE_MAX_PER_LEVEL + 1
        features = np.zeros((config.ML_MAX_LEVELS, actions,
                             config.CANDIDATE_FEATURE_DIM), dtype=np.float32)
        mask = np.zeros((config.ML_MAX_LEVELS, actions), dtype=np.bool_)
        prior = np.zeros((config.ML_MAX_LEVELS, actions), dtype=np.float32)
        files = np.zeros((config.ML_MAX_LEVELS, actions), dtype=np.uint64)
        levels_present = []
        for level in msg.get("levels", []) or []:
            index = int(_number(level.get("level"), -1))
            if index < 0 or index >= config.ML_MAX_LEVELS:
                continue
            levels_present.append(index)
            features[index, 0] = self._candidate_features(level, None)
            mask[index, 0] = True
            for action, candidate in enumerate(
                    (level.get("candidates", []) or [])[:config.CANDIDATE_MAX_PER_LEVEL],
                    start=1):
                features[index, action] = self._candidate_features(level, candidate)
                files[index, action] = _integer(candidate.get("source_file_number"))
                valid = not bool(candidate.get("conflict")) and files[index, action] != 0
                mask[index, action] = valid
                prior[index, action] = (self.analytic_prior(level, candidate)
                                        if config.ANALYTIC_PRIOR else 0.0)
        return CandidateSet(features, mask, prior, files, levels_present)

    # ------------------------------------------------------------------
    # Global reward and replay validity
    # ------------------------------------------------------------------
    def _tree_cost(self, msg: dict) -> Tuple[float, dict]:
        gets = _number(msg.get("get_latency_count"))
        scans = _number(msg.get("scan_latency_count"))
        returned = _number(msg.get("scan_returned_entries"))
        skipped = _number(msg.get("scan_internal_skipped"))
        physical = _number(msg.get("physical_sst_bytes"))
        live = _number(msg.get("live_logical_bytes"))
        expected = max(1.0, self.slo.expected_space, physical)
        point = _ratio(_number(msg.get("point_sst_probes")), gets)
        scan = _ratio(returned + skipped, returned,
                      1.0 if scans > 0 else 0.0)
        scan_seeks = _ratio(_number(msg.get("scan_sorted_run_seeks")), scans)
        space = _ratio(physical, live)
        pending = _ratio(_number(msg.get("pending_compaction_bytes")), expected)
        stall = _ratio(_number(msg.get("stall_duration_micros")),
                       _number(msg.get("interval_micros")))
        cost = point + scan + 0.25 * scan_seeks + 0.5 * space + pending + 2.0 * stall
        return cost, {"point_probe_cost": point, "scan_work_cost": scan,
                      "scan_seek_cost": scan_seeks, "space_cost": space,
                      "pending_cost": pending, "stall_cost": stall}

    def compute_reward(self, msg: dict) -> Tuple[float, dict]:
        cost, components = self._tree_cost(msg)
        phi = -cost
        dt = max(0.0, _number(msg.get("interval_micros")) / 1e6)
        gamma_dt = (config.GAMMA_PER_SEC ** dt
                    if config.GAMMA_PER_SEC > 0.0 else config.GAMMA)
        logical = _number(msg.get("user_logical_write_bytes"))
        write_amp = _ratio(_number(msg.get("flushed_bytes")) +
                           _number(msg.get("compaction_bytes_written")), logical)
        point = components["point_probe_cost"]
        scan = components["scan_work_cost"]
        latency = 0.0
        for operation in SLOMask.OPS:
            for percentile in ("avg", "p95"):
                limit = self.slo._latency_limit(operation, percentile)
                observed = _number(msg.get(
                    f"{operation}_latency_{percentile}_ns"))
                if limit > 0.0:
                    latency += max(0.0, observed / limit - 1.0)
        if self._previous_phi is None or msg.get("done"):
            reward = 0.0
        else:
            reward = gamma_dt * phi - self._previous_phi - dt * (
                0.25 * write_amp + 0.25 * point + 0.25 * scan + latency)
        self._previous_phi = phi
        components.update({"phi": phi, "gamma_dt": gamma_dt,
                           "write_amp_cost": write_amp,
                           "latency_budget_cost": latency})
        return float(reward), components

    def _previous_transition_valid(self, msg: dict) -> bool:
        if self._previous is None:
            return False
        if not self._previous.get("valid_by_construction", True):
            return False
        fallback = int(_number(msg.get("fallback_count")))
        if fallback != self._previous_fallback_count:
            return False
        if self._previous["action"] == 0:
            return True
        level = self._previous["level"]
        entry = next((item for item in msg.get("levels", []) or []
                      if int(_number(item.get("level"), -1)) == level), None)
        if entry is None:
            return False
        return (
            bool(entry.get("prev_transition_valid", False)) and
            int(_number(entry.get("prev_decision_id"))) ==
            self._previous["decision_id"] and
            int(_number(entry.get("prev_scheduling_result"))) == 1 and
            int(_number(entry.get("prev_override_reason"))) != 4
        )

    # ------------------------------------------------------------------
    # Safety and action selection
    # ------------------------------------------------------------------
    @staticmethod
    def _due_levels(msg: dict) -> List[dict]:
        return [level for level in (msg.get("levels", []) or [])
                if bool(level.get("default_needed"))]

    @staticmethod
    def _candidate_by_file(level: dict, file_number: int) -> Optional[dict]:
        return next((candidate for candidate in level.get("candidates", []) or []
                     if int(_number(candidate.get("source_file_number"))) == file_number),
                    None)

    def _tuned_action(self, msg: dict, candidates: CandidateSet) -> Tuple[int, int]:
        due = sorted(self._due_levels(msg),
                     key=lambda level: _number(level.get("score")), reverse=True)
        if not due:
            return (candidates.levels_present[0] if candidates.levels_present else 0, 0)
        level = int(_number(due[0].get("level")))
        valid = np.flatnonzero(candidates.mask[level, 1:])
        if valid.size:
            action = int(valid[0] + 1)
            return level, action
        # Structured safety fallback: candidate_file_number=0 tells C++ to run
        # the tuned leveled picker for this specific level, never another one.
        return level, -1

    def _apply_safety(self, msg: dict, candidates: CandidateSet,
                      breaches: Dict[str, bool]) -> Tuple[CandidateSet, Optional[Tuple[int, int]], str]:
        mask = candidates.mask.copy()
        forced = None
        reason = "none"
        expected = max(1.0, self.slo.expected_space,
                       _number(msg.get("physical_sst_bytes")))
        io_cap = config.CANDIDATE_IO_CAP_FRACTION * expected
        levels = {int(_number(level.get("level"))): level
                  for level in msg.get("levels", []) or []}
        if breaches["write"]:
            for index, level in levels.items():
                for action in range(1, mask.shape[1]):
                    if not mask[index, action]:
                        continue
                    candidate = self._candidate_by_file(
                        level, int(candidates.files[index, action]))
                    if (not bool(level.get("default_needed")) or candidate is None or
                            _number(candidate.get("estimated_write_bytes")) > io_cap):
                        mask[index, action] = False
        masked = CandidateSet(candidates.features, mask, candidates.prior,
                              candidates.files, candidates.levels_present)
        if breaches["read"] and breaches["write"]:
            return masked, self._tuned_action(msg, masked), "simultaneous"
        due = self._due_levels(msg)
        if breaches["space"]:
            choices = []
            for level in due:
                index = int(_number(level.get("level")))
                for action in np.flatnonzero(mask[index, 1:]) + 1:
                    candidate = self._candidate_by_file(
                        level, int(candidates.files[index, action]))
                    garbage = _ratio(_number(candidate.get("num_deletions")),
                                     _number(candidate.get("num_entries")))
                    efficiency = garbage / max(
                        1.0, _number(candidate.get("estimated_write_bytes")))
                    choices.append((efficiency, index, int(action)))
            if choices:
                _, level, action = max(choices)
                forced, reason = (level, action), "space"
            else:
                forced, reason = self._tuned_action(msg, masked), "space_fallback"
        elif breaches["read"]:
            choices = []
            for level in due:
                index = int(_number(level.get("level")))
                for action in np.flatnonzero(mask[index, 1:]) + 1:
                    candidate = self._candidate_by_file(
                        level, int(candidates.files[index, action]))
                    relief = (1.0 if candidate.get("empties_source_level") else
                              len(candidate.get("expanded_source_files", []) or []))
                    efficiency = relief / max(
                        1.0, _number(candidate.get("estimated_write_bytes")))
                    choices.append((efficiency, index, int(action)))
            if choices:
                _, level, action = max(choices)
                forced, reason = (level, action), "read"
            else:
                forced, reason = self._tuned_action(msg, masked), "read_fallback"
        return masked, forced, reason

    def _select_model_action(self, state: np.ndarray,
                             candidates: CandidateSet) -> Tuple[int, int, dict]:
        with torch.no_grad():
            state_t = torch.from_numpy(state).unsqueeze(0)
            candidate_t = torch.from_numpy(candidates.features).unsqueeze(0)
            residual = self.online(state_t, candidate_t)[0].cpu().numpy()
        q = residual + candidates.prior
        options = []
        if candidates.levels_present:
            first = candidates.levels_present[0]
            options.append((first, 0))  # one global defer, not N duplicates
        for level in candidates.levels_present:
            for action in np.flatnonzero(candidates.mask[level, 1:]) + 1:
                options.append((level, int(action)))
        if not options:
            return 0, 0, {"q": 0.0, "prior": 0.0, "residual": 0.0}
        values = np.asarray([q[level, action] for level, action in options])
        if config.EVAL_MODE:
            selected = int(np.argmax(values))
        elif config.EXPLORATION == "boltzmann":
            progress = min(1.0, self.step / max(1, config.EXPLORATION_DECAY_STEPS))
            temperature = (config.BOLTZMANN_TEMP_START + progress *
                           (config.BOLTZMANN_TEMP_END - config.BOLTZMANN_TEMP_START))
            logits = (values - values.max()) / max(1e-6, temperature)
            probabilities = np.exp(logits)
            probabilities /= probabilities.sum()
            selected = int(np.random.choice(len(options), p=probabilities))
        else:
            epsilon = max(config.EPSILON_END,
                          config.EPSILON_START - self.step /
                          max(1, config.EPSILON_DECAY_STEPS) *
                          (config.EPSILON_START - config.EPSILON_END))
            selected = random.randrange(len(options)) if random.random() < epsilon else int(np.argmax(values))
        level, action = options[selected]
        return level, action, {"q": float(q[level, action]),
                               "prior": float(candidates.prior[level, action]),
                               "residual": float(residual[level, action])}

    # ------------------------------------------------------------------
    # Learning and protocol response
    # ------------------------------------------------------------------
    def _train_once(self) -> None:
        minimum = max(config.CANDIDATE_MIN_REPLAY_SIZE, config.BATCH_SIZE)
        if len(self.replay) < minimum or config.EVAL_MODE:
            return
        batch = self.replay.sample(config.BATCH_SIZE)
        states = torch.from_numpy(np.stack([t.state for t in batch]))
        levels = torch.tensor([t.chosen_level for t in batch], dtype=torch.long)
        actions = torch.tensor([t.chosen_action for t in batch], dtype=torch.long)
        chosen = torch.from_numpy(np.stack([t.chosen_candidate for t in batch]))
        # Score chosen features explicitly, while retaining their original
        # level/action coordinates for the per-level head.
        chosen_grid = torch.zeros(
            (len(batch), config.ML_MAX_LEVELS, 1,
             config.CANDIDATE_FEATURE_DIM), dtype=torch.float32)
        chosen_grid[torch.arange(len(batch)), levels, 0] = chosen
        current = self.online(states, chosen_grid)[
            torch.arange(len(batch)), levels, 0]
        current = current + torch.tensor(
            [t.chosen_prior for t in batch], dtype=torch.float32)
        rewards = torch.tensor([t.reward for t in batch], dtype=torch.float32)
        dones = torch.tensor([t.done for t in batch], dtype=torch.float32)
        discounts = torch.tensor([t.discount for t in batch], dtype=torch.float32)
        next_states = torch.from_numpy(np.stack([t.next_state for t in batch]))
        next_candidates = torch.from_numpy(np.stack([t.next_candidates for t in batch]))
        next_mask = torch.from_numpy(np.stack([t.next_mask for t in batch]))
        next_prior = torch.from_numpy(np.stack([t.next_prior for t in batch]))
        with torch.no_grad():
            online_next = self.online(next_states, next_candidates) + next_prior
            online_next = online_next.masked_fill(~next_mask, -torch.inf)
            flat_action = online_next.flatten(1).argmax(1)
            target_next = self.target(next_states, next_candidates) + next_prior
            bootstrap = target_next.flatten(1).gather(
                1, flat_action[:, None]).squeeze(1)
            target = rewards + discounts * (1.0 - dones) * bootstrap
        loss = F.smooth_l1_loss(current, target)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.online.parameters(), 5.0)
        self.optimizer.step()
        tau = config.TARGET_TAU
        if tau > 0.0:
            with torch.no_grad():
                for target_parameter, parameter in zip(
                        self.target.parameters(), self.online.parameters()):
                    target_parameter.mul_(1.0 - tau).add_(parameter, alpha=tau)
        elif self.step % max(1, config.TARGET_UPDATE_INTERVAL) == 0:
            self.target.load_state_dict(self.online.state_dict())
        self.last_loss = float(loss.item())

    def handle(self, msg: dict) -> dict:
        done = bool(msg.get("done", False))
        state = self.encode_state(msg)
        candidates = self.build_candidates(msg)
        reward, reward_components = self.compute_reward(msg)
        self.last_reward = reward
        valid_previous = self._previous_transition_valid(msg)
        if self._previous is not None and valid_previous:
            previous = self._previous
            self.replay.push(Transition(
                previous["state"], previous["level"], previous["action"],
                previous["candidate"], previous["prior"], reward, state,
                candidates.features, candidates.mask, candidates.prior, done,
                reward_components.get("gamma_dt", config.GAMMA),
            ))
            for _ in range(max(1, config.TRAIN_STEPS_PER_OBSERVATION)):
                self._train_once()

        breaches = self.slo.update(msg)
        if config.SAFETY_MASK:
            masked, forced, safety_reason = self._apply_safety(
                msg, candidates, breaches)
        else:
            masked, forced, safety_reason = candidates, None, "disabled"
        if forced is None:
            level, action, selection = self._select_model_action(state, masked)
        else:
            level, action = forced
            selection = {"q": None, "prior": None, "residual": None}
        self.decision_id += 1
        actions = [0] * len(msg.get("levels", []) or [])
        files = [0] * len(actions)
        level_to_position = {
            int(_number(entry.get("level"))): position
            for position, entry in enumerate(msg.get("levels", []) or [])
        }
        wire_action = action
        if action != 0 and level in level_to_position:
            position = level_to_position[level]
            actions[position] = 1
            if action > 0:
                files[position] = int(masked.files[level, action])
            else:  # level-scoped tuned fallback, no exact candidate available
                files[position] = 0
        chosen_feature = (masked.features[level, action]
                          if action >= 0 else masked.features[level, 0])
        self._previous = {
            "state": state, "level": level,
            "action": max(0, action), "candidate": chosen_feature.copy(),
            "prior": (float(masked.prior[level, action])
                      if action >= 0 else 0.0),
            "decision_id": self.decision_id,
            "valid_by_construction": action >= 0,
        }
        self._previous_fallback_count = int(_number(msg.get("fallback_count")))
        self.step += 1
        self.last_diagnostics = {
            "reward": reward, "reward_components": reward_components,
            "transition_valid": valid_previous, "breaches": breaches,
            "safety_reason": safety_reason, "selected_level": level,
            "selected_action": wire_action, **selection,
            "replay_size": len(self.replay), "loss": self.last_loss,
        }
        return {
            "decision_id": self.decision_id,
            "snapshot_epoch": _integer(msg.get("snapshot_epoch")),
            "actions": actions,
            "candidate_file_numbers": files,
        }
