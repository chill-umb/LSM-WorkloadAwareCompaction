"""00_install_dependencies.sh on a fake node: a sysfs and /etc tree in a
temporary folder, and stand-ins for sudo, apt, dpkg-query, the compiler,
systemd, loginctl, git and python3 that record what they are asked and keep
the node's state in files. Checks that `check` changes nothing and names
every item that is not ready; that setting up makes each one ready (the
runtime settings and the boot unit through node_tuning.sh) and a second run
changes nothing; that setting up refuses while a db_bench runs; the pinning
check; ISOLATE_OS_CPUS; and the node record."""
import os
import pwd
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
SCRIPT = PIPELINE / "00_install_dependencies.sh"

# One stand-in, dispatched on its own name. $STATE holds the node's state,
# $CALLS every call, $SUDO_CALLS what ran as root.
STUB = r"""#!/usr/bin/env bash
name="$(basename "$0")"
echo "$name $*" >> "$CALLS"
case "$name" in
  sudo) echo "$*" >> "$SUDO_CALLS"; exec "$@" ;;
  apt-get)
    if [[ "$1" == install ]]; then
      shift
      for p in "$@"; do [[ "$p" == -* ]] || touch "$STATE/pkg-$p"; done
    fi ;;
  dpkg-query)
    package="${*: -1}"
    [[ -e "$STATE/pkg-$package" ]] || exit 1
    case "$*" in
      *Status*) printf 'install ok installed' ;;
      *) printf '1.0-stub' ;;
    esac ;;
  add-apt-repository) ;;
  update-alternatives)
    [[ "$1 $2" == "--set c++" ]] && touch "$STATE/gcc14" ;;
  c++)
    case "$1" in
      --version) [[ -e "$STATE/gcc14" ]] && echo "c++ (stub) 14.3.0" || echo "c++ (stub) 13.3.0" ;;
      -march=*) [[ -e "$STATE/gcc14" ]] || exit 1 ;;
    esac ;;
  systemctl)
    case "$1" in
      is-enabled) [[ -e "$STATE/enabled-$2" ]] && echo enabled || { echo disabled; exit 1; } ;;
      enable) touch "$STATE/enabled-$2" ;;
      show) cat "$STATE/allowedcpus" 2>/dev/null; true ;;
      set-property) echo "${4#AllowedCPUs=}" > "$STATE/allowedcpus" ;;
    esac ;;
  loginctl)
    case "$1" in
      show-user) [[ -e "$STATE/linger" ]] && echo yes || echo no ;;
      enable-linger) touch "$STATE/linger" ;;
    esac ;;
  systemd-run) ;;
  git)
    case "$*" in
      "submodule status --recursive")
        [[ -e "$STATE/submodule" ]] && echo " 5eff2f0 lib/rocksdb (heads/x)" || echo "-5eff2f0 lib/rocksdb" ;;
      "submodule update --init --recursive -- lib/rocksdb") touch "$STATE/submodule" ;;
      *rev-parse*) echo 0123abcd ;;
    esac ;;
  python3)
    if [[ "$1 $2" == "-m venv" ]]; then
      mkdir -p "$3/bin"
      cp "$0" "$3/bin/python"
    fi ;;
  python)  # the venv's
    case "$*" in
      -c*) echo "numpy 2.5.3 torch 2.14.1 matplotlib 3.11.2" ;;
      *freeze*) echo "numpy==2.5.3"; echo "torch==2.14.1" ;;
    esac ;;
esac
exit 0
"""
COMMANDS = ("sudo", "apt-get", "dpkg-query", "add-apt-repository",
            "update-alternatives", "c++", "systemctl", "loginctl",
            "systemd-run", "git", "python3")


class SetupNodeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for command in COMMANDS:
            (bin_dir / command).write_text(STUB)
            (bin_dir / command).chmod(0o755)
        # A fresh 4-CPU node: SMT on, powersave, THP always, L3 domains 0-1
        # and 2-3, the memory controller delegated.
        self.sysfs = self.root / "sys"
        cpu = self.sysfs / "devices" / "system" / "cpu"
        (cpu / "smt").mkdir(parents=True)
        (cpu / "smt" / "control").write_text("on\n")
        (cpu / "online").write_text("0-3\n")
        for n in range(4):
            (cpu / f"cpu{n}" / "cpufreq").mkdir(parents=True)
            (cpu / f"cpu{n}" / "cpufreq" / "scaling_governor").write_text("powersave\n")
            index = cpu / f"cpu{n}" / "cache" / "index3"
            index.mkdir(parents=True)
            (index / "level").write_text("3\n")
            (index / "shared_cpu_list").write_text("0-1\n" if n < 2 else "2-3\n")
        self.thp = self.sysfs / "kernel" / "mm" / "transparent_hugepage" / "enabled"
        self.thp.parent.mkdir(parents=True)
        self.thp.write_text("[always] madvise never\n")
        uid = os.getuid()
        user = (self.sysfs / "fs" / "cgroup" / "user.slice" / f"user-{uid}.slice"
                / f"user@{uid}.service")
        user.mkdir(parents=True)
        (user / "cgroup.controllers").write_text("cpu memory pids\n")
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            **os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STATE": str(self.state), "CALLS": str(self.root / "calls.txt"),
            "SUDO_CALLS": str(self.root / "sudo.txt"),
            "SETUP_SYSFS": str(self.sysfs), "SETUP_ETC": str(self.root / "etc"),
            "SETUP_SBIN": str(self.root / "sbin"), "HOME": str(self.home),
            "NODE_OPS_DIR": str(self.home / "node_ops"),
            "PYTHON_VENV": str(self.root / "venv"),
            "DBBENCH_BUILD_DIR": str(self.root / "build"),
            "DBBENCH_CPUS": "0-1", "CONTROLLER_CPUS": "2",
            "ROCKSDB_PORTABLE": "znver5", "ALLOW_CONCURRENT_RUNS": "1"}
        self.env.pop("NVME", None)
        self.env.pop("ISOLATE_OS_CPUS", None)

    def setup_node(self, *args, **overrides):
        return subprocess.run(["bash", str(SCRIPT), *args],
                              env={**self.env, **overrides},
                              capture_output=True, text=True)

    def sudo_calls(self):
        path = self.root / "sudo.txt"
        return path.read_text().splitlines() if path.exists() else []

    def sysfs_values(self):
        return {str(p): p.read_text() for p in self.sysfs.rglob("*") if p.is_file()}

    def test_check_names_what_is_not_ready_and_changes_nothing(self):
        before = self.sysfs_values()
        ran = self.setup_node("check")
        self.assertEqual(ran.returncode, 1, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout.strip().splitlines()[-1], "SETUP_EXIT=1")
        for name in ("submodules", "packages", "toolchain", "tuning", "boot tuning",
                     "open files", "linger", "venv", "node_ops"):
            self.assertIn(f"[{name}] NOT READY", ran.stdout)
        self.assertIn("SMT on", ran.stdout)
        self.assertIn("governor powersave", ran.stdout)
        self.assertIn("THP always", ran.stdout)
        self.assertIn("[pinning] ok", ran.stdout)
        self.assertIn("[memory cap] ok", ran.stdout)
        self.assertEqual(self.sudo_calls(), [])
        self.assertEqual(self.sysfs_values(), before)
        self.assertFalse((self.home / ".bashrc").exists())
        self.assertFalse((self.home / "node_ops").exists())
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), [])

    def test_setting_up_makes_every_item_ready_and_a_rerun_changes_nothing(self):
        ran = self.setup_node()
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(ran.stdout.strip().splitlines()[-1], "SETUP_EXIT=0")
        sudo = "\n".join(self.sudo_calls())
        # Only the missing submodule, and every package, the runbook's tools
        # among them, without a prompt.
        self.assertIn("[submodules] set", ran.stdout)
        self.assertIn("env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y", sudo)
        for package in ("build-essential", "libgflags-dev", "python3-venv",
                        "tmux", "pigz", "rsync", "zstd", "jq"):
            self.assertTrue((self.state / f"pkg-{package}").exists(), package)
        self.assertIn("add-apt-repository -y ppa:ubuntu-toolchain-r/test", sudo)
        self.assertIn("update-alternatives --set c++ /usr/bin/g++-14", sudo)
        self.assertIn("[toolchain] set: c++ (stub) 14.3.0", ran.stdout)
        # The runtime settings, through node_tuning.sh.
        cpu = self.sysfs / "devices" / "system" / "cpu"
        self.assertEqual((cpu / "smt" / "control").read_text().strip(), "off")
        for n in range(4):
            self.assertEqual((cpu / f"cpu{n}" / "cpufreq" / "scaling_governor")
                             .read_text().strip(), "performance")
        self.assertEqual(self.thp.read_text().strip(), "madvise")
        # The same at every boot: the helper is node_tuning.sh, the unit runs it.
        helper = self.root / "sbin" / "lsm-node-tuning"
        self.assertEqual(helper.read_bytes(), (PIPELINE / "node_tuning.sh").read_bytes())
        self.assertTrue(os.access(helper, os.X_OK))
        unit = (self.root / "etc" / "systemd" / "system" / "lsm-node-tuning.service").read_text()
        self.assertIn(f"ExecStart={helper}", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertTrue((self.state / "enabled-lsm-node-tuning.service").exists())
        # Open files, linger, the venv, node_ops.
        self.assertIn('ulimit -n "$(ulimit -Hn)"', (self.home / ".bashrc").read_text())
        self.assertIn(f"loginctl enable-linger {pwd.getpwuid(os.getuid()).pw_name}", sudo)
        self.assertTrue((self.state / "linger").exists())
        calls = (self.root / "calls.txt").read_text()
        self.assertIn("-m pip install -r rl_agent/requirements.txt matplotlib", calls)
        for sub in ("handoffs", "reports", "diag", "drafts"):
            self.assertTrue((self.home / "node_ops" / sub).is_dir(), sub)
        # The record of the node.
        records = list((self.home / "node_ops" / "node-setup").glob("*.txt"))
        self.assertEqual(len(records), 1)
        record = records[0].read_text()
        for line in ("cpus_online=0-3", "l3_domains=0-1 2-3", "c++=c++ (stub) 14.3.0",
                     "deb:cmake=1.0-stub", "deb:libc6=absent", "pip:torch==2.14.1",
                     "repo_head=0123abcd", "db_bench_sha256=not built"):
            self.assertIn(line, record)
        self.assertIn("SMT off, governor performance, THP madvise", record)
        # Now ready, and a second run changes nothing.
        self.assertEqual(self.setup_node("check").returncode, 0)
        before = len(self.sudo_calls())
        bashrc = (self.home / ".bashrc").read_text()
        again = self.setup_node()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("setting up", again.stdout)
        self.assertEqual(len(self.sudo_calls()), before)
        self.assertEqual((self.home / ".bashrc").read_text(), bashrc)

    def test_a_changed_node_tuning_is_reinstalled(self):
        self.assertEqual(self.setup_node().returncode, 0)
        helper = self.root / "sbin" / "lsm-node-tuning"
        helper.write_text("#!/bin/sh\n")
        ran = self.setup_node("check")
        self.assertEqual(ran.returncode, 1)
        self.assertIn("[boot tuning] NOT READY", ran.stdout)
        self.assertEqual(self.setup_node().returncode, 0)
        self.assertEqual(helper.read_bytes(), (PIPELINE / "node_tuning.sh").read_bytes())

    def test_setting_up_refuses_while_a_db_bench_runs(self):
        fake = self.root / "db_bench"
        shutil.copy("/bin/sleep", fake)
        process = subprocess.Popen([str(fake), "30"])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        for _ in range(50):  # until ps shows it under its own name
            if "db_bench" in subprocess.run(["ps", "-o", "comm=", "-p", str(process.pid)],
                                            capture_output=True, text=True).stdout:
                break
            time.sleep(0.02)
        env = {k: v for k, v in self.env.items() if k != "ALLOW_CONCURRENT_RUNS"}
        ran = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(ran.returncode, 3, ran.stdout + ran.stderr)
        self.assertIn("A run is going", ran.stderr)
        self.assertIn("db_bench", ran.stderr)
        self.assertEqual(self.sudo_calls(), [])
        # check changes nothing, so it runs anyway.
        ran = subprocess.run(["bash", str(SCRIPT), "check"], env=env,
                             capture_output=True, text=True)
        self.assertIn("SETUP_EXIT=", ran.stdout)

    def test_pinning_on_one_l3_domain_is_not_ready(self):
        ran = self.setup_node("check", DBBENCH_CPUS="0", CONTROLLER_CPUS="1")
        self.assertIn("[pinning] NOT READY: db_bench CPU 0 and controller CPU 1 share an L3",
                      ran.stdout)
        ran = self.setup_node("check", DBBENCH_CPUS="0-1", CONTROLLER_CPUS="7")
        self.assertIn("[pinning] NOT READY: CPU 7 is not online", ran.stdout)
        ran = self.setup_node("check", DBBENCH_CPUS="", CONTROLLER_CPUS="")
        self.assertIn("[pinning] ok: unpinned", ran.stdout)

    def test_the_os_cpuset_only_when_asked(self):
        ran = self.setup_node()
        self.assertNotIn("[OS cpuset]", ran.stdout)
        ran = self.setup_node(ISOLATE_OS_CPUS="1")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("systemctl set-property --runtime system.slice AllowedCPUs=3",
                      "\n".join(self.sudo_calls()))
        self.assertIn("[OS cpuset] set: system.slice confined to CPUs 3", ran.stdout)

    def test_node_tuning_on_a_node_without_smt_or_cpufreq(self):
        cpu = self.sysfs / "devices" / "system" / "cpu"
        (cpu / "smt" / "control").write_text("notsupported\n")
        for n in range(4):
            shutil.rmtree(cpu / f"cpu{n}" / "cpufreq")
        ran = subprocess.run(["bash", str(PIPELINE / "node_tuning.sh")],
                             env={**self.env}, capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(ran.stdout.strip(),
                         "[tuning] SMT notsupported; governor none; THP madvise")
        self.assertEqual((cpu / "smt" / "control").read_text(), "notsupported\n")

    def test_usage(self):
        ran = self.setup_node("bogus")
        self.assertEqual(ran.returncode, 2)
        self.assertIn("usage", ran.stderr)


if __name__ == "__main__":
    unittest.main()
