"""The turn mode's inputs and weights file (controller/turn.h, RLTW0001):
the 35 input names in the plugin's order, the blocks and the VDN views,
the file's encoding, an atomic writer, and a forward pass that follows
turn.cc's TurnScores operation for operation. Pure Python, so the golden
files the plugin's tests read need no torch."""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field

from .weights import features_hash, fnv1a64

MAGIC = b"RLTW0001"
ARCHS = {"factored": 0, "vdn": 1}
ACTIONS = ("hold", "compact", "defer", "expand")
N_ACTIONS = 4
GLOBAL = ("put_share", "get_share", "scan_share", "pending_soft",
          "stall_share", "busy_share")
L0_BLOCK = ("l0_files_k", "l0_anchor", "l0_slow", "l0_flushes_hat",
            "l0_probes_get")
LEVEL_BLOCK = ("score", "anchor", "backlog", "owed", "inflow_hat",
               "probes_get")


def feature_names(acting: int) -> list:
    """TurnFeatureNames: global (6), L0 (5), then levels 1..acting-1 (6)."""
    names = list(GLOBAL) + list(L0_BLOCK)
    for level in range(1, acting):
        names += [f"l{level}_{f}" for f in LEVEL_BLOCK]
    return names


def block(level: int, acting: int):
    """The input indices of a level's block (TurnBlockStart, TurnBlockSize),
    or None for a level without one."""
    if level == 0:
        return list(range(6, 11))
    if 1 <= level < acting:
        start = 11 + 6 * (level - 1)
        return list(range(start, start + 6))
    return None


def vdn_view(level: int, acting: int) -> list:
    """A VDN agent's view: its own block, the block above (none at L0), the
    block below (zeros, -1, where it has none) and the global block: 17
    inputs at L0, 23 at L1, 24 deeper."""
    view = list(block(level, acting))
    if level >= 1:
        view += block(level - 1, acting)
    below = block(level + 1, acting)
    view += below if below is not None else [-1] * len(LEVEL_BLOCK)
    view += list(range(len(GLOBAL)))
    return view


@dataclass
class Net:
    view: list            # input indices, -1 = zero
    layers: list          # [(rows of weights, biases)], ReLU between


@dataclass
class TurnWeights:
    version: int
    arch: int             # 0 factored, 1 vdn
    acting: int
    clip: float
    mean: list
    scale: list
    nets: list = field(default_factory=list)

    @property
    def names(self) -> list:
        return feature_names(self.acting)

    @property
    def n_in(self) -> int:
        return len(self.names)


def _f64s(values) -> bytes:
    values = [float(v) for v in values]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("weights must be finite")
    return struct.pack(f"<{len(values)}d", *values)


def encode(w: TurnWeights) -> bytes:
    if w.version <= 0:
        raise ValueError("version must be positive")
    if w.arch not in (0, 1):
        raise ValueError(f"arch must be 0 or 1, not {w.arch!r}")
    n_in = w.n_in
    if len(w.mean) != n_in or len(w.scale) != n_in:
        raise ValueError("mean and scale need one value per input")
    if not all(s > 0 for s in w.scale) or not w.clip > 0:
        raise ValueError("scale and clip must be positive")
    want_nets = 1 if w.arch == 0 else w.acting
    if len(w.nets) != want_nets:
        raise ValueError(f"arch {w.arch} needs {want_nets} nets, not {len(w.nets)}")
    want_out = 1 + N_ACTIONS * w.acting if w.arch == 0 else N_ACTIONS
    out = [struct.pack("<III", w.arch, w.acting, n_in),
           struct.pack("<Q", features_hash(w.names)),
           _f64s([w.clip]), _f64s(w.mean), _f64s(w.scale),
           struct.pack("<I", len(w.nets))]
    for net in w.nets:
        if not net.view or any(not -1 <= v < n_in for v in net.view):
            raise ValueError("view indices must lie in [-1, n_in)")
        out.append(struct.pack("<I", len(net.view)))
        out.append(struct.pack(f"<{len(net.view)}i", *net.view))
        out.append(struct.pack("<I", len(net.layers)))
        width = len(net.view)
        for rows, biases in net.layers:
            if len(rows) != len(biases) or any(len(r) != width for r in rows):
                raise ValueError("layer shape")
            out.append(struct.pack("<II", len(rows), width))
            out.append(_f64s(v for r in rows for v in r))
            out.append(_f64s(biases))
            width = len(rows)
        if width != want_out:
            raise ValueError(f"the last layer needs {want_out} outputs, not {width}")
    payload = b"".join(out)
    return (MAGIC + struct.pack("<QQQ", w.version, len(payload), fnv1a64(payload))
            + payload)


def decode(data: bytes) -> TurnWeights:
    if len(data) < 32 or data[:8] != MAGIC:
        raise ValueError("bad header")
    version, length, checksum = struct.unpack_from("<QQQ", data, 8)
    payload = data[32:]
    if length != len(payload) or fnv1a64(payload) != checksum:
        raise ValueError("payload length or checksum")
    pos = 0

    def take(fmt):
        nonlocal pos
        values = struct.unpack_from(fmt, payload, pos)
        pos += struct.calcsize(fmt)
        return values

    arch, acting, n_in = take("<III")
    (hash_,) = take("<Q")
    if n_in != len(feature_names(acting)) or hash_ != features_hash(feature_names(acting)):
        raise ValueError("inputs differ from the plugin's")
    (clip,) = take("<d")
    mean = list(take(f"<{n_in}d"))
    scale = list(take(f"<{n_in}d"))
    (n_nets,) = take("<I")
    nets = []
    for _ in range(n_nets):
        (n_view,) = take("<I")
        view = list(take(f"<{n_view}i"))
        (n_layers,) = take("<I")
        layers = []
        for _ in range(n_layers):
            n_out, n_inp = take("<II")
            flat = take(f"<{n_out * n_inp}d")
            rows = [list(flat[r * n_inp:(r + 1) * n_inp]) for r in range(n_out)]
            layers.append((rows, list(take(f"<{n_out}d"))))
        nets.append(Net(view, layers))
    if pos != len(payload):
        raise ValueError("trailing bytes")
    return TurnWeights(version, arch, acting, clip, mean, scale, nets)


def write_atomic(w: TurnWeights, path: str) -> None:
    """Written beside, synced and renamed into place: the plugin never reads
    half a file."""
    data = encode(w)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def forward(w: TurnWeights, x: list) -> list:
    """TurnScores: per acting level, the four scores (the factored arm's
    advantages, or each VDN agent's values), in turn.cc's order of
    operations."""
    xs = []
    for i in range(w.n_in):
        v = x[i] if i < len(x) else 0.0
        v = (v - w.mean[i]) / w.scale[i] if math.isfinite(v) else 0.0
        xs.append(min(w.clip, max(-w.clip, v)))

    def run(net: Net) -> list:
        h = [0.0 if j < 0 else xs[j] for j in net.view]
        for k, (rows, biases) in enumerate(net.layers):
            out = []
            for r, row in enumerate(rows):
                s = biases[r]
                for c, wc in enumerate(row):
                    s += wc * h[c]
                out.append(max(0.0, s) if k + 1 < len(net.layers) else s)
            h = out
        return h

    if w.arch == 0:
        o = run(w.nets[0])
        return [o[1 + N_ACTIONS * i:1 + N_ACTIONS * (i + 1)] for i in range(w.acting)]
    return [run(net) for net in w.nets[:w.acting]]
