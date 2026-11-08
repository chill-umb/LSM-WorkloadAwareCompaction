"""The pipeline's tests. Every stage a test starts inherits this process's
environment, so two machine-wide effects are made harmless here, before any
test runs (owner, 2026-11-08: the SSD is trimmed before every run, and a
machine's calibration is used by every batch on it):

- SSD_TRIM_COMMAND is a stand-in (fixtures/fake_fstrim.sh) that never
  touches a disk; a test that checks the TRIM sets SSD_TRIM_LOG or
  SSD_TRIM_FAIL.
- MACHINE_CAL_DIR points at a folder that does not exist, so no test finds
  the real machine's calibration under ~/node_ops/machine-cal.

A test module run on its own (python tests/test_x.py) skips this; run them
through run_python_tests.sh or "python -m unittest discover -s tests -t ."."""

import os
import tempfile
from pathlib import Path

os.environ["SSD_TRIM_COMMAND"] = (
    f"bash {Path(__file__).resolve().parent / 'fixtures' / 'fake_fstrim.sh'}")
os.environ["MACHINE_CAL_DIR"] = str(
    Path(tempfile.gettempdir()) / f"no-machine-calibration-{os.getpid()}")
