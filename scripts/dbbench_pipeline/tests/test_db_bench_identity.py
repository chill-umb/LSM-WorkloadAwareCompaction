"""db_bench's identity as loaded (preflight_marker.db_bench_identity): one
sha256 over the executable and the RocksDB shared library the dynamic loader
resolves for it, which the marker, 03 (dbbench_sha256, binary<sha>), 18, 22
and gate_n2_plan all use. On 2026-10-02 a fork change rebuilt only
librocksdb.so.11 and a hash of the db_bench file alone stayed put. Checks:
stable when nothing changes; changed by the library alone; the plain rule
over the executable alone for a static one; the library ldd resolves is the
one hashed (and, on this host, ldd resolves what the loader actually maps);
loud failure when a RocksDB library cannot be resolved; the CLI bash calls."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import preflight_marker as pm

SCRIPT = Path(pm.__file__).resolve()
ELF = b"\x7fELF\x02\x01\x01" + bytes(9)  # an ELF header's first bytes


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def static_rule(executable: Path) -> str:
    """The rule written out for an executable that loads no librocksdb."""
    return sha(f"{pm.IDENTITY_SCHEME}\nexecutable "
               f"{sha(executable.read_bytes())}\n".encode())


class IdentityTest(unittest.TestCase):
    """A build directory laid out as 01/02 leave it, and a stand-in ldd that
    prints what glibc's prints for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        build = self.dir / "build-dbbench"
        build.mkdir()
        self.db_bench = build / "db_bench"
        self.db_bench.write_bytes(ELF + b"db_bench-1")
        self.library = build / "librocksdb.so.11.1.1"
        self.library.write_bytes(b"rocksdb-1")
        (build / "librocksdb.so.11").symlink_to("librocksdb.so.11.1.1")
        (build / "librocksdb.so").symlink_to("librocksdb.so.11")
        self.libc = self.dir / "libc.so.6"
        self.libc.write_bytes(b"libc-1")
        self.ldd = self.dir / "bin" / "ldd"
        self.ldd.parent.mkdir()
        self.calls = self.dir / "ldd.calls"
        self.loads(build / "librocksdb.so.11")

    def loads(self, rocksdb, stdout=None, stderr="", code=0):
        """Make the stand-in ldd report `rocksdb` as the resolved
        librocksdb.so.11 (or print `stdout` instead)."""
        if stdout is None:
            stdout = ("\tlinux-vdso.so.1 (0x00007ffd5c9e6000)\n"
                      f"\tlibrocksdb.so.11 => {rocksdb} (0x00007f3a1c000000)\n"
                      "\tlibgflags.so.2.2 => /lib/x86_64-linux-gnu/"
                      "libgflags.so.2.2 (0x00007f3a1d000000)\n"
                      f"\tlibc.so.6 => {self.libc} (0x00007f3a1b000000)\n"
                      "\t/lib64/ld-linux-x86-64.so.2 (0x00007f3a1e000000)\n")
        (self.dir / "ldd.out").write_text(stdout)
        (self.dir / "ldd.err").write_text(stderr)
        self.ldd.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$1" >> "{self.calls}"\n'
            f'cat "{self.dir / "ldd.out"}"\n'
            f'cat "{self.dir / "ldd.err"}" >&2\n'
            f"exit {code}\n")
        self.ldd.chmod(0o755)

    def identity(self, path=None):
        return pm.db_bench_identity(path or self.db_bench, ldd=str(self.ldd))

    def test_stable_when_nothing_changes(self):
        first = self.identity()
        self.assertRegex(first, r"^[0-9a-f]{64}$")  # fingerprint's binary<sha>
        self.assertEqual(self.identity(), first)
        # Paths are not hashed: the same bytes elsewhere are the same program.
        moved = self.dir / "elsewhere" / "db_bench"
        moved.parent.mkdir()
        shutil.copy(self.db_bench, moved)
        self.assertEqual(self.identity(moved), first)

    def test_a_library_change_alone_changes_it(self):
        before, file_hash = self.identity(), pm.file_sha256(self.db_bench)
        self.library.write_bytes(b"rocksdb-2")  # the fork rebuilt, as on 10-02
        self.assertEqual(pm.file_sha256(self.db_bench), file_hash)
        self.assertNotEqual(self.identity(), before)

    def test_an_executable_change_changes_it(self):
        before = self.identity()
        self.db_bench.write_bytes(ELF + b"db_bench-2")
        self.assertNotEqual(self.identity(), before)

    def test_the_rule_written_out(self):
        expected = sha((f"{pm.IDENTITY_SCHEME}\n"
                        f"executable {sha(self.db_bench.read_bytes())}\n"
                        f"librocksdb.so.11 {sha(b'rocksdb-1')}\n").encode())
        self.assertEqual(self.identity(), expected)

    def test_static_executable_is_the_rule_over_itself(self):
        dynamic = self.identity()
        for stdout, stderr, code in (
                ("\tstatically linked\n", "", 0),            # static-pie
                ("", "\tnot a dynamic executable\n", 1),     # static
                # dynamic, but RocksDB linked in: only system libraries
                (f"\tlibc.so.6 => {self.libc} (0x00007f3a1b000000)\n", "", 0)):
            with self.subTest(stdout=stdout, stderr=stderr):
                self.loads(None, stdout, stderr, code)
                self.assertEqual(self.identity(), static_rule(self.db_bench))
                self.assertNotEqual(self.identity(), dynamic)

    def test_a_script_needs_no_loader(self):
        # The tests' stand-in db_bench is a script: not ELF, ldd not asked.
        script = self.dir / "stand_in"
        script.write_text("#!/usr/bin/env bash\nexit 0\n")
        self.assertEqual(self.identity(script), static_rule(script))
        self.assertFalse(self.calls.exists())

    def test_the_library_ldd_resolves_is_hashed(self):
        # The loader (RUNPATH, LD_LIBRARY_PATH) picks /other's copy, so a
        # librocksdb beside db_bench that it does not load does not count;
        # nor do system libraries.
        other = self.dir / "other" / "librocksdb.so.11"
        other.parent.mkdir()
        other.write_bytes(b"rocksdb-other")
        self.loads(other)
        before = self.identity()
        self.assertEqual(self.calls.read_text().split(), [str(self.db_bench)])
        parts = pm.db_bench_identity_parts(self.db_bench, ldd=str(self.ldd))
        self.assertEqual([p[:2] for p in parts[1:]],
                         [("librocksdb.so.11", str(other))])
        self.library.write_bytes(b"rocksdb-2")
        self.libc.write_bytes(b"libc-2")
        self.assertEqual(self.identity(), before)
        other.write_bytes(b"rocksdb-other-2")
        self.assertNotEqual(self.identity(), before)

    def test_a_library_named_by_path_counts(self):
        self.loads(None, f"\t{self.library} (0x00007f3a1c000000)\n")
        self.assertEqual([p[0] for p in pm.db_bench_identity_parts(
            self.db_bench, ldd=str(self.ldd))],
            ["executable", "librocksdb.so.11.1.1"])

    def test_an_unresolved_library_fails_loudly(self):
        cases = [
            ("\tlibrocksdb.so.11 => not found\n", "", 0, "librocksdb.so.11"),
            (f"\tlibrocksdb.so.11 => {self.dir / 'gone.so'} (0x1000)\n", "", 0,
             "not a file"),
            ("", "ldd: exited with unknown exit code (139)\n", 1, "exited 1"),
        ]
        for stdout, stderr, code, message in cases:
            with self.subTest(message=message):
                self.loads(None, stdout, stderr, code)
                with self.assertRaisesRegex(pm.IdentityError, re.escape(message)):
                    self.identity()
        with self.assertRaisesRegex(pm.IdentityError, "cannot run"):
            pm.db_bench_identity(self.db_bench, ldd=str(self.dir / "no-ldd"))

    def test_the_marker_refuses_a_rebuilt_library(self):
        root = self.dir / "repo"
        root.mkdir()
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        marker = self.dir / "PREFLIGHT_PASSED"
        with mock.patch.object(pm, "LDD", str(self.ldd)):
            hashes = lambda: pm.current_hashes(root, self.db_bench, None)
            self.assertEqual(hashes()["db_bench_sha256"], self.identity())
            pm.write_marker(marker, hashes(), {1, 2, 3, 4}, {})
            self.assertEqual(pm.problems(pm.read_marker(marker), hashes(), {1}), [])
            self.library.write_bytes(b"rocksdb-2")
            found = pm.problems(pm.read_marker(marker), hashes(), {1})
        self.assertEqual(len(found), 1)
        self.assertIn("db_bench_sha256 changed", found[0])

    def cli(self, *args, path_ldd=True):
        """The CLI bash stages call, with the stand-in ldd first on PATH."""
        env = dict(os.environ)
        if path_ldd:
            env["PATH"] = f"{self.ldd.parent}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def test_cli_prints_the_identity(self):
        ran = self.cli("identity", "--db-bench", str(self.db_bench), "--explain")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(ran.stdout, self.identity() + "\n")
        self.assertIn(f"librocksdb.so.11 {self.dir / 'build-dbbench'}", ran.stderr)
        self.assertIn(sha(b"rocksdb-1"), ran.stderr)

    def test_cli_fails_loudly(self):
        self.loads(None, "\tlibrocksdb.so.11 => not found\n")
        ran = self.cli("identity", "--db-bench", str(self.db_bench))
        self.assertEqual((ran.returncode, ran.stdout), (1, ""))
        self.assertIn("cannot identify db_bench", ran.stderr)
        self.assertIn("librocksdb.so.11", ran.stderr)
        ran = self.cli("identity", "--db-bench", str(self.dir / "missing"))
        self.assertEqual((ran.returncode, ran.stdout), (1, ""))
        # write and check still need their marker.
        self.assertEqual(self.cli("check", "--db-bench",
                                  str(self.db_bench)).returncode, 2)


@unittest.skipUnless(shutil.which("ldd") and Path("/proc/self/maps").is_file(),
                     "needs glibc's ldd and /proc")
class RealLoaderTest(unittest.TestCase):
    """On this host, with the real ldd: the library loaded_libraries names
    for a running program is the one the loader mapped into it."""

    def test_python_s_libc_is_the_mapped_one(self):
        executable = Path(os.path.realpath(sys.executable))
        with open(executable, "rb") as handle:
            if handle.read(4) != b"\x7fELF":
                self.skipTest(f"{executable} is not ELF")
        found = pm.loaded_libraries(executable, re.compile(r"libc\.so\.[0-9]+"))
        mapped = {line.split()[-1] for line in
                  Path("/proc/self/maps").read_text().splitlines()
                  if re.search(r"/libc\.so\.[0-9]+$", line)}
        if not mapped:
            self.skipTest("the interpreter maps no libc.so")
        self.assertEqual(len(found), 1, found)
        self.assertTrue(any(os.path.samefile(found[0][1], m) for m in mapped),
                        (found, mapped))

    def test_a_static_executable(self):
        # glibc's ldconfig is always linked statically.
        for candidate in ("/sbin/ldconfig.real", "/usr/sbin/ldconfig.real",
                          "/sbin/ldconfig", "/usr/sbin/ldconfig"):
            path = Path(candidate)
            if path.is_file() and path.read_bytes()[:4] == b"\x7fELF":
                self.assertEqual(pm.loaded_libraries(path), [])
                self.assertEqual(pm.db_bench_identity(path), static_rule(path))
                return
        self.skipTest("no static ldconfig ELF on this host")


if __name__ == "__main__":
    unittest.main()
