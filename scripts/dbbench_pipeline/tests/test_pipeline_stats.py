import sys
import unittest
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from pipeline_stats import envelope_verdict, required_pairs


class RequiredPairsTest(unittest.TestCase):
    # Values from the 2026-08-30 1M/T2 oracle preflight.  Their confidence
    # interval crosses -5% but not +5%, so three pairs cannot decide a
    # two-sided parity envelope.
    VALUES = [
        -0.03421485698185933,
        0.012281823288971427,
        -0.02601722617268587,
    ]

    def test_two_sided_requirement_uses_nearest_boundary(self):
        needed, _ = required_pairs(
            self.VALUES, 0.05, two_sided=True
        )
        self.assertEqual(needed, 5)

    def test_two_sided_requirement_is_symmetric_about_zero(self):
        negative, _ = required_pairs(
            self.VALUES, 0.05, two_sided=True
        )
        positive, _ = required_pairs(
            [-value for value in self.VALUES], 0.05, two_sided=True
        )
        self.assertEqual(negative, positive)

    def test_underpowered_two_sided_result_is_not_failed(self):
        result = envelope_verdict(
            self.VALUES, 0.05, minimum_pairs=3, two_sided=True
        )
        self.assertEqual(result["required_pairs"], 5)
        self.assertEqual(result["verdict"], "insufficient_pairs")
        self.assertIsNone(result["passed"])

    def test_one_sided_requirement_is_unchanged(self):
        needed, _ = required_pairs(self.VALUES, 0.05)
        self.assertEqual(needed, 3)

    def test_two_sided_limit_cannot_be_negative(self):
        with self.assertRaises(ValueError):
            required_pairs(self.VALUES, -0.05, two_sided=True)


if __name__ == "__main__":
    unittest.main()
