"""28_check_plugin_run.py on synthetic plugin logs, each check failing on the
defect it names; and 03's hold and rules arms end to end with a stub
db_bench that writes those logs: the config composed and passed, the
fingerprint's plugin segment parsing in fingerprint.py, the arm checked and scored, and
the refusals before any run."""
import importlib.util
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_run_experiments import (FIXTURE, STUB, run03, run04, select,
                                      summary)

PIPELINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_plugin_run", PIPELINE / "28_check_plugin_run.py")
check28 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check28)

STARTED = "RL_PLUGIN_STARTED created=1\nRL_PLUGIN_STOPPED\n"


def decision(id, op, level=1, old=1.0, requested=1.0, action="defer",
             mask=(1, 0, 1, 1), mode="rules"):
    return {"type": "decision", "schema": 2, "id": id, "level": level,
            "op": op, "mode": mode, "action": action, "mask": list(mask),
            "old": old, "requested": requested}


def apply(op, first, last, m=(1, 1, 1, 1), k0=4, ok=1, seen=1):
    """An apply line; m is the whole vector sent, k0 the trigger."""
    return {"type": "apply", "op": op, "first_id": first, "last_id": last,
            "m": list(m), "k0": k0, "ok": ok, "seen": seen,
            "seen_generation": 7}


def logs(mode="rules", levels=4, body=(), fallback=False):
    """A decision log and a transition log as controller/log.h writes them;
    `body` goes between start and stop, in op order."""
    lines = [{"type": "start", "mode": mode, "num_levels": levels}, *body]
    if fallback:
        lines.append({"type": "fallback", "reason": "config: need eps"})
    lines.append({"type": "stop", "fallback": 1 if fallback else 0})
    transitions = [{"type": "transition", "schema": 2, "level": level}
                   for level in range(levels)]
    return lines, transitions


def verdict(stdout=STARTED, mode="rules", expect_fallback=False,
            min_changes=0, problems=(), **kwargs):
    decisions, transitions = logs(mode=mode, **kwargs)
    return check28.check(stdout, decisions, transitions, list(problems), mode,
                         expect_fallback, min_changes)


# Level 1 decides at ops 100 and 200: a defer to 0.9 at 100, held at 200.
CHANGE = decision(1, 100, requested=0.9)
NEXT = decision(2, 200, old=0.9, requested=0.9, action="hold")


class CheckPluginRunTest(unittest.TestCase):
    def test_a_clean_rules_run_passes(self):
        report = verdict(body=[CHANGE, apply(100, 1, 1, m=(1, 0.9, 1, 1)),
                               NEXT, apply(150, 0, 0, ok=0, seen=0)],
                         min_changes=1)
        self.assertEqual(report["verdict"], "passed", report["failed_checks"])
        act3 = report["checks"]["act3"]
        self.assertEqual((act3["changes"], act3["faithful"], act3["share"]),
                         (1, 1, 1.0))
        self.assertEqual(act3["refused_calls"], 1)

    def test_act3_judges_each_requested_change(self):
        """Held past the level's next decision, overwritten before any call,
        carried by a call the next snapshot did not show or that RocksDB
        refused: each fails. Cut off by the drain: reported, not judged."""
        failing = {
            "held past the interval": [CHANGE, NEXT,
                                       apply(250, 1, 1, m=(1, 0.9, 1, 1))],
            "overwritten": [CHANGE, decision(2, 200, old=0.9, requested=0.8),
                            apply(200, 1, 2, m=(1, 0.8, 1, 1))],
            "not seen": [CHANGE, apply(100, 1, 1, m=(1, 0.9, 1, 1), seen=0),
                         NEXT],
            "refused": [CHANGE, apply(100, 1, 1, m=(1, 0.9, 1, 1), ok=0),
                        NEXT],
            "another level's call": [CHANGE, apply(100, 1, 1), NEXT],
        }
        for name, body in failing.items():
            with self.subTest(name):
                report = verdict(body=body)
                self.assertEqual(report["failed_checks"], ["act3"])
                self.assertIn(1, report["checks"]["act3"]["failed_ids"])
                self.assertEqual(report["checks"]["act3"]["failed_by_level"],
                                 {1: 1})
        # Calls the SetOptions cap held are counted, to tell that cause apart.
        held = verdict(body=[CHANGE, {"type": "apply_held", "op": 150,
                                      "first_id": 1, "last_id": 1}, NEXT])
        self.assertEqual(held["checks"]["act3"]["held_calls"], 1)
        self.assertIn("1 held by the SetOptions cap",
                      held["checks"]["act3"]["problems"][0])
        # L0's change is its trigger, carried as k0.
        l0 = verdict(body=[decision(1, 100, level=0, old=4, requested=3),
                           apply(100, 1, 1, k0=3),
                           decision(2, 300, level=0, old=3, requested=3)])
        self.assertEqual(l0["checks"]["act3"]["faithful"], 1)
        # The last change of a level, with no call before the drain.
        cut = verdict(body=[CHANGE], min_changes=1)
        self.assertEqual(cut["checks"]["act3"]["truncated_ids"], [1])
        self.assertEqual(cut["checks"]["act3"]["changes"], 0)
        self.assertEqual(cut["failed_checks"], ["act3"])  # min_changes
        # ... but a later call that leaves it out lost it.
        lost = verdict(body=[CHANGE, apply(150, 1, 1)])
        self.assertEqual(lost["checks"]["act3"]["failed_ids"], [1])
        # A call at exactly the level's next decision is within the interval.
        edge = verdict(body=[CHANGE, NEXT, apply(200, 1, 1, m=(1, 0.9, 1, 1))])
        self.assertEqual(edge["checks"]["act3"]["faithful"], 1)
        self.assertEqual(edge["verdict"], "passed", edge["failed_checks"])
        # A hold asks for no change, so it is not judged, even with no call.
        hold = verdict(body=[decision(1, 100, action="hold"),
                             decision(2, 200, requested=0.9),
                             apply(200, 2, 2, m=(1, 0.9, 1, 1)),
                             decision(3, 300, old=0.9, requested=0.9,
                                      action="hold")])
        self.assertEqual((hold["checks"]["act3"]["changes"],
                          hold["checks"]["act3"]["faithful"]), (1, 1))
        # 99 of 100 in time passes, 98 does not. Levels 1-3 take turns, so a
        # level's next decision comes 3000 operations later; a late call
        # comes 3500 after its decision.
        for late, passes in ((1, True), (2, False)):
            body = []
            for i in range(100):
                op = 1000 * (i + 1)
                body += [decision(2 * i + 1, op, level=1 + i % 3,
                                  requested=0.9),
                         apply(op + (3500 if i < late else 0), 2 * i + 1,
                               2 * i + 1, m=[1] + [0.9] * 3)]
            # Each level's next decision closes its last change's interval.
            body += [decision(1000 + level, 10**6, level=level, old=0.9,
                              requested=0.9) for level in (1, 2, 3)]
            report = verdict(body=sorted(body, key=lambda line: line["op"]))
            self.assertEqual(report["checks"]["act3"]["changes"], 100)
            self.assertEqual(report["verdict"] == "passed", passes, late)

    def test_act3_takes_a_logged_repair_as_the_carried_value(self):
        # learned2 at T = 2 (2026-11-05): L1 at 1.5 makes Repair lift an L2
        # move to 0.667 up to 0.75 in the same poll; the call carries 0.75.
        def l2(id, op, old, requested, action="down"):
            return decision(id, op, level=2, old=old, requested=requested,
                            action=action, mask=(1, 1, 1, 0), mode="learned2")
        def repair(op, old, new):
            return {"type": "repair", "level": 2, "op": op, "old": old,
                    "new": new, "reason": "targets must not shrink going down"}
        lifted = verdict(mode="learned2", body=(
            l2(1, 100, 1.0, 2 / 3), repair(100, 2 / 3, 0.75),
            apply(101, 1, 1, m=(1, 1.5, 0.75, 1)),
            l2(2, 900, 0.75, 0.75, action="hold")))
        self.assertTrue(lifted["checks"]["act3"]["passed"], lifted)
        self.assertEqual(lifted["checks"]["act3"]["faithful"], 1)
        # A repair back to the value in effect undid the change: not judged.
        undone = verdict(mode="learned2", body=(
            l2(1, 100, 0.75, 0.5), repair(100, 0.5, 0.75),
            l2(2, 900, 0.75, 0.75, action="hold")))
        self.assertTrue(undone["checks"]["act3"]["passed"], undone)
        self.assertEqual(undone["checks"]["act3"]["changes"], 0)
        # Without a repair line, a call carrying another value still fails.
        wrong = verdict(mode="learned2", body=(
            l2(1, 100, 1.0, 2 / 3), apply(101, 1, 1, m=(1, 1.5, 0.75, 1)),
            l2(2, 900, 0.75, 0.75, action="hold")))
        self.assertFalse(wrong["checks"]["act3"]["passed"])

    def test_each_defect_fails_its_check(self):
        cases = [
            ({"stdout": "RL_PLUGIN_STARTED created=0\nRL_PLUGIN_STOPPED\n"},
             "started"),
            ({"stdout": "RL_PLUGIN_STARTED created=1\n"}, "started"),
            ({"body": [decision(1, 1, mask=(1, 0, 0, 1))]}, "masks"),
            ({"body": [decision(1, 1, action="jump", mask=(1, 1, 1, 1))]},
             "masks"),
            ({"min_changes": 1}, "act3"),
            ({"fallback": True}, "fallback"),
            ({"expect_fallback": True}, "fallback"),
            ({"problems": ["decisions.jsonl line 9 does not parse"]}, "logs"),
        ]
        for changes, name in cases:
            with self.subTest(changes=changes):
                report = verdict(**changes)
                self.assertEqual(report["failed_checks"], [name])

    def test_hold_only_never_calls_setoptions(self):
        self.assertEqual(verdict(mode="hold-only")["verdict"], "passed")
        report = verdict(mode="hold-only", body=[apply(1, 0, 0)])
        self.assertEqual(report["failed_checks"], ["hold_only"])

    def test_hold_only_with_a_scheduled_change_calls_exactly_once(self):
        """The step-response arm (owner, 2026-10-07): one accepted apply
        marked scheduled, at or after apply_at_op, and no other."""
        plan = {"type": "apply_scheduled", "op": 0, "apply_at_op": 500,
                "m": [1, 1, 2, 1], "k0": 0}
        def sched(op, ok=1):
            return {**apply(op, 0, 0, m=(1, 1, 2, 1), ok=ok), "scheduled": 1}
        def failed(*body):
            return verdict(mode="hold-only", body=list(body))["failed_checks"]
        report = verdict(mode="hold-only", body=[plan, sched(600)])
        self.assertEqual(report["verdict"], "passed")
        self.assertEqual(report["checks"]["hold_only"]["scheduled_applies"], 1)
        self.assertEqual(failed(plan), ["hold_only"])               # never made
        self.assertEqual(failed(plan, sched(400)), ["hold_only"])   # too early
        self.assertEqual(failed(plan, sched(600, ok=0)), ["hold_only"])
        self.assertEqual(failed(plan, sched(600), sched(700)), ["hold_only"])
        self.assertEqual(failed(plan, sched(600), apply(800, 0, 0)), ["hold_only"])
        self.assertEqual(failed(sched(600)), ["hold_only"])         # no plan line

    def test_hold_only_with_a_schedule_calls_once_per_change_in_order(self):
        """The K-step batch's multi-change runs (owner, 2026-10-21): one
        accepted apply per planned change, in order, none before its op."""
        plans = [{"type": "apply_scheduled", "op": 0, "apply_at_op": at,
                  "m": [1, 1, 1, 1], "k0": k0, "index": i}
                 for i, (at, k0) in enumerate(((500, 8), (900, 2), (1300, 0)))]
        def sched(op, index, ok=1):
            return {**apply(op, 0, 0, m=(1, 1, 1, 1), ok=ok), "scheduled": 1,
                    "index": index}
        def failed(*body):
            return verdict(mode="hold-only", body=[*plans, *body])["failed_checks"]
        made = [sched(510, 0), sched(905, 1), sched(1300, 2)]
        report = verdict(mode="hold-only", body=[*plans, *made])
        self.assertEqual(report["verdict"], "passed", report["checks"]["hold_only"])
        self.assertEqual(report["checks"]["hold_only"]["scheduled_applies"], 3)
        self.assertEqual(failed(*made[:2]), ["hold_only"])           # one missing
        self.assertEqual(failed(made[0], sched(850, 1), made[2]),
                         ["hold_only"])                              # too early
        self.assertEqual(failed(made[0], sched(905, 1, ok=0), made[2]),
                         ["hold_only"])
        self.assertEqual(failed(made[0], sched(905, 2), sched(1300, 1)),
                         ["hold_only"])                              # out of order
        self.assertEqual(failed(*made, sched(1400, 3)), ["hold_only"])

    def test_an_expected_fallback_passes(self):
        report = verdict(fallback=True, expect_fallback=True)
        self.assertEqual(report["verdict"], "passed", report["failed_checks"])

    def test_the_logs_must_cover_every_level_from_start_to_stop(self):
        decisions, transitions = logs()
        for broken, expected in (
                (decisions[1:], "does not start with `start`"),
                (decisions[:-1], "does not end with `stop`")):
            report = check28.check(STARTED, broken, transitions, [], "rules",
                                   False, 0)
            self.assertIn(expected, " ".join(report["checks"]["logs"]
                                             ["problems"]))
        report = check28.check(STARTED, decisions, transitions[:-1], [],
                               "rules", False, 0)
        self.assertIn("levels with no transition: [3]",
                      report["checks"]["logs"]["problems"])
        report = check28.check(STARTED, decisions, transitions, [], "hold-only",
                               False, 0)
        self.assertEqual(report["failed_checks"], ["mode"])

    def test_turn_mode_judges_its_turn_lines(self):
        ones, up = [1, 1, 1, 1], [1, 1.4142135623730951, 1, 1]
        start = {"type": "start", "mode": "turn", "num_levels": 4, "m": ones, "k0": 4}
        stop = {"type": "stop", "fallback": 0}

        def turn(t, sent, applied, set_, op0=None, action="hold", expand=1):
            return {"type": "turn", "t": t, "op0": 100 * t if op0 is None else op0,
                    "op1": 100 * (t + 1), "final": 0,
                    "masks": [[1, 1, 1, 1], [1, 1, 1, expand], [1, 0, 0, 0], [1, 0, 0, 0]],
                    "action": ["hold", action, "hold", "hold"], "sent_m": sent,
                    "sent_k": 4, "applied_m": applied, "applied_k": 4, "set": set_}

        def judged(body):
            lines = [start, *body, stop]
            transitions = [{"type": "transition", "level": i} for i in range(4)]
            return check28.check(STARTED, lines, transitions, [], "turn", False, 0)

        # Sent at its boundary; then held by the cap and sent within the turn.
        report = judged([apply(100, 0, 0, m=up), turn(0, up, up, 1, action="expand"),
                         turn(1, ones, up, 0), apply(150, 0, 0, m=ones),
                         turn(2, ones, ones, 0)])
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["act3"]["faithful"], 2)
        self.assertEqual(report["checks"]["turn"]["turns"], 3)
        # Held and never sent before the next turn: a lost change.
        report = judged([turn(0, up, ones, 0, action="expand"), turn(1, up, ones, 0)])
        self.assertIn("act3", report["failed_checks"])
        self.assertEqual(report["checks"]["act3"]["failed_ids"], [0])
        self.assertEqual(report["checks"]["act3"]["truncated_ids"], [1])
        # A real log's end (w6 and w8, 2026-11-08): the last turn's own call
        # refused because the drain started, then the drain and the final
        # line. Cut off by the drain, not lost; a refusal before a later
        # deciding turn still fails.
        drained = apply(200, 0, 0, m=up)
        drained.update(ok=0, error="Operation aborted: RLControllerHost: the drain has started")
        final = {"type": "turn", "t": 2, "op0": 200, "op1": 200, "final": 1}
        report = judged([turn(0, ones, ones, 0), drained, turn(1, up, ones, 0, action="expand"),
                         {"type": "drain", "op": 200}, final])
        self.assertNotIn("act3", report["failed_checks"])
        self.assertEqual(report["checks"]["act3"]["failed_ids"], [])
        self.assertEqual(report["checks"]["act3"]["truncated_ids"], [1])
        report = judged([turn(0, ones, ones, 0), drained, turn(1, up, ones, 0, action="expand"),
                         turn(2, up, ones, 0), {"type": "drain", "op": 300},
                         {**final, "t": 3, "op0": 300, "op1": 300}])
        self.assertIn("act3", report["failed_checks"])
        self.assertEqual(report["checks"]["act3"]["failed_ids"], [1])
        self.assertEqual(report["checks"]["act3"]["truncated_ids"], [2])
        # A gap in operations, and a masked action.
        report = judged([turn(0, ones, ones, 0), turn(1, ones, ones, 0, op0=150)])
        self.assertIn("turn", report["failed_checks"])
        report = judged([apply(100, 0, 0, m=up),
                         turn(0, up, up, 1, action="expand", expand=0)])
        self.assertIn("turn", report["failed_checks"])

    def test_a_truncated_last_line_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.jsonl"
            path.write_text('{"type":"start"}\n{"type":"sto')
            lines, problems = check28.read_lines(path)
            self.assertEqual(len(lines), 1)
            self.assertEqual(problems,
                             ["decisions.jsonl line 2 does not parse"])
            self.assertEqual(check28.read_lines(Path(tmp) / "none")[1],
                             ["none missing"])


KEYS = check28.level_reads.KEYS


def read_block(scale, levels=3):
    """A level_reads block: counter k at level i is scale (k + 1) (i + 1)."""
    return {key: [scale * (k + 1) * (i + 1) for i in range(levels)]
            for k, key in enumerate(KEYS)}


def stamp(name, op, rows):
    return {"type": "stamp", "name": name, "op": op, "levels": rows}


def host(windows, levels=3, width=8, extra=None):
    """A host log whose measure_start..drain_start counters are the windows'
    totals (plus `extra`, {(key, level): n}), from a non-zero base."""
    base = [[7 * (i + 1)] * width for i in range(levels)]
    end = [row[:] for row in base]
    for _, _, _, block in windows:
        for k, key in enumerate(KEYS[:width]):
            for i, n in enumerate(block[key][:levels]):
                end[i][k] += n
    for (key, level), n in (extra or {}).items():
        end[level][KEYS.index(key)] += n
    return [{"type": "header", "schema": 3}, stamp("measure_start", 1000, base),
            stamp("drain_start", windows[-1][1], end), stamp("drain_end", windows[-1][1], end)]


# Three windows, [1000, 2000), [2000, 3500), [3500, 4000) the drain's.
WINDOWS = [(1000, 2000, 0, read_block(10)), (2000, 3500, 0, read_block(15)),
           (3500, 4000, 1, read_block(5))]


class LevelReadsCheckTest(unittest.TestCase):
    """28's level_reads check (level_reads.py) on synthetic logs: hold-only's
    lines and a turn run's blocks against the host log's stamps."""

    def hold(self, windows=WINDOWS, config=None, host_records=None, levels=3, extra=()):
        body = [{"type": "level_reads", "w": w, "op0": op0, "op1": op1, "t_us": 0,
                 "final": final, "level_reads": block}
                for w, (op0, op1, final, block) in enumerate(windows)]
        lines = [{"type": "start", "mode": "hold-only", "num_levels": levels, "op": 1000},
                 *extra, {"type": "drain", "op": 4000}, *body, {"type": "stop", "fallback": 0}]
        transitions = [{"type": "transition", "level": i} for i in range(levels)]
        config = {"level_reads": 1, "level_reads_ops": 1000} if config is None else config
        return check28.check(STARTED, lines, transitions, [], "hold-only", False, 0,
                             config=config,
                             host_records=host(WINDOWS) if host_records is None else host_records)

    def test_a_whole_hold_run_passes(self):
        report = self.hold()
        self.assertEqual(report["verdict"], "passed", report["checks"].get("level_reads"))
        lr = report["checks"]["level_reads"]
        self.assertEqual((lr["windows"], lr["compared"], lr["gaps"]),
                         (3, list(KEYS), {"start": 0, "end": 0}))
        self.assertEqual(set(lr["max_deviation"].values()), {0})
        # A scheduled change rides along, judged as before.
        plan = {"type": "apply_scheduled", "op": 1000, "apply_at_op": 1500,
                "m": [1, 1, 2], "k0": 0}
        made = {**apply(2000, 0, 0, m=(1, 1, 2)), "scheduled": 1}
        report = self.hold(extra=[plan, made])
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["hold_only"]["scheduled_applies"], 1)

    def test_off_the_report_is_as_before(self):
        plain = verdict(mode="hold-only")
        decisions, transitions = logs(mode="hold-only")
        for config in ({"level_reads": 0}, {}):
            report = check28.check(STARTED, decisions, transitions, [], "hold-only",
                                   False, 0, config=config, host_records=[])
            self.assertEqual(report, plain)
        # A line that carries it anyway fails.
        decisions.insert(1, {"type": "level_reads", "w": 0})
        report = check28.check(STARTED, decisions, transitions, [], "hold-only", False, 0,
                               config={"level_reads": 0}, host_records=[])
        self.assertEqual(report["failed_checks"], ["level_reads"])
        # Without --config the check is not run at all (22's and 13's calls).
        self.assertNotIn("level_reads", check28.check(
            STARTED, decisions, transitions, [], "hold-only", False, 0)["checks"])

    def test_each_defect_fails_the_check(self):
        w = WINDOWS
        short = {**read_block(15), "probes": [1, 2]}
        negative = {**read_block(15), "seeks": [-1, 0, 0]}
        fraction = {**read_block(15), "seeks": [0.5, 0, 0]}
        missing = {k: v for k, v in read_block(15).items() if k != "hidden_steps"}
        cases = {
            "a gap": [w[0], (2100, 3500, 0, w[1][3]), w[2]],
            "an overlap": [w[0], (1900, 3500, 0, w[1][3]), w[2]],
            "a late start": [(1100, 2000, 0, w[0][3]), w[1], w[2]],
            "no final": [w[0], w[1], (3500, 4000, 0, w[2][3])],
            "a final not last": [(1000, 2000, 1, w[0][3]), w[1], w[2]],
            "a level short": [w[0], (2000, 3500, 0, short), w[2]],
            "a negative count": [w[0], (2000, 3500, 0, negative), w[2]],
            "a fraction": [w[0], (2000, 3500, 0, fraction), w[2]],
            "a counter missing": [w[0], (2000, 3500, 0, missing), w[2]],
            "a lost window": [w[0], (2000, 4000, 1, w[2][3])],
            "no window": [],
        }
        for name, windows in cases.items():
            with self.subTest(name):
                report = self.hold(windows=windows)
                self.assertEqual(report["failed_checks"], ["level_reads"], report)
        # Another line carrying a block; lines numbered out of order.
        report = self.hold(extra=[{"type": "decision_x", "level_reads": {}}])
        self.assertEqual(report["failed_checks"], ["level_reads"])
        lines = [{"type": "start", "mode": "hold-only", "num_levels": 3, "op": 1000}] + [
            {"type": "level_reads", "w": w, "op0": op0, "op1": op1, "final": final,
             "level_reads": block} for w, (op0, op1, final, block) in zip((0, 2, 1), WINDOWS)]
        report = check28.level_reads.check_hold(lines, {"level_reads": 1}, host(WINDOWS))
        self.assertEqual(report["problems"], ["level_reads lines not numbered 0, 1, ..."])

    def test_the_totals_must_be_the_host_logs_within_the_tolerance(self):
        tol = check28.level_reads
        # Probes at L2: the windows' total is (10 + 15 + 5) * 3 = 90, so 100
        # counts (ABS_TOL) is the bound; reopen time is bounded by 1 ms.
        for extra, passes in ((("probes", 2, 100), True), (("probes", 2, 101), False),
                              (("probes", 2, -100), True), (("hidden_steps", 0, 250), False),
                              (("reopen_nanos", 1, tol.ABS_TOL_NANOS), True),
                              (("reopen_nanos", 1, tol.ABS_TOL_NANOS + 1), False)):
            with self.subTest(extra=extra):
                key, level, n = extra
                report = self.hold(host_records=host(WINDOWS, extra={(key, level): n}))
                self.assertEqual(report["verdict"] == "passed", passes, report)
        # Above ABS_TOL / REL_TOL counts the relative bound governs.
        big = [(op0, op1, final, read_block(100000)) for op0, op1, final, _ in WINDOWS]
        bound = tol.REL_TOL * 3 * 100000                  # probes at L0: 300
        for n, passes in ((bound, True), (bound + 1, False)):
            report = self.hold(windows=big, host_records=host(big, extra={("probes", 0): n}))
            self.assertEqual(report["verdict"] == "passed", passes, n)
        # The last window may end short of drain_start only within the bound.
        records = host(WINDOWS)
        for r in records[2:]:
            r["op"] = 4000 + 101
        self.assertEqual(self.hold(host_records=records)["failed_checks"], ["level_reads"])
        for r in records[2:]:
            r["op"] = 4000 + 100
        self.assertEqual(self.hold(host_records=records)["verdict"], "passed")
        # No stamps, other levels, or rows of an unknown width: refused.
        self.assertEqual(self.hold(host_records=[])["failed_checks"], ["level_reads"])
        self.assertEqual(self.hold(host_records=host(WINDOWS, levels=4))["failed_checks"],
                         ["level_reads"])
        odd = host(WINDOWS)
        odd[1]["levels"] = [row[:5] for row in odd[1]["levels"]]
        self.assertEqual(self.hold(host_records=odd)["failed_checks"], ["level_reads"])
        # A schema 2 host log (no hidden steps) compares the seven it has.
        report = self.hold(host_records=host(WINDOWS, width=7))
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["level_reads"]["compared"], list(KEYS[:7]))

    def test_turn_lines_carry_it_and_l0_reads_agree(self):
        def turn_lines(windows, l0=None, extra=()):
            lines = [{"type": "start", "mode": "turn", "num_levels": 3, "op": 1000,
                      "m": [1, 1, 1], "k0": 4}, *extra]
            for t, (op0, op1, final, block) in enumerate(windows):
                line = {"type": "turn", "t": t, "op0": op0, "op1": op1, "final": final,
                        "masks": [[1, 0, 0, 0]] * 3, "action": ["hold"] * 3,
                        "sent_m": [1, 1, 1], "sent_k": 4, "set": 0, "level_reads": block}
                if l0 is not None:
                    line["l0_read"] = l0(block)
                lines.append(line)
            return lines + [{"type": "stop", "fallback": 0}]

        def judged(lines, config):
            transitions = [{"type": "transition", "level": i} for i in range(3)]
            return check28.check(STARTED, lines, transitions, [], "turn", False, 0,
                                 config=config, host_records=host(WINDOWS))

        def agreeing(block):
            return {**{k: v[0] for k, v in block.items()},
                    "l1_hidden_steps": block["hidden_steps"][1]}

        report = judged(turn_lines(WINDOWS), {"level_reads": 1})
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["level_reads"]["windows"], 3)
        both = {"level_reads": 1, "turn_l0_bill": 1}
        self.assertEqual(judged(turn_lines(WINDOWS, agreeing), both)["verdict"], "passed")
        for bad in (lambda b: {**agreeing(b), "probes": agreeing(b)["probes"] + 1},
                    lambda b: {**agreeing(b), "l1_hidden_steps": 0}):
            report = judged(turn_lines(WINDOWS, bad), both)
            self.assertEqual(report["failed_checks"], ["level_reads"], report)
        # A hold-only line in mode turn, a missing block, a gap: each fails.
        stray_line = [{"type": "level_reads", "w": 0}]
        self.assertEqual(judged(turn_lines(WINDOWS, extra=stray_line),
                                {"level_reads": 1})["failed_checks"], ["level_reads"])
        lines = turn_lines(WINDOWS)
        del lines[2]["level_reads"]
        self.assertEqual(judged(lines, {"level_reads": 1})["failed_checks"], ["level_reads"])
        # Off: blocks on the lines fail; none, and the report is as before.
        self.assertEqual(judged(turn_lines(WINDOWS), {"level_reads": 0})["failed_checks"],
                         ["level_reads"])
        lines = turn_lines(WINDOWS)
        for line in lines:
            line.pop("level_reads", None)
        transitions = [{"type": "transition", "level": i} for i in range(3)]
        self.assertEqual(judged(lines, {"level_reads": 0}),
                         check28.check(STARTED, lines, transitions, [], "turn", False, 0))


# The turn mode's 35 input names (rl_agent/learner/turn_weights.py at five
# acting levels), for the stub's turn lines.
TURN_NAMES = (["put_share", "get_share", "scan_share", "pending_soft",
               "stall_share", "busy_share", "l0_files_k", "l0_anchor", "l0_slow",
               "l0_flushes_hat", "l0_probes_get"] +
              [f"l{i}_{f}" for i in range(1, 5)
               for f in ("score", "anchor", "backlog", "owed", "inflow_hat",
                         "probes_get")])

# The stub db_bench of test_run_experiments, plus the plugin's part: with
# --rl_plugin_config it writes the logs the config names. STUB_PLUGIN_MASKED=1
# makes its one decision (or, in mode turn, its one expand) take a masked
# action. In mode turn its lines follow the config's turn_acting (the inputs
# of that many levels) and, with turn_l0_bill 1, carry an l0_read block.
# With level_reads 1 it logs the host log's per-level counts from
# measure_start to drain_start in two windows (290-600, 600-1000; the start
# line then has the host log's op and levels): on the two turn lines, or in
# hold-only as two "level_reads" lines after a drain line;
# STUB_LEVEL_READS_BAD=1 adds 5000 probes at L0 to the first.
PLUGIN_STUB = STUB.replace(
    "cat " + shlex.quote(str(FIXTURE / "run.log")), f"""
for a in "$@"; do
  case "$a" in --rl_plugin_config=*) config="${{a#*=}}" ;; esac
done
cat {shlex.quote(str(FIXTURE / "run.log"))}
if [[ -n "${{config:-}}" ]]; then
  python3 - "$config" "${{STUB_PLUGIN_MASKED:-0}}" "${{log:-}}" \
      "${{STUB_LEVEL_READS_BAD:-0}}" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
mask = [1, 0, 0, 1] if sys.argv[2] == "1" else [1, 0, 1, 1]
mode = config["mode"]
lines = [{{"type": "start", "mode": mode, "num_levels": 4}}]
blocks = None
if config.get("level_reads") == 1:
    keys = ["probes", "filter_passes", "filter_hits", "seeks", "get_reopens",
            "iter_reopens", "reopen_nanos", "hidden_steps"]
    stamps = {{r["name"]: r for r in map(json.loads, open(sys.argv[3]))
              if r.get("type") == "stamp"}}
    a, b = stamps["measure_start"], stamps["drain_start"]
    n = len(a["levels"])
    total = {{k: [b["levels"][i][j] - a["levels"][i][j] if j < len(a["levels"][i])
                  else 0 for i in range(n)] for j, k in enumerate(keys)}}
    first = {{k: [v // 3 for v in total[k]] for k in keys}}
    second = {{k: [v - f for v, f in zip(total[k], first[k])] for k in keys}}
    if sys.argv[4] == "1":
        first["probes"][0] += 5000
    blocks = (first, second)
    lines[0].update({{"op": a["op"], "num_levels": n}})
if mode == "rules":
    lines += [{{"type": "decision", "id": 1, "level": 1, "op": 10,
                "mode": mode, "action": "defer", "mask": mask, "old": 1,
                "requested": 0.9}},
              {{"type": "apply", "op": 10, "first_id": 1, "last_id": 1,
                "m": [1, 0.9, 1, 1], "k0": 4, "ok": 1, "seen": 1}}]
if mode == "turn":
    lines[0].update({{"m": [1, 1, 1, 1], "k0": 4}})
    acting = int(config["turn_acting"])
    names = {json.dumps(TURN_NAMES)}[:11 + 6 * (acting - 1)]
    up = [1, 1.4142135623730951, 1, 1]
    turn = {{"masks": [[1, 1, 1, 1], [1, 1, 1, mask[2]], [1, 0, 0, 0],
                       [1, 0, 0, 0]],
             "x": {{n: 0.5 for n in names}}, "owed": [0, 0, 0, 0],
             "mid_applies": 0}}
    if config.get("turn_l0_bill") == 1:
        turn["l0_read"] = {{"probes": 70, "filter_passes": 3, "filter_hits": 1,
                            "seeks": 5, "get_reopens": 2, "iter_reopens": 1,
                            "reopen_nanos": 100, "hidden_steps": 4,
                            "l1_hidden_steps": 6}}
    own = [{{}}, {{}}]
    for t, block in enumerate(blocks or ()):
        own[t]["level_reads"] = block
        if config.get("turn_l0_bill") == 1:
            own[t]["l0_read"] = {{**{{k: v[0] for k, v in block.items()}},
                                 "l1_hidden_steps": block["hidden_steps"][1]}}
    lines += [{{"type": "turn_start", "arch": config["turn_arch"], "acting": acting,
                "features": names}},
              {{"type": "apply", "op": 600, "first_id": 0, "last_id": 0,
                "m": up, "k0": 4, "ok": 1, "seen": 1}},
              {{"type": "turn", "t": 0, "op0": 290, "op1": 600, "final": 0,
                "action": ["hold", "expand", "hold", "hold"], "sent_m": up,
                "sent_k": 4, "applied_m": up, "applied_k": 4, "set": 1, **turn,
                **own[0]}},
              {{"type": "turn", "t": 1, "op0": 600, "op1": 1000, "final": 1,
                **turn, **own[1]}}]
if mode == "hold-only" and blocks:
    lines += [{{"type": "drain", "op": 1000}}] + [
        {{"type": "level_reads", "w": w, "op0": op0, "op1": op1, "t_us": 0,
          "final": int(w == 1), "level_reads": block}}
        for w, (op0, op1, block) in enumerate(((290, 600, blocks[0]),
                                                (600, 1000, blocks[1])))]
lines.append({{"type": "stop", "fallback": 0}})
with open(config["decision_log"], "w") as out:
    out.writelines(json.dumps(line) + "\\n" for line in lines)
with open(config["transition_log"], "w") as out:
    out.writelines(json.dumps({{"type": "transition", "level": level}}) + "\\n"
                   for level in range(4))
PY
  echo RL_PLUGIN_STARTED created=1
  echo RL_PLUGIN_STOPPED
fi
""")


def run03_plugin(root: Path, stub: str = PLUGIN_STUB, **overrides):
    build = root / "build"
    build.mkdir(parents=True)
    (build / "db_bench").write_text(stub)
    (build / "db_bench").chmod(0o755)
    plugin = root / "librl_controller.so"
    plugin.write_bytes(b"stand-in plugin")
    return run03(root, CONTROLLER_PLUGIN=str(plugin),
                 ACTION_BOUNDS_FILE=str(root / "no_bounds.json"),
                 CONTROLLER_RULES_FILE=str(root / "no_rules.json"),
                 **overrides)


class ControllerArmsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.ran = run03_plugin(root, EXPERIMENT_ARMS="native hold rules",
                               PLUGIN_PLACEHOLDERS="1")
        cls.cell = root / "results" / "1M" / "T2"
        cls.scored = run04(root / "results")
        cls.plugin = root / "librl_controller.so"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def metadata(self, arm):
        return dict(line.split("=", 1) for line in
                    (self.cell / arm / "metadata.env").read_text().splitlines())

    def test_the_arms_run_are_checked_and_scored(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stderr)
        self.assertEqual(self.scored.returncode, 0, self.scored.stderr)
        for arm in ("hold", "rules"):
            report = json.loads((self.cell / arm / "plugin_check.json")
                                .read_text())
            self.assertEqual(report["verdict"], "passed", arm)
        rows = summary(self.cell.parents[1])
        self.assertEqual({rows[arm]["settle_ok"] for arm in rows}, {"1"})

    def test_each_arm_gets_its_composed_config(self):
        command = (self.cell / "rules" / "command.txt").read_text()
        self.assertIn(f"--rl_plugin={self.plugin}", command)
        config_path = self.cell / "rules" / "plugin_config.json"
        self.assertIn(f"--rl_plugin_config={config_path}", command)
        config = json.loads(config_path.read_text())
        self.assertEqual(config["mode"], "rules")
        self.assertEqual(config["decision_log"],
                         str(self.cell / "rules" / "decisions.jsonl"))
        hold = json.loads((self.cell / "hold" / "plugin_config.json")
                          .read_text())
        self.assertEqual(hold["mode"], "hold-only")
        self.assertNotIn("--rl_plugin",
                         (self.cell / "native" / "command.txt").read_text())
        self.assertIn("epsilon", self.metadata("rules")["plugin_placeholders"])

    def test_the_fingerprint_carries_the_plugin_and_its_config(self):
        options = {arm: select.parse_fingerprint_options(
            self.metadata(arm)["experiment_fingerprint"])
            for arm in ("native", "hold", "rules")}
        self.assertIsNone(options["native"]["plugin_sha256"])
        self.assertEqual(options["hold"]["plugin_sha256"],
                         self.metadata("hold")["plugin_sha256"])
        self.assertEqual(options["rules"]["plugin_config_sha256"],
                         self.metadata("rules")["plugin_config_sha256"])
        self.assertNotEqual(options["hold"]["plugin_config_sha256"],
                            options["rules"]["plugin_config_sha256"])

    def test_a_failed_plugin_check_stops_the_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03_plugin(Path(tmp), EXPERIMENT_ARMS="rules",
                               PLUGIN_PLACEHOLDERS="1", STUB_PLUGIN_MASKED="1")
            self.assertEqual(ran.returncode, 8, ran.stderr)
            arm = Path(tmp) / "results" / "1M" / "T2" / "rules"
            self.assertTrue((arm / "FAILED_PLUGIN_CHECK").exists())

    def test_level_reads_reach_the_hold_arm_and_stay_out_of_the_fingerprint(self):
        """LEVEL_READS=1: the hold arm's config carries level_reads 1 and its
        window (LEVEL_READS_OPS, config.sh's 64000), the run records both,
        28 judges the plugin's lines against the host log, and the
        fingerprint is the run's without it (logging only). Off, the config
        has level_reads 0 and the check is not reported. A bad value is
        refused before any run; a wrong total fails the plugin check."""
        runs = {}
        for flag in ("0", "1"):
            with tempfile.TemporaryDirectory() as tmp:
                ran = run03_plugin(Path(tmp), EXPERIMENT_ARMS="hold",
                                   PLUGIN_PLACEHOLDERS="1", LEVEL_READS=flag)
                self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
                arm = Path(tmp) / "results" / "1M" / "T2" / "hold"
                runs[flag] = (json.loads((arm / "plugin_config.json").read_text()),
                              dict(line.split("=", 1) for line in
                                   (arm / "metadata.env").read_text().splitlines()),
                              json.loads((arm / "plugin_check.json").read_text()))
        (off, off_meta, off_check), (on, on_meta, on_check) = runs["0"], runs["1"]
        self.assertEqual(off["level_reads"], 0)
        self.assertNotIn("level_reads_ops", off)
        self.assertEqual((on["level_reads"], on["level_reads_ops"]), (1, 64000))
        self.assertEqual((off_meta["level_reads"], on_meta["level_reads"]), ("0", "1"))
        self.assertNotIn("level_reads_ops", off_meta)
        self.assertEqual(on_meta["level_reads_ops"], "64000")
        self.assertEqual((off_check["verdict"], on_check["verdict"]), ("passed", "passed"),
                         on_check)
        self.assertNotIn("level_reads", off_check["checks"])
        judged = on_check["checks"]["level_reads"]
        self.assertEqual((judged["windows"], len(judged["compared"])), (2, 7))  # schema 2 log
        for key in ("experiment_fingerprint", "plugin_config_sha256"):
            self.assertEqual(on_meta[key], off_meta[key], key)
        for overrides, message in (({"LEVEL_READS": "2"}, "LEVEL_READS must be 0 or 1"),
                                   ({"LEVEL_READS": "1", "LEVEL_READS_OPS": "999"},
                                    "LEVEL_READS_OPS must be")):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as tmp:
                ran = run03_plugin(Path(tmp), EXPERIMENT_ARMS="hold",
                                   PLUGIN_PLACEHOLDERS="1", **overrides)
                self.assertEqual(ran.returncode, 1, ran.stdout)
                self.assertIn(message, ran.stderr)
                self.assertFalse((Path(tmp) / "results" / "1M" / "T2" / "hold").exists())
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03_plugin(Path(tmp), EXPERIMENT_ARMS="hold", PLUGIN_PLACEHOLDERS="1",
                               LEVEL_READS="1", STUB_LEVEL_READS_BAD="1")
            self.assertEqual(ran.returncode, 8, ran.stderr[-3000:])
            arm = Path(tmp) / "results" / "1M" / "T2" / "hold"
            self.assertTrue((arm / "FAILED_PLUGIN_CHECK").exists())
            report = json.loads((arm / "plugin_check.json").read_text())
            self.assertEqual(report["failed_checks"], ["level_reads"])

    def test_refusals_before_any_run(self):
        cases = [
            ({}, "lacks: m_min (D-18 action bounds)"),
            # A hold-only matrix may take placeholders at full length (D-31's
            # step runs; tests/test_step_chain.py); another arm may not.
            ({"EXPERIMENT_ARMS": "rules", "PLUGIN_PLACEHOLDERS": "1",
              "WORKLOAD_SIZES_M": "29", "LOAD_PERCENT": "10"},
             "preflight's smoke runs only"),
            ({"PLUGIN_PLACEHOLDERS": "yes"}, "must be 0 or 1"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run03_plugin(Path(tmp), **{"EXPERIMENT_ARMS": "hold",
                                                     **overrides})
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "db").exists())
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), EXPERIMENT_ARMS="hold",
                        CONTROLLER_PLUGIN=str(Path(tmp) / "missing.so"))
            self.assertEqual(ran.returncode, 1)
            self.assertIn("13's step 1 builds the plugin", ran.stderr)


ROOT = PIPELINE.parents[1]
VENV = ROOT / ".venv-dbbench"


def venv_has_torch() -> bool:
    python = VENV / "bin" / "python"
    return python.exists() and subprocess.run(
        [str(python), "-c", "import torch"], capture_output=True).returncode == 0


# The host log fixture predates the step counters (schema 2): the turn
# trainer's pricer reads them, so this stub adds them to its copy, the
# header's names and each job record's cumulative counts.
FG = ["rocksdb.point.sst.probe", "rocksdb.bloom.filter.full.positive",
      "rocksdb.sorted.run.seek", "rocksdb.read.table.reopen",
      "rocksdb.number.db.next.found", "rocksdb.number.iter.skip",
      "rocksdb.number.keys.read", "rocksdb.number.db.seek",
      "rocksdb.number.keys.written", "rocksdb.read.table.reopen.nanos",
      "rocksdb.rl.scan.setup.nanos"]
_COPY = "cp " + shlex.quote(str(FIXTURE / "host_log.jsonl")) + ' "$log"'
TURN_STUB = PLUGIN_STUB.replace(_COPY, _COPY + f"""
python3 - "$log" <<'PY'
import json, sys
names = {json.dumps(FG)}
recs = [json.loads(line) for line in open(sys.argv[1])]
for r in recs:
    if r["type"] == "header":
        r["fg"] = names
    elif r["type"] in ("job_begin", "job_end", "flush_begin", "flush_end", "snap"):
        r["fg"] = [r["op"]] * len(names)
with open(sys.argv[1], "w") as out:
    out.writelines(json.dumps(r) + "\\n" for r in recs)
PY""")


@unittest.skipUnless(venv_has_torch(), "the turn trainer needs the venv's torch")
class TurnArmsTest(unittest.TestCase):
    """03's turn arms with the stub: each composes its config from the turn
    settings, runs the turn trainer beside db_bench and stops it after the
    plugin's stop line, and is checked by 28 in mode turn."""

    def run_turn(self, tmp, **overrides):
        from tests.test_plugin_config import PRICES6
        prices = Path(tmp) / "prices6.json"
        prices.write_text(json.dumps(PRICES6))
        return run03_plugin(Path(tmp), stub=TURN_STUB, EXPERIMENT_ARMS="turn turn_vdn",
                            PLUGIN_PLACEHOLDERS="1", DIAGNOSTIC_RUN="1",
                            CONTROLLER_QBAR_STANDIN="50000",
                            PRICES_FILE=str(prices), PYTHON_VENV=str(VENV),
                            LEARNER_STOP_TIMEOUT_SECONDS="60", **overrides)

    def test_both_turn_arms_run_their_trainer_and_pass_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp)
            self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
            cell = Path(tmp) / "results" / "1M" / "T2"
            for arm, arch in (("turn", "factored"), ("turn_vdn", "vdn")):
                config = json.loads((cell / arm / "plugin_config.json").read_text())
                self.assertEqual((config["mode"], config["turn_arch"]), ("turn", arch))
                self.assertEqual(config["weights_path"], str(cell / arm / "weights.bin"))
                self.assertEqual(config["turn_decay"], 1)   # DECAY's default
                self.assertEqual(config["turn_prior"], 0)   # PRIOR's default
                self.assertIn("turn_decay=1", (cell / arm / "metadata.env")
                              .read_text().splitlines())
                report = json.loads((cell / arm / "plugin_check.json").read_text())
                self.assertEqual(report["verdict"], "passed", (arm, report))
                self.assertEqual(report["checks"]["turn"]["turns"], 2)
                self.assertEqual(report["checks"]["act3"]["faithful"], 1)
                summary = json.loads((cell / arm / "turn_trainer_summary.json")
                                     .read_text())
                self.assertEqual((summary["arch"], summary["lines"]), (arch, 2))
                self.assertEqual(summary["why"], "plugin stop")

    def test_decay_off_reaches_the_plugin_and_a_bad_flag_is_refused(self):
        """DECAY=0: the turn arms' configs carry
        turn_decay 0 and the run records it; anything but 0 or 1 is refused
        before any run."""
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp, DECAY="0")
            self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
            cell = Path(tmp) / "results" / "1M" / "T2"
            for arm in ("turn", "turn_vdn"):
                config = json.loads((cell / arm / "plugin_config.json").read_text())
                self.assertEqual(config["turn_decay"], 0)
                meta = (cell / arm / "metadata.env").read_text().splitlines()
                self.assertIn("turn_decay=0", meta)
                report = json.loads((cell / arm / "plugin_check.json").read_text())
                self.assertEqual(report["verdict"], "passed", (arm, report))
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp, DECAY="2")
            self.assertEqual(ran.returncode, 1, ran.stderr[-3000:])
            self.assertIn("DECAY must be 0 or 1", ran.stderr)
            self.assertFalse((Path(tmp) / "results" / "1M" / "T2" / "turn").exists())

    def test_the_l0_prior_reaches_the_plugin_and_a_bad_flag_is_refused(self):
        """PRIOR=1: the turn arms' configs carry turn_prior 1 and the run
        records it (0 by default, above); anything but 0 or 1 is refused
        before any run."""
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp, PRIOR="1")
            self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
            cell = Path(tmp) / "results" / "1M" / "T2"
            for arm in ("turn", "turn_vdn"):
                config = json.loads((cell / arm / "plugin_config.json").read_text())
                self.assertEqual(config["turn_prior"], 1)
                meta = (cell / arm / "metadata.env").read_text().splitlines()
                self.assertIn("turn_prior=1", meta)
                report = json.loads((cell / arm / "plugin_check.json").read_text())
                self.assertEqual(report["verdict"], "passed", (arm, report))
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp, PRIOR="2")
            self.assertEqual(ran.returncode, 1, ran.stderr[-3000:])
            self.assertIn("PRIOR must be 0 or 1", ran.stderr)
            self.assertFalse((Path(tmp) / "results" / "1M" / "T2" / "turn").exists())

    def test_the_l0_bill_and_compact_floor_reach_the_plugin_and_bad_flags_are_refused(self):
        """L0_BILL=1 (with an L0-only turn settings file) and COMPACT_FLOOR=1:
        the turn arms' configs carry turn_l0_bill 1 and turn_compact_floor 1,
        the run records both, and the trainer takes the bill (0 by default);
        L0_BILL=1 with the shipped five acting levels, or a flag other than 0
        or 1, is refused before any run."""
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp)
            self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
            for arm in ("turn", "turn_vdn"):
                arm_dir = Path(tmp) / "results" / "1M" / "T2" / arm
                config = json.loads((arm_dir / "plugin_config.json").read_text())
                self.assertEqual((config["turn_l0_bill"], config["turn_compact_floor"]), (0, 0))
                meta = (arm_dir / "metadata.env").read_text().splitlines()
                self.assertIn("turn_l0_bill=0", meta)
                self.assertIn("turn_compact_floor=0", meta)
        shipped = json.loads((ROOT / "config" / "turn_settings.json").read_text())
        shipped["plugin"]["turn_acting"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "turn_settings_l0.json"
            settings.write_text(json.dumps(shipped))
            ran = self.run_turn(tmp, L0_BILL="1", COMPACT_FLOOR="1",
                                TURN_SETTINGS_FILE=str(settings))
            self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
            for arm in ("turn", "turn_vdn"):
                arm_dir = Path(tmp) / "results" / "1M" / "T2" / arm
                config = json.loads((arm_dir / "plugin_config.json").read_text())
                self.assertEqual((config["turn_l0_bill"], config["turn_compact_floor"],
                                  config["turn_acting"]), (1, 1, 1))
                meta = (arm_dir / "metadata.env").read_text().splitlines()
                self.assertIn("turn_l0_bill=1", meta)
                self.assertIn("turn_compact_floor=1", meta)
                report = json.loads((arm_dir / "plugin_check.json").read_text())
                self.assertEqual(report["verdict"], "passed", (arm, report))
                # The trainer took the L0-only config and ran to the plugin's stop
                # (the stub's one later line ends past the drain, so none is priced).
                summary = json.loads((arm_dir / "turn_trainer_summary.json").read_text())
                self.assertEqual((summary["why"], summary["lines"]), ("plugin stop", 2))
        cases =[({"L0_BILL": "1"}, "turn_acting 1"),
                 ({"L0_BILL": "2"}, "L0_BILL must be 0 or 1"),
                 ({"COMPACT_FLOOR": "yes"}, "COMPACT_FLOOR must be 0 or 1")]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = self.run_turn(tmp, **overrides)
                    self.assertEqual(ran.returncode, 1, ran.stderr[-3000:])
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "results" / "1M" / "T2" / "turn").exists())

    def test_level_reads_reach_the_turn_arms(self):
        """LEVEL_READS=1: the turn arms' configs carry level_reads 1 and no
        window (the turn is it), the run records it, 28 judges the turn
        lines' blocks against the host log, the trainer runs as before, and
        the fingerprint is the run's without it; with L0_BILL=1 too, the
        blocks' L0 entries are checked against l0_read."""
        shipped = json.loads((ROOT / "config" / "turn_settings.json").read_text())
        shipped["plugin"]["turn_acting"] = 1
        meta = {}
        for flags in ({"LEVEL_READS": "0"}, {"LEVEL_READS": "1"},
                      {"LEVEL_READS": "1", "L0_BILL": "1"}):
            with tempfile.TemporaryDirectory() as tmp:
                settings = Path(tmp) / "turn_settings_l0.json"
                settings.write_text(json.dumps(shipped))
                ran = self.run_turn(tmp, TURN_SETTINGS_FILE=str(settings), **flags)
                self.assertEqual(ran.returncode, 0, ran.stderr[-3000:])
                for arm in ("turn", "turn_vdn"):
                    arm_dir = Path(tmp) / "results" / "1M" / "T2" / arm
                    config = json.loads((arm_dir / "plugin_config.json").read_text())
                    self.assertEqual(config["level_reads"], int(flags["LEVEL_READS"]))
                    self.assertNotIn("level_reads_ops", config)
                    lines = dict(line.split("=", 1) for line in
                                 (arm_dir / "metadata.env").read_text().splitlines())
                    self.assertEqual(lines["level_reads"], flags["LEVEL_READS"])
                    self.assertNotIn("level_reads_ops", lines)
                    report = json.loads((arm_dir / "plugin_check.json").read_text())
                    self.assertEqual(report["verdict"], "passed", (arm, flags, report))
                    self.assertEqual("level_reads" in report["checks"],
                                     flags["LEVEL_READS"] == "1")
                    summary = json.loads((arm_dir / "turn_trainer_summary.json").read_text())
                    self.assertEqual((summary["why"], summary["lines"]), ("plugin stop", 2))
                    meta[(arm, tuple(flags.items()))] = lines
        for arm in ("turn", "turn_vdn"):
            self.assertEqual(meta[(arm, (("LEVEL_READS", "0"),))]["experiment_fingerprint"],
                             meta[(arm, (("LEVEL_READS", "1"),))]["experiment_fingerprint"])

    def test_a_masked_turn_action_fails_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ran = self.run_turn(tmp, STUB_PLUGIN_MASKED="1")
            self.assertEqual(ran.returncode, 8, ran.stderr[-3000:])
            report = json.loads((Path(tmp) / "results" / "1M" / "T2" / "turn" /
                                 "plugin_check.json").read_text())
            self.assertIn("turn", report["failed_checks"])


if __name__ == "__main__":
    unittest.main()
