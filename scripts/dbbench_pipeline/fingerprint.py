#!/usr/bin/env python3
"""Parse 03's experiment fingerprint, the canonical identity of a run.

03 writes the fingerprint and this parser reads it back, so the two must stay
in lockstep: a new segment needs both sides changed, and
tests/test_fingerprint.py checks the pair (CLAUDE.md gotcha). Moved here from
the retired 06_select_baseline_slo.py on 2026-10-02, unchanged.
"""

from __future__ import annotations

import re


def parse_fingerprint_options(fingerprint: str) -> dict[str, object]:
    """Recover every experiment option encoded in the canonical identity."""
    pattern = re.compile(
        r"^([A-Za-z0-9_.-]+):(\d+)M:T(\d+):k(\d+):v(\d+):wb(\d+):"
        r"sst(\d+):block(\d+):l1(\d+):levels(\d+):"
        r"l0-(\d+)-(\d+)-(\d+):pri(\d+):load(\d+):"
        r"mix([0-9.]+)-([0-9.]+)-([0-9.]+):scan(\d+)-(\d+)"
        r"(?::skew(\d+)-([0-9.]+))?(?::pow([0-9.]+)-([0-9.]+))?:"
        r"cache(\d+):bloom(\d+):bg(\d+):threads(\d+):wal([01]):"
        r"dio([01])(?::cap([0-9.x]+))?(?::ltm([0-9.x]+))?"
        r"(?::settle(\d+))?(?::qbar([0-9.]+))?(?::prices([0-9a-f]{64}))?"
        r"(?::plugin([0-9a-f]{64}):pcfg([0-9a-f]{64}))?:"
        r"dynamic([01]):soft(\d+):hard(\d+):"
        r"binary([0-9a-f]{64}):objective([0-9a-f]{64})$"
    )
    match = pattern.fullmatch(fingerprint)
    if not match:
        raise SystemExit(f"malformed experiment fingerprint: {fingerprint}")
    names = (
        "workload_profile", "size_millions", "size_ratio", "key_size", "value_size",
        "write_buffer_size", "target_file_size", "block_size",
        "max_bytes_for_level_base", "num_levels",
        "level0_file_num_compaction_trigger",
        "level0_slowdown_writes_trigger", "level0_stop_writes_trigger",
        "compaction_priority", "load_percent", "mix_get_ratio",
        "mix_put_ratio", "mix_seek_ratio", "scan_length",
        "mix_max_scan_length", "keyrange_num", "value_theta",
        "key_dist_a", "key_dist_b",
        "block_cache_size", "bloom_bits",
        "max_background_jobs", "threads", "disable_wal", "use_direct_io",
        "static_capacity_scales", "level_target_multipliers",
        "settle_hold_seconds", "reference_rate", "prices_sha256",
        "plugin_sha256", "plugin_config_sha256",
        "level_compaction_dynamic_level_bytes",
        "soft_pending_compaction_bytes_limit",
        "hard_pending_compaction_bytes_limit",
        "dbbench_sha256", "research_objective_sha256",
    )
    OPAQUE = ("workload_profile", "static_capacity_scales",
              "level_target_multipliers", "dbbench_sha256",
              "research_objective_sha256", "prices_sha256", "plugin_sha256",
              "plugin_config_sha256")
    # Programme 1 segments (D-13); absent on runs that predate them or do
    # not use them: the power-law family's key fit, the settle hold, q-bar,
    # the prices file, and a controller arm's plugin and its config.
    OPTIONAL = {"key_dist_a": float, "key_dist_b": float,
                "settle_hold_seconds": int, "reference_rate": float,
                "prices_sha256": str, "plugin_sha256": str,
                "plugin_config_sha256": str}
    # Absent means the run predates the B1 skew knob, which is the uniform
    # family at keyrange_num 1 and a fixed value size.
    SKEW_DEFAULTS = {"keyrange_num": "1", "value_theta": None}
    values: list[object] = list(match.groups())
    for index, name in enumerate(names):
        if name in ("static_capacity_scales", "level_target_multipliers"):
            # Absent means no scaling (or a run predating the knob).
            values[index] = values[index] or "off"
            continue
        if name in SKEW_DEFAULTS:
            raw = values[index] or SKEW_DEFAULTS[name]
            values[index] = None if raw is None else (
                int(raw) if name == "keyrange_num" else float(raw))
            continue
        if name in OPTIONAL:
            values[index] = (None if values[index] is None
                             else OPTIONAL[name](values[index]))
            continue
        if name in OPAQUE:
            continue
        if name in ("mix_get_ratio", "mix_put_ratio", "mix_seek_ratio"):
            values[index] = float(values[index])
        else:
            values[index] = int(values[index])
    return dict(zip(names, values))
