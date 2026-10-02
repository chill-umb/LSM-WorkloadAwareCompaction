#!/usr/bin/env python3
"""Support for D-17: what a two-stage screen of Gate N2's static class
would cost, and how often it would drop a configuration that is truly on the
lower convex hull (PATHWAYS C §1-§4; PREREGISTRATION D-13 §4-5).

Reads, per workload, 04's summary.csv of the q-bar arms (five native arms
of one configuration at T=10, D-14 §2), the prices file of stage 18, and
q-bar (the contract's, or --qbar). Reports, per workload:

 1. Run-to-run spread of that one configuration: mean, SD and relative SD
    (SD / mean) of the raw counts, of C_W, C_R, C_S, and of J_beta for every
    mode, beta* and c_s scale the contract reports, each SD with its 95%
    chi-square interval (five runs give 4 degrees of freedom: about x0.60 to
    x2.87). Per J, hull_shift_ratio = sum_c beta_c sd(C_c) / sd(J), an
    estimate of how much more the hull rule below lowers J than its shadow
    does: it uses one configuration's unpaired SDs, while the rule uses the
    screen's pooled paired SDs.
 2. By Monte Carlo, the probability that the screen drops a configuration,
    per design and per gap, in units of sigma_d (the SD of one seed-paired
    difference between two configurations). The gap is taken at a weight
    beta > 0 at which theta is best, not necessarily a contract mode. A
    positive gap is a true hull point that far below its nearest rivals
    there (dropping it leaves a hole in the hull); a negative gap is a point
    that far above them (dropping it is the screen's saving). Each cell has
    its own seed, derived from --seed and its parameters, and its Monte
    Carlo SE; a cell within 2 SE of --risk or --power is flagged. Read off
    it, per n_s: the smallest safe gap (p_drop <= --risk against the most
    --rivals) and the distance beyond which a point is dropped (p_drop >=
    --power against the fewest), both also as a share of each J (and in
    USD) through the measured spread, at its point estimate and at the
    upper end of its 95% interval. The hole-side figures are upper bounds
    for the hull rule; the drop distance is a best case for it (below).
 3. Runs and node-hours per design, from Theta_s's admissible grid
    (A-Impl-6) and the q-bar arms' own run time, which is at T=10.

The drop rule simulated (D-17's candidate; k is --threshold). After n_s
seed-paired runs of every configuration of one (workload, T) cell, let
x(theta) be theta's mean (C_W, C_R, C_S) and s_c the pooled SD of one paired
difference in component c: sqrt 2 times the root mean square residual of the
cell's two-way (configuration x seed) layout, with (M-1)(n_s-1) degrees of
freedom for M configurations. The layout is balanced: every configuration
enters with seeds 1..n_s only, including the native arms that run all five
seeds before the rest (unless --screen-native). theta is dropped iff the
point x(theta) - k s / sqrt(n_s), every component lowered, is still off the
lower convex hull (frontier_analysis.lower_hull) of that point and the
other configurations' means. Everything else is topped up to --full-runs.

What is simulated is the rule's one-dimensional shadow at a weight where
theta is best: dropped iff J(theta) - min over rivals J > k s_J / sqrt(n_s),
with s_J the pooled paired SD of that J. Lowering each component by k s_c
lowers J by sum_c beta_c k s_c >= k s_J (the SD of a sum is at most the sum
of the SDs), so a point the hull rule drops is also dropped by the shadow:
the hole probabilities are upper bounds for the hull rule. For the same
reason the drop distance is a best case (a lower bound on the distance) for
the hull rule, twice over: it lowers J by hull_shift_ratio times more, and
it keeps a point that is best at any weight, not only at one.

Assumptions, each a flag:
  --pair-correlation rho  five runs of ONE configuration measure its own
      run-to-run SD sigma, not the SD sigma_d of a paired difference between
      two configurations; sigma_d = sigma sqrt(2(1 - rho)) with rho the
      correlation of two configurations' costs on the same seed. Nothing
      measured tonight gives rho; 0 (no benefit from pairing) is the
      cautious default. The drop probabilities in sigma_d units do not
      depend on rho; the safe gap as a share of J does.
  --noise-scaling  relative: sigma is a fixed share of J in every
      configuration, so the safe gap as a share of J carries over to other
      configurations; absolute: sigma is the same in USD everywhere, so the
      safe gap in USD carries over.
  --rivals  how many configurations sit at the same gap; those farther
      away do not matter. More near-ties make a drop likelier, so the
      bound holds for a cell with at most that many near its hull point.
  --sigma  pooled: the rule estimates sigma_d from the screen as above;
      known: it uses the true sigma_d (optimistic).
  --hull-vertices  vertices per cell for the chance of any hole (4, C-1's
      minimum, and 8 by default; a 3-D hull of 32 points may have more);
      vertices, cells and workloads are taken as independent, each at the
      same gap.
Not modelled: C §3's top-ups beyond --full-runs (they follow the hull
points, which every design keeps) and differences in run time across T and
configurations (the q-bar arms run at T=10;
--seconds-per-run overrides their mean).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from pathlib import Path

import research_objective

MIB = 1 << 20
# priced_costs' arguments, in order (table_reopens since D-20).
COUNTS = ("sst_bytes_written", "filter_probes", "block_reading_probes",
          "run_seeks", "table_reopens", "held_byte_operations")
COSTS = ("C_W", "C_R", "C_S")


def number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def chi2_cdf(x: float, df: float) -> float:
    """The chi-square CDF: the regularised lower gamma P(df/2, x/2), by its
    power series (converges for every x)."""
    a, y = df / 2, x / 2
    if y <= 0:
        return 0.0
    term = total = 1.0 / a
    n = 0
    while term > total * 1e-16:
        n += 1
        term *= y / (a + n)
        total += term
    return min(1.0, total * math.exp(a * math.log(y) - y - math.lgamma(a)))


def chi2_quantile(p: float, df: float) -> float:
    lo, hi = 0.0, df + 20 * math.sqrt(2 * df) + 20
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if chi2_cdf(mid, df) < p else (lo, mid)
    return (lo + hi) / 2


def spread(values: list[float]) -> dict:
    """Mean, SD and relative SD, with the SD's 95% chi-square interval."""
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    df = len(values) - 1
    ci = [sd * math.sqrt(df / chi2_quantile(q, df)) for q in (0.975, 0.025)]
    return {"mean": mean, "sd": sd, "relative_sd": sd / mean if mean else math.nan,
            "sd_ci95": ci,
            "relative_sd_ci95": [c / mean if mean else math.nan for c in ci]}


def workload_noise(rows: list[dict], contract: dict, prices, qbar) -> dict:
    """Run-to-run spread of one configuration's counts, costs and every J."""
    if len(rows) < 3:
        raise ValueError(f"{len(rows)} runs; the spread needs at least 3")
    if len({row.get("experiment_fingerprint") for row in rows}) != 1:
        raise ValueError("the runs are not of one configuration")
    counts = {c: [number(row.get(c)) for row in rows] for c in COUNTS}
    bad = [c for c, v in counts.items() if not all(math.isfinite(x) for x in v)]
    if bad:
        raise ValueError(f"missing counts: {bad}")
    out = {"counts": {c: spread(v) for c, v in counts.items()}}
    if prices is None:
        return out
    costs = [research_objective.priced_costs(prices, qbar, *run)
             for run in zip(*(counts[c] for c in COUNTS))]
    out["costs"] = {name: spread([run[k] for run in costs])
                    for k, name in enumerate(COSTS)}
    out["J"] = {}
    sds = [out["costs"][name]["sd"] for name in COSTS]
    for mode, beta, scale in research_objective.objective_grid(contract):
        weights = research_objective.mode_weights(contract, mode, beta)
        s = spread([research_objective.j_beta(run, weights, scale) for run in costs])
        # An estimate: one configuration's unpaired SDs stand in for the
        # rule's pooled paired ones.
        s["hull_shift_ratio"] = (research_objective.j_beta(sds, weights, scale) /
                                 s["sd"] if s["sd"] else math.nan)
        out["J"][research_objective.j_column(mode, beta, scale)] = s
    return out


def drop_probability(gap: float, rivals: int, runs: int, threshold: float,
                     df, trials: int, rng: random.Random) -> float:
    """P(the screen drops theta), in units of sigma_d: theta's true J is 0,
    its rivals' is `gap`. Each configuration's n_s-run mean errs by
    N(0, sigma_e^2 / n_s), sigma_e = sigma_d / sqrt 2 (a seed's shared part
    cancels in a paired difference). df None: the rule knows sigma_d;
    otherwise it estimates it with df degrees of freedom."""
    sd_mean = math.sqrt(0.5 / runs)
    drops = 0
    for _ in range(trials):
        mine = rng.gauss(0.0, sd_mean)
        best = min(gap + rng.gauss(0.0, sd_mean) for _ in range(rivals))
        sigma = (1.0 if df is None else
                 math.sqrt(2.0 * rng.gammavariate(df / 2.0, 1.0) / df))
        drops += mine - best > threshold * sigma / math.sqrt(runs)
    return drops / trials


def admissible_points(static_class: dict, flush_bytes: float,
                      slowdown_trigger: int) -> list[tuple[int, int]]:
    """(K0, base MiB) of Theta_s: K0 in [2, K_cap], K_cap =
    min(floor(C_1 / F), K_slow - 1) (A-Impl-6)."""
    points = []
    for base in static_class["base_size_mib"]:
        cap = min(math.floor(base * MIB / flush_bytes), slowdown_trigger - 1)
        points += [(k0, base) for k0 in static_class["l0_trigger"] if 2 <= k0 <= cap]
    return points


def design_runs(configs: int, natives: int, cross_t: int, full: int,
                screen=None, survivors: float = 0.0,
                screen_native: bool = False) -> float:
    """Expected runs of one workload. No screen: every configuration and
    the cross-T re-runs get `full`. A screen of `screen` runs: the screened
    configurations get `screen`, the surviving share `survivors` of them is
    topped up to `full`; the cross-T re-runs (chosen after T=10 is scored)
    and, unless screen_native, the native arms (D-14 §3 computes two
    profiles from them) get `full`."""
    if screen is None:
        return float((configs + cross_t) * full)
    kept = 0 if screen_native else natives
    screened = configs - kept
    return (screened * screen + survivors * screened * (full - screen) +
            (kept + cross_t) * full)


def boundary(entries: list[dict], side: int, level: float):
    """Walking the gap grid inward from its far end on one side, the last
    |gap| up to which every gap passes, and whether Monte Carlo noise could
    move it: any cell walked, the first failing one included, within 2 SE
    of the level. side +1 (true hull points, pass is p_drop <= level): the
    smallest safe gap. side -1 (points above their rivals, pass is
    p_drop >= level): the distance beyond which the screen drops. None if
    even the farthest gap fails."""
    found, near = None, False
    for entry in sorted((e for e in entries if side * e["gap_sd"] >= 0),
                        key=lambda e: -side * e["gap_sd"]):
        p = entry["p_drop"]
        near = near or abs(p - level) < 2 * entry["se"]
        if (p > level) if side > 0 else (p < level):
            break
        found = abs(entry["gap_sd"])
    return found, near


def pairs(items, cast=float) -> dict:
    out = {}
    for item in items or ():
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"give WORKLOAD=VALUE, not {item!r}")
        out[key] = cast(value)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("summary", type=Path, nargs="+",
                        help="04's summary.csv of one workload's q-bar arms")
    parser.add_argument("--prices", type=Path,
                        help="stage 18's prices.json; without it, counts only")
    parser.add_argument("--qbar", action="append", metavar="WORKLOAD=OPS",
                        help="q-bar while the contract has none")
    parser.add_argument("--seconds-per-run", action="append",
                        metavar="WORKLOAD=S", help="default: the q-bar arms' "
                        "mean elapsed_seconds")
    parser.add_argument("--threshold", type=float, default=3.0,
                        help="k of the drop rule")
    parser.add_argument("--screen-runs", type=int, nargs="+", default=[2, 3])
    parser.add_argument("--full-runs", type=int, default=5,
                        help="D-13 §5's first repeats")
    parser.add_argument("--pair-correlation", type=float, default=0.0)
    parser.add_argument("--noise-scaling", choices=("relative", "absolute"),
                        default="relative")
    parser.add_argument("--sigma", choices=("pooled", "known"), default="pooled")
    parser.add_argument("--rivals", type=int, nargs="+", default=[1, 3])
    parser.add_argument("--gaps", type=float, nargs="+",
                        default=[-5.0, -4.0, -3.5, -3.0, -2.5, -2.0, -1.5, -1.0,
                                 -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0])
    parser.add_argument("--risk", type=float, default=0.01,
                        help="drop probability at which a gap counts as safe")
    parser.add_argument("--power", type=float, default=0.9,
                        help="drop probability at which a point above its "
                             "rivals counts as dropped")
    parser.add_argument("--hull-vertices", type=int, nargs="+", default=[4, 8],
                        help="per cell; 4 is C-1's minimum")
    parser.add_argument("--survivor-fractions", type=float, nargs="+",
                        default=[0.1, 0.25, 0.5, 1.0])
    parser.add_argument("--flush-mib", type=float, default=2.0,
                        help="F, the mean flush file size (write buffer 2 MiB; "
                             "23_static_profiles measures it)")
    parser.add_argument("--slowdown-trigger", type=int, default=20,
                        help="K_slow (config.sh L0_SLOWDOWN_TRIGGER)")
    parser.add_argument("--cross-t-configs", type=int, default=4,
                        help="theta* per mode re-run at each cross-T ratio")
    parser.add_argument("--screen-native", action="store_true",
                        help="screen the native arms too")
    parser.add_argument("--trials", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args(argv)
    if not 0 <= args.pair_correlation < 1:
        raise SystemExit("--pair-correlation must be in [0, 1)")
    if any(not 1 < n < args.full_runs for n in args.screen_runs):
        raise SystemExit("every --screen-runs must be between 2 and --full-runs - 1")

    contract, _ = research_objective.load_contract()
    prices = (research_objective.checked_prices(
        json.loads(args.prices.read_text()), contract) if args.prices else None)
    qbars, seconds = pairs(args.qbar), pairs(args.seconds_per_run)
    static = contract["static_class"]
    points = admissible_points(static, args.flush_mib * MIB, args.slowdown_trigger)
    per_cell = len(points) * len(static["profiles"])
    cells = len(static["size_ratios"])
    cross_t = len(static["cross_t_check"]["size_ratios"]) * args.cross_t_configs
    report = {"assumptions": vars(args),
              "admissible_points": points, "configurations_per_cell": per_cell,
              "workloads": {}}

    # 1 and 3: per workload.
    for path in args.summary:
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        families = {row.get("workload_family") for row in rows}
        if len(families) != 1:
            raise SystemExit(f"{path}: not one workload family: {families}")
        family = families.pop()
        qbar = research_objective.reference_rate(contract, family)
        if family in qbars and qbar is not None and qbars[family] != qbar:
            raise SystemExit(f"{family}: --qbar differs from the contract's {qbar}")
        qbar = qbars.get(family, qbar)
        if prices is not None and not (qbar and qbar > 0):
            raise SystemExit(f"{family}: no q-bar; pass --qbar {family}=OPS")
        try:
            noise = workload_noise(rows, contract, prices, qbar)
        except ValueError as error:
            raise SystemExit(f"{path}: {error}") from None
        run_s = seconds.get(family, statistics.fmean(
            number(row.get("elapsed_seconds")) for row in rows))
        configs, natives = cells * per_cell, cells * len(points)
        designs = {"current": design_runs(configs, natives, cross_t, args.full_runs)}
        for n in args.screen_runs:
            for f in args.survivor_fractions:
                designs[f"screen{n}_survivors{f:g}"] = design_runs(
                    configs, natives, cross_t, args.full_runs, n, f,
                    args.screen_native)
        report["workloads"][family] = {
            "summary": str(path), "runs_measured": len(rows), "qbar": qbar,
            "seconds_per_run": run_s, "noise": noise,
            "configurations": configs, "native_arms": natives,
            "cross_t_runs_of": cross_t,
            "designs": {name: {"runs": runs, "node_hours": runs * run_s / 3600}
                        for name, runs in designs.items()}}

    # 2: the Monte Carlo, in units of sigma_d; the same for every workload.
    carlo = []
    for n in args.screen_runs:
        df = None if args.sigma == "known" else (per_cell - 1) * (n - 1)
        for rivals in args.rivals:
            for gap in args.gaps:
                # One stream per cell, so no cell depends on which others ran.
                rng = random.Random(f"{args.seed}:{n}:{rivals}:{float(gap)!r}:{df}:"
                                    f"{args.threshold!r}")
                p = drop_probability(gap, rivals, n, args.threshold, df,
                                     args.trials, rng)
                se = math.sqrt(p * (1 - p) / args.trials)
                level = args.risk if gap >= 0 else args.power
                carlo.append({
                    "screen_runs": n, "rivals": rivals, "gap_sd": gap,
                    "p_drop": p, "se": se, "near_threshold": abs(p - level) < 2 * se,
                    "p_any_hole": None if gap < 0 else {
                        str(v): {"cell": 1 - (1 - p) ** v,
                                 "workload": 1 - (1 - p) ** (v * cells)}
                        for v in args.hull_vertices}})
    report["monte_carlo"] = carlo
    # Each read on its cautious side: holes against the most rivals, the
    # saving against the fewest.
    gaps = {}
    for n in args.screen_runs:
        def grid(rivals):
            return [c for c in carlo
                    if c["screen_runs"] == n and c["rivals"] == rivals]
        safe, safe_near = boundary(grid(max(args.rivals)), +1, args.risk)
        beyond, beyond_near = boundary(grid(min(args.rivals)), -1, args.power)
        gaps[n] = {"safe": safe, "safe_uncertain": safe_near,
                   "dropped_beyond": beyond, "dropped_beyond_uncertain": beyond_near}
    report["gaps_sd"] = gaps
    pairing = math.sqrt(2 * (1 - args.pair_correlation))
    for entry in report["workloads"].values():
        entry["gaps"] = {}
        for column, s in entry["noise"].get("J", {}).items():
            upper = s["sd_ci95"][1] / s["sd"] if s["sd"] else math.nan
            entry["gaps"][column] = {n: {
                name: None if gap is None else {
                    "usd": gap * s["sd"] * pairing,
                    "share_of_J": gap * s["relative_sd"] * pairing,
                    "usd_upper": gap * s["sd"] * pairing * upper,
                    "share_of_J_upper": gap * s["relative_sd"] * pairing * upper}
                for name, gap in by_n.items() if not name.endswith("_uncertain")}
                for n, by_n in gaps.items()}

    print_report(report, args)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return 0


def print_report(report: dict, args) -> None:
    print(f"Theta_s admissible (K0, base MiB) at F = {args.flush_mib} MiB: "
          f"{report['admissible_points']}; {report['configurations_per_cell']} "
          "configurations per (workload, T)")
    for family, w in report["workloads"].items():
        print(f"\n== {family}: {w['runs_measured']} runs of one configuration, "
              f"q-bar {w['qbar']}, {w['seconds_per_run']:.0f} s per run")
        for group in ("counts", "costs", "J"):
            for name, s in w["noise"].get(group, {}).items():
                if group == "J" and not name.endswith("_cs1"):
                    continue
                low, high = s["relative_sd_ci95"]
                shift = (f"  hull shift x{s['hull_shift_ratio']:.2f}"
                         if "hull_shift_ratio" in s else "")
                print(f"  {name:<28} mean {s['mean']:.4g}  SD {s['sd']:.3g}  "
                      f"relative SD {s['relative_sd']:.2%} "
                      f"(95%: {low:.2%}-{high:.2%}){shift}")
        for name, d in w["designs"].items():
            print(f"  design {name:<26} {d['runs']:7.1f} runs "
                  f"{d['node_hours']:7.1f} node-hours")
        print("  node-hours use the q-bar arms' T=10 run time (T=2 runs are "
              "likely slower) and leave out C §3's top-ups beyond five")
    print(f"\n== drop probability, gap in sigma_d units at a weight where the "
          f"point is best (k = {args.threshold}, sigma {args.sigma}; positive "
          "gap = true hull point; any hole per cell / per workload at V "
          "vertices, V = 4 being C-1's minimum; * = within 2 Monte Carlo SE "
          "of --risk or --power)")
    print("  n_s rivals    gap   p_drop      SE  " + "  ".join(
        f"V={v}: cell  workload" for v in args.hull_vertices))
    for c in report["monte_carlo"]:
        holes = "" if c["p_any_hole"] is None else "  ".join(
            f"{h['cell']:12.4f} {h['workload']:9.4f}" for h in c["p_any_hole"].values())
        print(f"  {c['screen_runs']:3d} {c['rivals']:6d} {c['gap_sd']:6.2f} "
              f"{c['p_drop']:8.4f} {c['se']:7.4f}{'*' if c['near_threshold'] else ' '} "
              f"{holes}")
    unit = "share_of_J" if args.noise_scaling == "relative" else "usd"

    def show(g):
        if g is None:
            return "off the grid"
        if unit == "share_of_J":
            return f"{g[unit]:.2%} ({g[unit + '_upper']:.2%})"
        return f"{g[unit]:.3g} USD ({g[unit + '_upper']:.3g})"

    print(f"\n== gaps, rho = {args.pair_correlation}: safe = a hull point this "
          f"far below its {max(args.rivals)} rivals is dropped with "
          f"p <= {args.risk}; dropped beyond = a point this far above its "
          f"{min(args.rivals)} rival(s) is dropped with p >= {args.power}")
    print("  dropped beyond is a best case for the hull rule: it lowers J by "
          "about hull_shift_ratio (an estimate) times more than this shadow, "
          "and keeps a point that "
          "is best at any weight")
    for n, g in report["gaps_sd"].items():
        flags = {name: " (Monte Carlo-sensitive)" if g[name + "_uncertain"] else ""
                 for name in ("safe", "dropped_beyond")}
        print(f"  n_s = {n}: safe {g['safe']} sigma_d{flags['safe']}, dropped "
              f"beyond {g['dropped_beyond']} sigma_d{flags['dropped_beyond']}")
    print(f"  in {unit} (c_s scale 1; every scale is in --json); in brackets, "
          "at the upper end of sigma's 95% interval:")
    for family, w in report["workloads"].items():
        for column, by_n in w["gaps"].items():
            if column.endswith("_cs1"):
                print(f"  {family:<15} {column:<16} " + "; ".join(
                    f"n_s={n}: safe {show(g['safe'])}, dropped beyond "
                    f"{show(g['dropped_beyond'])}" for n, g in by_n.items()))


if __name__ == "__main__":
    raise SystemExit(main())
