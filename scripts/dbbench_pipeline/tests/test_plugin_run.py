"""28_check_plugin_run.py on synthetic plugin logs, each check failing on the
defect it names; and 03's hold and rules arms end to end with a stub
db_bench that writes those logs: the config composed and passed, the
fingerprint's plugin segment parsing in fingerprint.py, the arm checked and scored, and
the refusals before any run."""
import importlib.util
import json
import shlex
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


# The stub db_bench of test_run_experiments, plus the plugin's part: with
# --rl_plugin_config it writes the logs the config names. STUB_PLUGIN_MASKED=1
# makes its one decision take a masked action.
PLUGIN_STUB = STUB.replace(
    "cat " + shlex.quote(str(FIXTURE / "run.log")), f"""
for a in "$@"; do
  case "$a" in --rl_plugin_config=*) config="${{a#*=}}" ;; esac
done
cat {shlex.quote(str(FIXTURE / "run.log"))}
if [[ -n "${{config:-}}" ]]; then
  python3 - "$config" "${{STUB_PLUGIN_MASKED:-0}}" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
mask = [1, 0, 0, 1] if sys.argv[2] == "1" else [1, 0, 1, 1]
mode = config["mode"]
lines = [{{"type": "start", "mode": mode, "num_levels": 4}}]
if mode == "rules":
    lines += [{{"type": "decision", "id": 1, "level": 1, "op": 10,
                "mode": mode, "action": "defer", "mask": mask, "old": 1,
                "requested": 0.9}},
              {{"type": "apply", "op": 10, "first_id": 1, "last_id": 1,
                "m": [1, 0.9, 1, 1], "k0": 4, "ok": 1, "seen": 1}}]
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


def run03_plugin(root: Path, **overrides):
    build = root / "build"
    build.mkdir(parents=True)
    (build / "db_bench").write_text(PLUGIN_STUB)
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

    def test_refusals_before_any_run(self):
        cases = [
            ({}, "lacks: m_min (D-18 action bounds)"),
            ({"PLUGIN_PLACEHOLDERS": "1", "WORKLOAD_SIZES_M": "29",
              "LOAD_PERCENT": "10"}, "preflight's smoke runs only"),
            ({"PLUGIN_PLACEHOLDERS": "yes"}, "must be 0 or 1"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as tmp:
                    ran = run03_plugin(Path(tmp), EXPERIMENT_ARMS="hold",
                                       **overrides)
                    self.assertEqual(ran.returncode, 1, ran.stdout)
                    self.assertIn(message, ran.stderr)
                    self.assertFalse((Path(tmp) / "db").exists())
        with tempfile.TemporaryDirectory() as tmp:
            ran = run03(Path(tmp), EXPERIMENT_ARMS="hold",
                        CONTROLLER_PLUGIN=str(Path(tmp) / "missing.so"))
            self.assertEqual(ran.returncode, 1)
            self.assertIn("13's step 1 builds the plugin", ran.stderr)


if __name__ == "__main__":
    unittest.main()
