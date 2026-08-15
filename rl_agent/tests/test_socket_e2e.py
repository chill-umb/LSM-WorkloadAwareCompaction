"""End-to-end test: the real server.py over a real Unix socket, driven by a
fake C++ client speaking protocol v2.

This is the test that would have caught the 2026-07-19 protocol bug, where the
C++ response parser's needle never matched what json.dumps emitted and every
RL run silently fell back to leveled compaction. It asserts on the exact bytes
on the wire, not just on Python-side behaviour.

    .venv/bin/python3 rl_agent/tests/test_socket_e2e.py
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

RL_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(RL_AGENT_DIR)
PYTHON_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
if not os.path.exists(PYTHON_BIN):
    PYTHON_BIN = sys.executable


def level_entry(level=0, files=2, default_needed=False, is_last=False,
                defer_count=0, prev_action_executed=0):
    return {
        "level": level, "files": files, "bytes": files * (1 << 22),
        "score": files / 4.0, "target_bytes": 0 if level == 0 else (1 << 26),
        "next_level_files": 4, "next_level_bytes": 1 << 25,
        "next_level_score": 0.5, "next_level_target_bytes": 1 << 26,
        "overlap_bytes": 1 << 24, "bytes_in": 1 << 22,
        "bytes_read_out": 1 << 21, "bytes_written_out": 1 << 21,
        "compactions_from": 1, "compactions_scheduled": 1,
        "compactions_forced": 1,
        "prev_action_executed": prev_action_executed,
        "prev_action_overridden": False, "prev_compaction_picked": True,
        "defer_count": defer_count, "default_needed": default_needed,
        "is_last": is_last,
    }


def v2_message(levels, done=False, interval_micros=50_000):
    return {
        "version": 2, "pending_compaction_bytes": 1 << 20,
        "flushed_bytes": 1 << 22, "compaction_bytes_read": 1 << 21,
        "compaction_bytes_written": 1 << 21, "compactions_completed": 1,
        "stall_count": 0, "stop_count": 0, "l0_compaction_trigger": 4,
        "l0_slowdown_trigger": 20, "l0_stop_trigger": 36,
        "l0_delay_trigger_count": 0, "interval_micros": interval_micros,
        "keys_read": 1000, "seeks": 50, "get_hit_l0": 300, "get_hit_l1": 400,
        "get_hit_l2_and_up": 300, "bloom_useful": 900,
        "non_last_level_read_count": 700, "last_level_read_count": 300,
        "done": done, "levels": levels,
    }


class ServerHarness:
    """Runs server.py as a subprocess against a temporary socket and log set."""

    def __init__(self, **env_overrides):
        self.tmpdir = tempfile.mkdtemp(prefix="rl_e2e_")
        self.socket_path = os.path.join(self.tmpdir, "rl.sock")
        self.metrics_path = os.path.join(self.tmpdir, "metrics.jsonl")
        self.io_path = os.path.join(self.tmpdir, "io.jsonl")
        self.env = dict(os.environ)
        self.env.update({
            "RL_COMPACTION_SOCKET_PATH": self.socket_path,
            "RL_MODEL_SAVE_PATH": os.path.join(self.tmpdir, "model.pt"),
            "RL_METRICS_LOG_PATH": self.metrics_path,
            "RL_IO_LOG_PATH": self.io_path,
            "RL_SEED": "1",
            "OMP_NUM_THREADS": "1",
        })
        self.env.update({k: str(v) for k, v in env_overrides.items()})
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [PYTHON_BIN, "server.py"], cwd=RL_AGENT_DIR, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(self.socket_path):
                return self
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"server exited early:\n{self.proc.stdout.read()}")
            time.sleep(0.05)
        raise RuntimeError("server did not create its socket in time")

    def __exit__(self, *exc):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        return False

    def connect(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10)
        client.connect(self.socket_path)
        return client

    @staticmethod
    def _read_jsonl(path):
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def read_metrics(self):
        return self._read_jsonl(self.metrics_path)

    def read_io_log(self):
        return self._read_jsonl(self.io_path)

    def wait_for_records(self, count, timeout=15):
        """Logging happens deliberately after the response is sent, so tests
        must wait for it rather than assume it landed. Polling instead of
        sleeping keeps this from going flaky on a loaded machine."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            records = self.read_metrics()
            if len(records) >= count:
                return records
            time.sleep(0.05)
        return self.read_metrics()


def send_and_receive(client, message):
    client.sendall((json.dumps(message) + "\n").encode())
    buffer = b""
    while not buffer.endswith(b"\n"):
        chunk = client.recv(4096)
        if not chunk:
            raise RuntimeError("server closed the connection")
        buffer += chunk
    return buffer.decode()


class TestSocketEndToEnd(unittest.TestCase):

    def test_response_is_parseable_by_the_cpp_parser(self):
        """The C++ ParseIntArrayField scans for the literal `"actions"` then
        skips whitespace to ':' and '['. Assert on raw bytes: a silent
        mismatch here degrades every RL run to plain leveled compaction."""
        with ServerHarness() as server:
            client = server.connect()
            raw = send_and_receive(
                client, v2_message([level_entry(0), level_entry(1, is_last=True)]))
            self.assertTrue(raw.endswith("\n"), "response must be newline-framed")
            self.assertRegex(raw, r'"actions"\s*:\s*\[')
            match = re.search(r'"actions"\s*:\s*\[([^\]]*)\]', raw)
            self.assertIsNotNone(match)
            actions = [int(x) for x in match.group(1).split(",") if x.strip()]
            self.assertEqual(len(actions), 2, "one action per requested level")
            self.assertTrue(all(a in (0, 1) for a in actions))
            self.assertNotIn("candidate_file_numbers", json.loads(raw),
                             "the trigger policy must never select SST files")
            client.close()

    def test_action_order_matches_request_order(self):
        with ServerHarness() as server:
            client = server.connect()
            levels = [level_entry(0), level_entry(2), level_entry(5, is_last=True)]
            raw = send_and_receive(client, v2_message(levels))
            self.assertEqual(len(json.loads(raw)["actions"]), 3)
            client.close()

            metrics = server.wait_for_records(3)
            logged = [m["level"] for m in metrics]
            self.assertEqual(logged, [0, 2, 5])

    def test_new_protocol_fields_reach_the_agent(self):
        """interval_micros, read tickers, defer_count and prev_action_executed
        must all survive the wire and land in the logged state."""
        with ServerHarness() as server:
            client = server.connect()
            send_and_receive(client, v2_message([level_entry(0, defer_count=3)]))
            send_and_receive(client, v2_message(
                [level_entry(0, defer_count=4, prev_action_executed=1)]))
            client.close()
            server.wait_for_records(2)

            entries = server.read_io_log()
            self.assertGreaterEqual(len(entries), 2)
            latest = entries[-1]
            self.assertEqual(latest["input"]["defer_count"], 4)
            self.assertEqual(latest["diagnostics"]["executed_action"], 1)
            self.assertIsNotNone(latest["diagnostics"]["dt_seconds"])
            self.assertGreater(latest["diagnostics"]["dt_seconds"], 0.0)

            features = server.read_metrics()[-1]["state_features"]
            # file_reads_per_op_norm replaced non_last_read_fraction, which was
            # the ratio between the non-last and last level classes and sat at
            # p50 = 1.00 for entire runs.
            for field in ("read_rate_norm", "l0_hit_fraction",
                          "file_reads_per_op_norm", "defer_norm"):
                self.assertIn(field, features)
            self.assertGreater(features["l0_hit_fraction"], 0.0)
            # The read-path audit trail must survive the round trip: the
            # globals were never logged, which is how a constant feature went
            # unnoticed across every run in the changelog.
            components = server.read_io_log()[-1]["reward_components"]
            for field in ("read_gets", "read_seeks", "read_file_reads_per_op",
                          "read_exposure_level"):
                self.assertIn(field, components)

    def test_analytic_prior_is_reported_by_default(self):
        with ServerHarness() as server:
            client = server.connect()
            for _ in range(3):
                send_and_receive(client, v2_message([level_entry(0, files=8)]))
            client.close()
            server.wait_for_records(3)

            diagnostics = server.read_io_log()[-1]["diagnostics"]
            self.assertIsNotNone(diagnostics["analytic_advantage"],
                                 "prior must be on by default")
            self.assertIsNotNone(diagnostics["residual_advantage"])

    def test_done_message_finalises_pending_credit(self):
        with ServerHarness(RL_CREDIT_HORIZON_MS=10 ** 6) as server:
            client = server.connect()
            for _ in range(4):
                send_and_receive(client, v2_message([level_entry(0)]))
            send_and_receive(client, v2_message([level_entry(0)], done=True))
            client.close()
            server.wait_for_records(5)

            entries = server.read_io_log()
            finalized = [len(e["diagnostics"]["finalized_returns"])
                         for e in entries]
            self.assertGreater(sum(finalized), 0,
                               "done must finalise the open credit windows")

    def test_legacy_single_level_protocol_still_served(self):
        """Scope boundary: the old protocol is not extended, but must not
        break — old binaries share the socket."""
        with ServerHarness() as server:
            client = server.connect()
            raw = send_and_receive(client, {
                "l0_files": 3, "l0_size_bytes": 1 << 24, "l0_score": 0.75,
                "pending_compaction_bytes": 1 << 20, "flushed_bytes": 1 << 22,
                "compaction_bytes_read": 0, "compaction_bytes_written": 0,
                "l0_compactions_completed": 0, "stall_count": 0,
                "l0_compaction_trigger": 4, "done": False,
            })
            self.assertIn("action", json.loads(raw))
            client.close()

    def test_reconnect_preserves_processor_state(self):
        """A socket timeout reconnects; normalizer scales and prev-state must
        survive it, or every reconnect resets the agent's world model."""
        with ServerHarness() as server:
            first = server.connect()
            for _ in range(3):
                send_and_receive(first, v2_message([level_entry(0, files=6)]))
            first.close()
            server.wait_for_records(3)

            second = server.connect()
            send_and_receive(second, v2_message([level_entry(0, files=6)]))
            second.close()
            server.wait_for_records(4)

            steps = [m["step"] for m in server.read_metrics()]
            self.assertEqual(steps, sorted(steps))
            self.assertGreaterEqual(max(steps), 4,
                                    "step counter must continue across reconnect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
