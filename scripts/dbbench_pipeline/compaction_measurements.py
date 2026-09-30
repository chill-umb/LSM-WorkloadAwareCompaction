"""Per-source-level compaction measurements from the host log's job_end
records (plan §5; PATHWAYS §1.1, B §2 item 4, Gate N0 item 3).

For source level i, over successful jobs:
  merges (trivial moves excluded, whose output equals input):
    rho_i = sum(X - O) / sum(S)   pass-through
    o_i   = sum(O) / sum(S)       overlap ratio
    eta_i = sum(X) / sum(S + O)   the 2026-09-11 merge survival
    dropped bytes = sum(S + O - X)
  every job to a lower level, trivial moves included:
    leaving bytes = merge S + trivially moved bytes (a_i times user bytes)
    xi_i = trivially moved bytes / leaving bytes
    rho_tilde_i = xi_i + (1 - xi_i) rho_i
  and, per user byte written in the view, a_i and t_i.

Views: "measured" (jobs ending between the measure_start and drain_end
stamps, as 04 windows the measured phase) and "whole_run".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import host_log


def _level_view(jobs: list[dict], num_levels: int,
                user_bytes: float | None) -> dict:
    levels = {}
    for level in range(num_levels):
        # A job within one level (intra-L0, a last-level self-compaction)
        # moves nothing down; Lemma D.7 lists it apart.
        within = [j for j in jobs if j["start_level"] == level == j["output_level"]]
        mine = [j for j in jobs
                if j["start_level"] == level != j["output_level"]]
        merges = [j for j in mine if not j["trivial"]]
        s = sum(j["s"] for j in merges)
        o = sum(j["o"] for j in merges)
        x = sum(j["x"] for j in merges)
        moved = sum(j["s"] for j in mine if j["trivial"])
        leaving = s + moved
        rho = (x - o) / s if s else None
        xi = moved / leaving if leaving else None
        # xi < 1 means some merge bytes, so rho is defined; all-trivial
        # leaves rho undefined and rho_tilde = xi = 1.
        rho_tilde = None if xi is None else 1.0 if xi == 1 else xi + (1 - xi) * rho
        levels[str(level)] = {
            "merge_jobs": len(merges), "trivial_jobs": len(mine) - len(merges),
            "s_bytes": s, "o_bytes": o, "x_bytes": x,
            "dropped_bytes": s + o - x, "trivially_moved_bytes": moved,
            "leaving_bytes": leaving,
            "rho": rho, "o": o / s if s else None,
            "eta": x / (s + o) if s + o else None,
            "xi": xi, "rho_tilde": rho_tilde,
            "a": leaving / user_bytes if user_bytes else None,
            "t": moved / user_bytes if user_bytes else None,
            "within_level_jobs": len(within),
            "within_level_x_bytes": sum(j["x"] for j in within),
        }
    return {"user_bytes": user_bytes, "levels": levels}


def analyze(records: list[dict], num_levels: int) -> dict:
    """The measurement report; `complete` is False when a job began but did
    not end, a job failed, or a job's source level lies outside the tree."""
    stamps = host_log.stamp_index(records)
    begun = {r["job"] for r in records if r.get("type") == "job_begin"}
    ends = [(i, r) for i, r in enumerate(records) if r.get("type") == "job_end"]
    failed = sorted(r["job"] for _, r in ends if r["ok"] != 1)
    outside = sorted(r["job"] for _, r in ends
                     if not 0 <= r["start_level"] < num_levels)
    ok = [(i, r) for i, r in ends
          if r["ok"] == 1 and 0 <= r["start_level"] < num_levels]
    views = {"whole_run": _level_view([r for _, r in ok], num_levels, None)}
    if "measure_start" in stamps and "drain_end" in stamps:
        lo, hi = stamps["measure_start"], stamps["drain_end"]
        user = (records[hi]["tickers"]["rocksdb.bytes.written"] -
                records[lo]["tickers"]["rocksdb.bytes.written"])
        views["measured"] = _level_view(
            [r for i, r in ok if lo < i < hi], num_levels, user or None)
    unfinished = sorted(begun - {r["job"] for _, r in ends})
    return {"schema_version": 2, "source": "host_log job_end records",
            "num_levels": num_levels, "views": views,
            "failed_jobs": failed, "unfinished_jobs": unfinished,
            "jobs_outside_tree": outside,
            "complete": bool(ends) and not (failed or unfinished or outside)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host_log", type=Path)
    parser.add_argument("--num-levels", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(host_log.load(args.host_log), args.num_levels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
