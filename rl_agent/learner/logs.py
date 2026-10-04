"""The plugin's logs (controller/log.h, schema 4): constants, a reader that
tails a growing file, and the shapes the learner reads."""

from __future__ import annotations

import json
import math
import os
from typing import Optional

SCHEMA = 4
ACTIONS = ("hold", "compact", "defer", "expand")
AGENTS = ("l0", "interior", "last")
# cost_model_v2's step types and kinds, in the plugin's order.
STEP_TYPES = ("probe", "block", "seek", "reopen", "step", "iblock",
              "memtable", "get0", "scan0", "put")
KINDS = ("flush", "l0", "deep", "move")
COST_KEYS = (
    "write_bytes", "probes", "fp_reads", "seeks", "hit_reads", "reopens",
    "slot_out_probes", "slot_out_fp_reads", "slot_out_seeks",
    "slot_out_reopens", "slot_in_probes", "slot_in_fp_reads",
    "slot_in_seeks", "slot_in_reopens", "ops", "gets", "scans", "writes",
    "user_bytes", "busy_ops", "inflow_bytes", "wait_ops", "waits",
    "held_byte_ops", "jobs_flush", "jobs_l0", "jobs_deep", "jobs_move",
    "read_bytes", "hidden_steps", "slot_out_hidden", "slot_in_hidden",
    "fg_probes", "fg_block_probes", "fg_run_seeks", "fg_reopens",
    "fg_nexts_found", "fg_iter_skips", "memtable_hidden", "k0_ops",
    "l0_probes", "l0_block_probes", "l0_seeks", "l0_reopens", "l0_hidden")


def model_key(agent: str, level: int) -> str:
    """One model per agent: L0, each interior level (D-19: no pool), and the
    last level."""
    if agent == "interior":
        return f"interior-{level}"
    if agent in ("l0", "last"):
        return agent
    raise ValueError(f"unknown agent {agent!r}")


def key_agent_level(key: str) -> tuple[str, int]:
    """(agent, level) as the weights file names them; -1: every level."""
    if key.startswith("interior-"):
        return "interior", int(key.split("-", 1)[1])
    return key, (0 if key == "l0" else -1)


def vector(state: dict) -> list[float]:
    """A state's values in the plugin's order; null (not measured) is NaN."""
    return [math.nan if v is None else float(v) for v in state.values()]


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and \
        math.isfinite(x)


class LogTail:
    """New complete lines of a file that another process appends to. A
    partial last line is kept until its newline arrives; a missing file
    reads as empty."""

    def __init__(self, path: str):
        self.path = path
        self.offset = 0
        self.partial = b""

    def read(self) -> list[dict]:
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
        except FileNotFoundError:
            return []
        self.offset += len(data)
        data = self.partial + data
        lines = data.split(b"\n")
        self.partial = lines.pop()
        out = []
        for line in lines:
            if line.strip():
                out.append(json.loads(line))
        return out


def read_all(path: str) -> list[dict]:
    with open(path, encoding="ascii") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None
