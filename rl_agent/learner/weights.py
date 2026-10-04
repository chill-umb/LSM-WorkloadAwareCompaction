"""The weights file the plugin's learned mode reads (controller/mlp.h has the
layout): written to a temporary file in the same directory, synced, and
renamed into place, so the plugin never reads half a file. Pure Python, so
the export and its golden file need no torch."""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field

MAGIC = b"RLCW0001"
AGENT_CODES = {"l0": 0, "interior": 1, "last": 2}
CODE_AGENTS = {v: k for k, v in AGENT_CODES.items()}
N_ACTIONS = 4


def fnv1a64(data: bytes) -> int:
    h = 1469598103934665603
    for byte in data:
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def features_hash(names) -> int:
    return fnv1a64(",".join(names).encode("ascii"))


@dataclass
class ModelWeights:
    agent: str
    level: int            # -1: every level of the agent
    names: list           # the agent's feature names, the plugin's order
    clip: float
    layers: list          # [(rows of weights, biases)], ReLU between
    delta: tuple = None   # (4 rows of n_in, 4 biases) or None

    @property
    def n_in(self) -> int:
        return len(self.names)


@dataclass
class WeightsFile:
    version: int
    models: list = field(default_factory=list)

    def find(self, agent: str, level: int):
        fallback = None
        for m in self.models:
            if m.agent != agent:
                continue
            if m.level == level:
                return m
            if m.level == -1 and fallback is None:
                fallback = m
        return fallback


def _f64s(values) -> bytes:
    values = [float(v) for v in values]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("weights must be finite")
    return struct.pack(f"<{len(values)}d", *values)


def encode(w: WeightsFile) -> bytes:
    if w.version <= 0:
        raise ValueError("version must be positive")
    payload = [struct.pack("<I", len(w.models))]
    for m in w.models:
        payload.append(struct.pack("<IiQId", AGENT_CODES[m.agent], m.level,
                                   features_hash(m.names), m.n_in,
                                   float(m.clip)))
        payload.append(struct.pack("<I", len(m.layers)))
        width = m.n_in
        for rows, biases in m.layers:
            if any(len(r) != width for r in rows) or len(rows) != len(biases):
                raise ValueError("layer shapes do not chain")
            payload.append(struct.pack("<II", len(rows), width))
            payload.append(_f64s(v for r in rows for v in r))
            payload.append(_f64s(biases))
            width = len(rows)
        if width < N_ACTIONS:
            raise ValueError("the last layer needs an output per action")
        if m.delta is None:
            payload.append(struct.pack("<I", 0))
        else:
            rows, biases = m.delta
            payload.append(struct.pack("<I", 1))
            payload.append(_f64s(v for r in rows for v in r))
            payload.append(_f64s(biases))
    body = b"".join(payload)
    return MAGIC + struct.pack("<QQQ", w.version, len(body), fnv1a64(body)) + body


def write_atomic(w: WeightsFile, path: str) -> None:
    data = encode(w)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def decode(data: bytes, names_by_agent: dict) -> WeightsFile:
    """The inverse of encode; `names_by_agent` gives each agent's feature
    names (the hash in the file must match them)."""
    if data[:8] != MAGIC:
        raise ValueError("bad magic")
    version, length, checksum = struct.unpack_from("<QQQ", data, 8)
    body = data[32:]
    if len(body) != length or fnv1a64(body) != checksum:
        raise ValueError("length or checksum mismatch")
    pos = 0

    def take(fmt):
        nonlocal pos
        values = struct.unpack_from(fmt, body, pos)
        pos += struct.calcsize(fmt)
        return values

    (n_models,) = take("<I")
    out = WeightsFile(version=version)
    for _ in range(n_models):
        code, level, fhash, n_in, clip = take("<IiQId")
        agent = CODE_AGENTS[code]
        names = list(names_by_agent[agent])
        if features_hash(names) != fhash or len(names) != n_in:
            raise ValueError(f"the {agent} model has other state inputs")
        (n_layers,) = take("<I")
        layers, width = [], n_in
        for _ in range(n_layers):
            rows_n, cols = take("<II")
            flat = take(f"<{rows_n * cols}d")
            biases = list(take(f"<{rows_n}d"))
            layers.append(([list(flat[r * cols:(r + 1) * cols])
                            for r in range(rows_n)], biases))
            width = rows_n
        (has_delta,) = take("<I")
        delta = None
        if has_delta:
            flat = take(f"<{N_ACTIONS * n_in}d")
            delta = ([list(flat[a * n_in:(a + 1) * n_in])
                      for a in range(N_ACTIONS)], list(take(f"<{N_ACTIONS}d")))
        out.models.append(ModelWeights(agent, level, names, clip, layers, delta))
    if pos != len(body):
        raise ValueError("payload longer than its models")
    return out


def prepare(state, clip: float) -> list[float]:
    """NaN -> 0, then clip, as mlp.cc's Forward does."""
    out = []
    for v in state:
        v = 0.0 if v is None or not math.isfinite(v) else float(v)
        out.append(min(max(v, -clip), clip))
    return out


def forward(m: ModelWeights, state) -> list[float]:
    """f_theta(s) + delta_j(s) per action, in mlp.cc's order of operations."""
    x = prepare(state, m.clip)
    h = x
    for index, (rows, biases) in enumerate(m.layers):
        nxt = []
        for row, bias in zip(rows, biases):
            total = bias
            for w, v in zip(row, h):
                total += w * v
            nxt.append(max(0.0, total) if index + 1 < len(m.layers) else total)
        h = nxt
    f = h[:N_ACTIONS]
    if m.delta is not None:
        rows, biases = m.delta
        for a in range(N_ACTIONS):
            d = biases[a]
            for w, v in zip(rows[a], x):
                d += w * v
            f[a] += d
    return f
