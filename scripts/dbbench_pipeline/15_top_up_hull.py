#!/usr/bin/env python3
"""Decide which hull points still need repeats, and record those that cannot be
resolved at any affordable cost.

C-2 asks that each retained hull point's full 95% interval fit inside half the
distance to its nearest hull neighbour. Where the frontier is dense that needs
more repeats than a lease can pay for, and sometimes more than the estimator
searches for. Those pairs are a measured property of the static configuration
class, not an experiment failure, so they are written out rather than chased.

The spend limit is on ADDITIONAL arms per configuration, not on the target: a
point needing 11 repeats that already has 10 costs one run, while one needing
32 with 14 costs eighteen. Every hull point is still carried to the floor of
five repeats, which is the part of C-2 that is always achievable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hull-dir", type=Path, default=Path("gate1"),
                        help="directory holding hull-T<ratio>.json")
    parser.add_argument("--size-ratio", type=int, nargs="+", default=[2, 6, 10])
    parser.add_argument("--floor", type=int, default=5,
                        help="repeats every hull point must reach")
    parser.add_argument("--add-cap", type=int, default=10,
                        help="most additional arms to spend on one configuration")
    parser.add_argument("--seconds-per-arm", type=float, default=174.0)
    parser.add_argument("--output", type=Path,
                        help="default: <hull-dir>/topup.tsv")
    parser.add_argument("--indistinguishable", type=Path,
                        help="default: <hull-dir>/hull_indistinguishable.tsv")
    args = parser.parse_args()

    output = args.output or args.hull_dir / "topup.tsv"
    unresolved_path = (args.indistinguishable or
                       args.hull_dir / "hull_indistinguishable.tsv")

    lines, unresolved, extra_total = [], [], 0
    for ratio in args.size_ratio:
        path = args.hull_dir / f"hull-T{ratio}.json"
        if not path.exists():
            raise SystemExit(f"missing {path}; extract the hull first")
        report = json.loads(path.read_text())
        for name in report["empirical_hull"]:
            entry = report["repeat_top_up"][name]
            target = entry["suggested_total_repeats"]
            have = entry["repeats_present"]
            if entry["width_criterion_passed"] and have >= args.floor:
                continue
            extra = None if target is None else target - have
            if target is None or extra > args.add_cap:
                unresolved.append((ratio, target, have, name))
                target = max(args.floor, have)
            if target <= have:
                continue
            knobs = entry["knobs"]
            lines.append((knobs["size_ratio"],
                          knobs["level0_file_num_compaction_trigger"],
                          knobs["baseline_level_base_scale"], target))
            extra_total += target - have

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(f"{a}\t{b}\t{c}\t{d}\n" for a, b, c, d in lines))
    unresolved_path.write_text(
        "".join(f"{r}\t{t}\t{h}\t{n}\n" for r, t, h, n in unresolved))

    hours = extra_total * args.seconds_per_arm / 3600
    print(f"configurations to top up : {len(lines)}")
    print(f"additional arms          : {extra_total}  (~{hours:.1f} h)")
    print(f"indistinguishable pairs  : {len(unresolved)}")
    print(f"written                  : {output}")
    print(f"                           {unresolved_path}")
    for ratio, target, have, _ in unresolved:
        need = "no n <= 200" if target is None else f"{target}"
        print(f"  T={ratio}: needs {need}, has {have}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
