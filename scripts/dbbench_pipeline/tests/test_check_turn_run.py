"""37_check_turn_run.py on a synthetic turn arm: the pushes counted, the
plugin's logged scores recomputed from the archived weights of their version
(ARCH-6), a changed score or greedy choice failing it, a score that is not
finite failing ARCH-1, the L0 bill's records (turn_l0_bill) checked both
ways, the per-level read log (level_reads) checked against the host log,
and the SetOptions price measured from the calls' wall time."""
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
ROOT = PIPELINE.parents[1]
sys.path.insert(0, str(ROOT / "rl_agent"))
from learner import turn_weights as TW  # noqa: E402

spec = importlib.util.spec_from_file_location("check_turn_run",
                                              PIPELINE / "37_check_turn_run.py")
check37 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check37)

ACTING = 5
N_IN = len(TW.feature_names(ACTING))


def net(version, seed, spread=1.0, acting=ACTING):
    n_in = len(TW.feature_names(acting))

    def v(i, j):
        return round(math.sin(seed * (3 * i + 7 * j + 1)) * 0.3, 6)
    layers = [([[v(r, c) for c in range(n_in)] for r in range(6)], [v(r, 99) for r in range(6)]),
              ([[v(r + 50, c) for c in range(6)] for r in range(1 + 4 * acting)],
               [v(r, 77) for r in range(1 + 4 * acting)])]
    return TW.TurnWeights(version=version, arch=0, acting=acting, clip=5.0,
                          mean=[0.0] * n_in, scale=[1.0] * n_in,
                          nets=[TW.Net(list(range(n_in)), layers)], spread=spread)


# Cost model 2's plugin keys at round prices, for the L0 bill's check.
KINDS = ("flush", "l0", "deep", "move")
TYPES = ("probe", "block", "seek", "reopen", "step", "iblock", "memtable",
         "get0", "scan0", "put")
CM2 = {"cost_model": 2, "c_w": 2.0, "c_f": 100.0, "c_blk": 1000.0, "c_sk": 2000.0,
       "c_open": 5000.0, "c_s": 0.001, "q_bar": 1000.0, "beta_w": 1.0, "beta_r": 2.5,
       "beta_s": 1.0, "c_cr": 0.5, "c_st": 10.0, "c_ib": 0.0, "c_mt": 20.0,
       "c_get0": 30.0, "c_sc0": 40.0, "c_put": 50.0, "p_dev": 1e9, "lambda": 0.25,
       "n_win": 1000, **{f"c_job_{k}": 1000.0 for k in KINDS},
       **{f"kappa_b_{x}": 1e-9 for x in TYPES},
       **{f"kappa_j_{x}_{k}": 0.0 for x in TYPES for k in KINDS}}


def l0_read(t):
    return {"probes": 700 + t, "filter_passes": 30, "filter_hits": 9, "seeks": 50 + t,
            "get_reopens": 2, "iter_reopens": 1, "reopen_nanos": 12345, "hidden_steps": 40,
            "l1_hidden_steps": 60}


def bill_read(t):
    n = l0_read(t)
    return CM2["beta_r"] * (100.0 * n["probes"] + 1000.0 * n["filter_passes"] +
                            2000.0 * n["seeks"] + 5000.0 * (n["get_reopens"] + n["iter_reopens"]) +
                            10.0 * (n["hidden_steps"] + n["l1_hidden_steps"]))


LR_KEYS = check37.level_reads.KEYS
LEVELS = 3


def reads_block(t, bill=False):
    """Turn t's level_reads block over LEVELS levels; with the L0 bill, its
    L0 entries and L1's hidden steps are l0_read(t)'s, as the plugin's are."""
    block = {key: [(k + 1) * (i + 1) * (t + 2) for i in range(LEVELS)]
             for k, key in enumerate(LR_KEYS)}
    if bill:
        for key in LR_KEYS:
            block[key][0] = l0_read(t)[key]
        block["hidden_steps"][1] = l0_read(t)["l1_hidden_steps"]
    return block


def write_arm(root: Path, tamper=None, prior=None, bill=None, reads=None):
    """prior: the L0 prior's four values (J per turn) on every line, with
    the weights' spread 2, as the plugin logs them with turn_prior 1. bill:
    an L0-only arm (turn_acting 1) with turn_l0_bill `bill`; with 1, its
    turn lines carry l0_read and the trainer's lines the bill, as the plugin
    and the trainer log them. reads: the config's level_reads; with it set,
    the lines are turns [1000 t, 1000 (t + 1)) from the start (op 0) and a
    final line at the drain (6000-6500), and with 1 each carries its
    reads_block and host_log.jsonl's measure_start..drain_start counters
    are their totals."""
    acting = ACTING if bill is None else 1
    n_in = len(TW.feature_names(acting))
    (root / "weights").mkdir(parents=True)
    spread = 2.0 if prior else 1.0
    nets = {v: net(v, 0.3 + 0.2 * v, spread, acting) for v in (1, 2)}
    for v, w in nets.items():
        (root / "weights" / f"turn.v{v}.bin").write_bytes(TW.encode(w))
    config = {"mode": "turn", "turn_arch": "factored", "turn_acting": acting}
    if bill is not None:
        config.update(CM2, turn_l0_bill=bill)
    if reads is not None:
        config["level_reads"] = reads
    (root / "plugin_config.json").write_text(json.dumps(config))
    names = TW.feature_names(acting)
    lines = [{"type": "start", "mode": "turn"},
             {"type": "turn_weights", "version": 1, "arch": "factored"},
             {"type": "turn_weights", "version": 2, "arch": "factored"}]
    masks = [[1, 1, 1, 1], [1, 0, 1, 1], [1, 1, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]]
    trainer = [{"type": "train", "t": 4, "loss": 0.5}]
    for t in range(6):
        x = [0.1 * ((i * 5 + t) % 9) - 0.3 for i in range(n_in)]
        v = 0 if t == 0 else 1 if t < 3 else 2
        scores = TW.forward(nets[v], x) if v else [[0.0] * 4 for _ in range(acting)]
        acted = [list(level) for level in scores]
        if prior:
            acted[0] = [a - b / spread for a, b in zip(acted[0], prior)]
        line = {"type": "turn", "t": t, "final": 0, "weights": v,
                "x": dict(zip(names, x)), "masks": masks, "scores": scores,
                "greedy": check37.greedy(acted, masks)}
        if prior:
            line.update(prior=list(prior), prior_spread=spread if v else 1.0)
        if bill:
            line["l0_read"] = l0_read(t)
        if reads is not None:
            line.update(op0=1000 * t, op1=1000 * (t + 1))
        if reads:
            line["level_reads"] = reads_block(t, bool(bill))
        if t and bill is not None:
            record = {"type": "turn", "t": t, "J": 1e6, "J_reward": 1e6}
            if bill:
                record.update(bill_read=bill_read(t), bill_merge=3e5 * (t % 2),
                              J_reward=bill_read(t) + 3e5 * (t % 2))
            trainer.append(record)
        if tamper:
            tamper(line)
        lines.append(line)
        lines.append({"type": "apply", "ok": 1, "apply_us": 1000 + 100 * t})
    if reads is not None:
        lines[0].update(op=0, num_levels=LEVELS)
        final = {"type": "turn", "t": 6, "final": 1, "op0": 6000, "op1": 6500,
                 "x": dict(zip(names, [0.0] * n_in)), "masks": masks}
        if bill:
            final["l0_read"] = l0_read(6)
        if reads:
            final["level_reads"] = reads_block(6, bool(bill))
        lines += [{"type": "drain", "op": 6500}, final]
        blocks = [r["level_reads"] for r in lines if "level_reads" in r]
        start = [[11] * len(LR_KEYS) for _ in range(LEVELS)]
        end = [[11 + sum(b[key][i] for b in blocks) for key in LR_KEYS]
               for i in range(LEVELS)]
        (root / "host_log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
            {"type": "header", "schema": 3},
            {"type": "stamp", "name": "measure_start", "op": 0, "levels": start},
            {"type": "stamp", "name": "drain_start", "op": 6500, "levels": end})))
    lines.append({"type": "stop"})
    (root / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    (root / "turn_trainer.jsonl").write_text("".join(json.dumps(r) + "\n" for r in trainer))
    (root / "turn_trainer_summary.json").write_text(json.dumps(
        {"weights_version": 2, "transitions": 5}))


PRICES = {"price_per_core_second": 2e-5, "c_w": 3e-14}


class CheckTurnRunTest(unittest.TestCase):
    def run_check(self, tamper=None, options_bytes=None, prior=None, bill=None, trainer=None,
                  reads=None, host=None):
        with tempfile.TemporaryDirectory() as tmp:
            write_arm(Path(tmp), tamper, prior, bill, reads)
            if host:
                host(Path(tmp) / "host_log.jsonl")
            if trainer:
                path = Path(tmp) / "turn_trainer.jsonl"
                records = [json.loads(x) for x in path.read_text().splitlines()]
                for r in records:
                    trainer(r)
                path.write_text("".join(json.dumps(r) + "\n" for r in records))
            return check37.check(Path(tmp), PRICES, options_bytes, min_pushes=2)

    def test_a_clean_run_passes_and_c_set_is_measured(self):
        report = self.run_check(options_bytes=7000)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)
        c = report["checks"]["c_set"]
        self.assertEqual(c["calls"], 6)
        self.assertEqual(c["median_us"], 1250)
        self.assertAlmostEqual(c["c_set"], 1250e-6 * 2e-5 + 7000 * 3e-14)

    def test_each_defect_fails_its_check(self):
        def score(line):
            if line["t"] == 4:
                line["scores"][1][2] += 1e-6
        self.assertEqual(self.run_check(score)["failed_checks"], ["arch6"])

        def choice(line):
            if line["t"] == 2:
                line["greedy"][2] = "compact" if line["greedy"][2] == "hold" else "hold"
        self.assertEqual(self.run_check(choice)["failed_checks"], ["arch6"])

        def nan(line):
            if line["t"] == 1:
                line["scores"][0][0] = None
        self.assertIn("arch1", self.run_check(nan)["failed_checks"])

        def version(line):
            if line["t"] == 5:
                line["weights"] = 9      # no archived file
        self.assertEqual(self.run_check(version)["failed_checks"], ["arch6"])
        with tempfile.TemporaryDirectory() as tmp:
            write_arm(Path(tmp))
            report = check37.check(Path(tmp), PRICES, None, min_pushes=3)
            self.assertEqual(report["failed_checks"], ["pushes"])


    def test_the_l0_prior_enters_the_greedy_check(self):
        """turn_prior 1: L0's greedy choice is its scores less the logged
        prior over the weights' spread; ignoring the prior, or a logged
        prior_spread that is not the weights', fails ARCH-6."""
        prior = [0.0, 50.0, 0.0, -50.0]       # expand far cheaper: L0 expands
        report = self.run_check(prior=prior)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)

        def raw(line):                         # the net's choice alone
            line["greedy"] = check37.greedy(line["scores"], line["masks"])
        self.assertEqual(self.run_check(raw, prior=prior)["failed_checks"], ["arch6"])

        def spread(line):
            if line["t"] == 3:
                line["prior_spread"] = 3.0
        report = self.run_check(spread, prior=prior)
        self.assertEqual(report["failed_checks"], ["arch6"])
        self.assertIn("prior_spread", " ".join(report["checks"]["arch6"]["problems"]))


    def test_the_l0_bill_records_are_checked_both_ways(self):
        """turn_l0_bill 1 (an L0-only arm): every turn line's l0_read and
        every trainer line's bill, bill_read being beta_r times the turn's
        counts at the config's prices; a line without the block, a read part
        that is not the priced counts, or a J_reward that is not the sum
        fails l0_bill. Off: no block and no bill anywhere, as before."""
        report = self.run_check(bill=1)
        self.assertEqual(report["verdict"], "passed", report)
        c = report["checks"]["l0_bill"]
        self.assertEqual((c["on"], c["turn_lines"], c["billed"]), (True, 6, 5))
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)   # one acting level

        def no_block(line):
            if line["t"] == 3:
                del line["l0_read"]
        self.assertEqual(self.run_check(no_block, bill=1)["failed_checks"], ["l0_bill"])

        def half_count(line):
            if line["t"] == 2:
                line["l0_read"]["seeks"] += 0.5
        self.assertEqual(self.run_check(half_count, bill=1)["failed_checks"], ["l0_bill"])

        def read_off(r):
            if r.get("t") == 4 and r["type"] == "turn":
                r["bill_read"] *= 1.001
                r["J_reward"] = r["bill_read"] + r["bill_merge"]
        report = self.run_check(bill=1, trainer=read_off)
        self.assertEqual(report["failed_checks"], ["l0_bill"])
        self.assertIn("priced reads", " ".join(report["checks"]["l0_bill"]["problems"]))

        def sum_off(r):
            if r.get("t") == 5 and r["type"] == "turn":
                r["J_reward"] += 1.0
        self.assertEqual(self.run_check(bill=1, trainer=sum_off)["failed_checks"], ["l0_bill"])

        # The trainer's l0_bill_written: the written part belongs to the sum; a negative one fails.
        def written(r):
            if r["type"] == "turn":
                r["bill_written"] = 2e5
                r["J_reward"] = r["bill_read"] + r["bill_merge"] + 2e5
        self.assertEqual(self.run_check(bill=1, trainer=written)["verdict"], "passed")

        def written_left_out(r):
            if r["type"] == "turn":
                r["bill_written"] = 2e5
        self.assertEqual(self.run_check(bill=1, trainer=written_left_out)["failed_checks"], ["l0_bill"])

        def written_negative(r):
            if r["type"] == "turn":
                r["bill_written"] = -1.0
                r["J_reward"] = r["bill_read"] + r["bill_merge"] - 1.0
        self.assertEqual(self.run_check(bill=1, trainer=written_negative)["failed_checks"], ["l0_bill"])

        report = self.run_check(bill=0)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertFalse(report["checks"]["l0_bill"]["on"])

        def stray(line):
            line["l0_read"] = l0_read(line["t"])
        self.assertEqual(self.run_check(stray, bill=0)["failed_checks"], ["l0_bill"])
        self.assertEqual(self.run_check(stray)["failed_checks"], ["l0_bill"])

    def test_the_level_reads_are_checked_against_the_host_log(self):
        """level_reads 1: every turn line, the drain's too, carries a whole
        block of LEVELS levels; the turns tile the run from the start to the
        drain; their totals are the host log's; with the L0 bill too, the
        L0 entries are l0_read's. A missing block, a wrong total, no host
        log, or L0 entries that are not l0_read's fail level_reads. Off: the
        report is as before; a block anyway fails."""
        report = self.run_check(reads=1)
        self.assertEqual(report["verdict"], "passed", report)
        c = report["checks"]["level_reads"]
        self.assertEqual((c["windows"], c["gaps"], c["compared"]),
                         (7, {"start": 0, "end": 0}, list(LR_KEYS)))
        self.assertEqual(report["checks"]["arch6"]["judged"], 5)   # the final line is not judged
        report = self.run_check(reads=1, bill=1)
        self.assertEqual(report["verdict"], "passed", report)
        self.assertTrue(report["checks"]["l0_bill"]["on"])

        def no_block(line):
            if line["t"] == 3:
                del line["level_reads"]
        self.assertEqual(self.run_check(no_block, reads=1)["failed_checks"], ["level_reads"])

        def other_l0(line):
            if line["t"] == 2:
                line["l0_read"]["probes"] += 1
        # The bill's own check takes the changed count; level_reads sees the
        # disagreement (and the totals still match the blocks).
        report = self.run_check(other_l0, reads=1, bill=1)
        self.assertIn("level_reads", report["failed_checks"])
        self.assertIn("not l0_read's", " ".join(report["checks"]["level_reads"]["problems"]))

        def more_probes(path):
            records = [json.loads(x) for x in path.read_text().splitlines()]
            records[2]["levels"][1][0] += 500
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
        report = self.run_check(reads=1, host=more_probes)
        self.assertEqual(report["failed_checks"], ["level_reads"])
        self.assertEqual(report["checks"]["level_reads"]["max_deviation"]["probes"], -500)
        self.assertEqual(self.run_check(reads=1, host=lambda p: p.unlink())["failed_checks"],
                         ["level_reads"])

        off = self.run_check(reads=0)
        self.assertEqual(off["verdict"], "passed", off)
        self.assertNotIn("level_reads", off["checks"])

        def stray(line):
            line["level_reads"] = reads_block(line["t"])
        self.assertEqual(self.run_check(stray, reads=0)["failed_checks"], ["level_reads"])
        self.assertEqual(self.run_check(stray)["failed_checks"], ["level_reads"])


if __name__ == "__main__":
    unittest.main()
