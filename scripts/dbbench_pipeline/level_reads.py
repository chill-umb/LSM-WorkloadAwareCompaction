"""The per-level read log (the plugin config's level_reads, 03's LEVEL_READS;
controller/log.h): its records and the checks 28 and 37 share.

With the flag on, the plugin logs every level's eight read counters per
window, each a list with one whole number per level:

  turn mode   every "turn" line carries "level_reads" over the turn it
              closes, [op0, op1); with turn_l0_bill on too, entry 0 of each
              list is l0_read's count and hidden_steps[1] its l1_hidden_steps
  hold-only   a "level_reads" line ("w" from 0, op0, op1, t_us, final) per
              level_reads_ops operations, cut at the first poll past each
              boundary, and a last one ("final": 1) at the drain

Either way the windows must tile the measured phase: the first starts at the
plugin's start (its start line's op, which is the host log's measure_start:
db_bench stamps it and then starts the plugin, on the one client thread),
each starts where the one before ended, and the last, final, ends at the
drain. Their totals per level and counter must be the host log's per-level
counters from measure_start to drain_start: the same counters
(RLReadCounters), read by the plugin at its polls and by the stamps.

Tolerance. The plugin reads the counters at the top of a poll and only then
sees the drain, so in a rare race the last window ends a few operations
before drain_start and misses their reads; a reader on another thread could
also add a count between a stamp and the plugin's sample. Each total (and the
drain_start gap in operations) may therefore differ from the host log's by
at most REL_TOL of it or ABS_TOL counts (ABS_TOL_NANOS for reopen_nanos),
whichever is larger: far above one poll's reads in a real run, far below any
lost or doubled window. The first window's start must be exact.
"""

from __future__ import annotations

import math

import host_log

# RLLevelReadCounts' order (lib/rocksdb/include/rocksdb/rl_controller_host.h),
# which is also a host-log stamp's levels row order.
KEYS = ("probes", "filter_passes", "filter_hits", "seeks", "get_reopens",
        "iter_reopens", "reopen_nanos", "hidden_steps")
FIELD = "level_reads"     # the turn line's field and the hold line's type
REL_TOL = 1e-3
ABS_TOL = 100
ABS_TOL_NANOS = 1_000_000  # 1 ms of reopen time


def on(config: dict | None) -> bool:
    return bool(config) and config.get(FIELD) in (1, True)


def whole(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 and value == math.floor(value))


def block_problem(block, num_levels: int) -> str | None:
    """What is wrong with one "level_reads" block; None when it is whole:
    exactly KEYS, each a list of num_levels whole numbers >= 0."""
    if not isinstance(block, dict):
        return "no level_reads block"
    if set(block) != set(KEYS):
        return f"keys {sorted(block)}, not {list(KEYS)}"
    for key in KEYS:
        values = block[key]
        if not isinstance(values, list) or len(values) != num_levels:
            return f"{key} is not a list of {num_levels} levels"
        if not all(whole(v) for v in values):
            return f"{key} has a value that is not a whole number >= 0"
    return None


def tolerance(key: str, host: float) -> float:
    return max(REL_TOL * abs(host), ABS_TOL_NANOS if key == "reopen_nanos" else ABS_TOL)


def host_counts(records: list[dict]) -> tuple[dict, list[str]]:
    """The host log's measured phase: {"ops": (measure_start op, drain_start
    op), "totals": {key: [per level]} over measure_start..drain_start, "keys":
    the counters its rows carry (all eight from schema 3 on)}, and the
    problems that stop the comparison."""
    try:
        at = host_log.stamp_index(records)
    except ValueError as error:
        return {}, [f"host log: {error}"]
    missing = [name for name in ("measure_start", "drain_start") if name not in at]
    if missing:
        return {}, [f"host log has no {' or '.join(missing)} stamp"]
    first, last = records[at["measure_start"]], records[at["drain_start"]]
    rows0, rows1 = first.get("levels"), last.get("levels")
    if not (isinstance(rows0, list) and isinstance(rows1, list) and rows0
            and len(rows0) == len(rows1)):
        return {}, ["host log stamps without matching per-level rows"]
    width = len(rows0[0])
    if width not in host_log.ROW_WIDTH.values() or any(
            len(row) != width for row in rows0 + rows1):
        return {}, [f"host log rows are not {sorted(host_log.ROW_WIDTH.values())} wide"]
    keys = list(KEYS[:width])
    totals = {key: [rows1[i][k] - rows0[i][k] for i in range(len(rows0))]
              for k, key in enumerate(keys)}
    return {"ops": (first["op"], last["op"]), "totals": totals, "keys": keys}, []


def check_windows(windows: list[tuple], start_op, num_levels, host_records) -> dict:
    """The windows, (label, op0, op1, final, block) in log order, against
    the plugin's start op and the host log (module docstring)."""
    problems = []
    if not isinstance(num_levels, int) or num_levels < 1:
        return {"passed": False, "on": True,
                "problems": ["the start line has no num_levels"]}
    if not windows:
        return {"passed": False, "on": True, "problems": ["no level_reads window"]}
    bad = [(label, why) for label, _, _, _, block in windows
           if (why := block_problem(block, num_levels))]
    if bad:
        problems.append(f"windows without a whole block: {bad[:5]}")
    if windows[0][1] != start_op:
        problems.append(f"the first window starts at op {windows[0][1]}, not at the "
                        f"plugin's start ({start_op})")
    joins = [label for (label, _, end, _, _), (_, begin, _, _, _) in
             zip(windows, windows[1:]) if begin != end]
    backwards = [label for label, op0, op1, _, _ in windows
                 if not (isinstance(op0, int) and isinstance(op1, int) and op1 >= op0)]
    if joins:
        problems.append(f"windows that do not start where the one before ended "
                        f"(the one before): {joins[:10]}")
    if backwards:
        problems.append(f"windows that end before they start: {backwards[:10]}")
    finals = [label for label, _, _, final, _ in windows if final == 1]
    if finals != [windows[-1][0]]:
        problems.append(f"final windows {finals[:10]}: only the last, at the drain, "
                        "must be final")
    out = {"windows": len(windows)}
    host, host_problems = host_counts(host_records)
    problems += host_problems
    if host and not bad and not backwards:
        m_op, d_op = host["ops"]
        out["compared"] = host["keys"]
        out["gaps"] = {"start": windows[0][1] - m_op, "end": d_op - windows[-1][2]}
        if out["gaps"]["start"] != 0:
            problems.append(f"the first window starts {out['gaps']['start']} operations "
                            "after measure_start")
        if abs(out["gaps"]["end"]) > tolerance("ops", d_op - m_op):
            problems.append(f"the last window ends {out['gaps']['end']} operations "
                            "before drain_start")
        rows = len(next(iter(host["totals"].values())))
        if rows != num_levels:
            problems.append(f"the host log has {rows} levels, the plugin {num_levels}")
        else:
            worst, off = {key: 0 for key in host["keys"]}, []
            for key in host["keys"]:
                for level in range(num_levels):
                    got = sum(block[key][level] for *_, block in windows)
                    want = host["totals"][key][level]
                    diff = got - want
                    if abs(diff) > abs(worst[key]):
                        worst[key] = diff
                    if abs(diff) > tolerance(key, want):
                        off.append((key, level, got, want))
            out["max_deviation"] = worst
            if off:
                problems.append("totals that differ from the host log's measure_start.."
                                f"drain_start counters (key, level, logged, host): {off[:10]}")
    return {"passed": not problems, "on": True, "problems": problems, **out}


def stray(decisions: list[dict]) -> list:
    """With the flag off: the lines that carry the log anyway."""
    return [r.get("t", r.get("w", r.get("type"))) for r in decisions
            if r.get("type") == FIELD or FIELD in r]


def check_turn(decisions: list[dict], config: dict, host_records) -> dict | None:
    """Mode turn: the turn lines' blocks (module docstring); with
    turn_l0_bill on, L0's entries are l0_read's. None with the flag off and
    no stray block (nothing to report: the check is as before)."""
    if not on(config):
        lines = stray(decisions)
        return None if not lines else {
            "passed": False, "on": False,
            "problems": [f"level_reads off, but lines carry it: {lines[:10]}"]}
    start = decisions[0] if decisions and decisions[0].get("type") == "start" else {}
    turns = [r for r in decisions if r.get("type") == "turn"]
    report = check_windows([(r.get("t"), r.get("op0"), r.get("op1"), r.get("final"),
                             r.get(FIELD)) for r in turns],
                           start.get("op"), start.get("num_levels"), host_records)
    own = [r.get("w") for r in decisions if r.get("type") == FIELD]
    if own:
        report["problems"].append(f"level_reads lines in mode turn: {own[:10]}")
    if int(config.get("turn_l0_bill", 0)) == 1:
        disagree = []
        for r in turns:
            block, l0 = r.get(FIELD), r.get("l0_read")
            if block_problem(block, start.get("num_levels")) or not isinstance(l0, dict):
                continue
            want = {key: l0.get(key) for key in KEYS}
            got = {key: block[key][0] for key in KEYS}
            if got != want or (len(block["hidden_steps"]) > 1 and
                               block["hidden_steps"][1] != l0.get("l1_hidden_steps")):
                disagree.append(r.get("t"))
        if disagree:
            report["problems"].append(f"turns whose L0 entries are not l0_read's: "
                                      f"{disagree[:10]}")
    report["passed"] = not report["problems"]
    return report


def check_hold(decisions: list[dict], config: dict, host_records) -> dict | None:
    """Hold-only: the "level_reads" lines, numbered 0, 1, ... (module
    docstring). None with the flag off and no stray line."""
    if not on(config):
        lines = stray(decisions)
        return None if not lines else {
            "passed": False, "on": False,
            "problems": [f"level_reads off, but lines carry it: {lines[:10]}"]}
    start = decisions[0] if decisions and decisions[0].get("type") == "start" else {}
    lines = [r for r in decisions if r.get("type") == FIELD]
    report = check_windows([(r.get("w"), r.get("op0"), r.get("op1"), r.get("final"),
                             r.get(FIELD)) for r in lines],
                           start.get("op"), start.get("num_levels"), host_records)
    if [r.get("w") for r in lines] != list(range(len(lines))):
        report["problems"].append("level_reads lines not numbered 0, 1, ...")
    carried = [r.get("type") for r in decisions if r.get("type") != FIELD and FIELD in r]
    if carried:
        report["problems"].append(f"other lines carry level_reads: {carried[:10]}")
    report["passed"] = not report["problems"]
    return report
