import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from metric_formulas import (point_read_amplification, scan_amplification,
                             sorted_run_seeks_per_scan, space_amplification,
                             write_amplification)


class MetricFormulaTest(unittest.TestCase):
    def test_synthetic_get_scan_write_delete_and_empty_scan(self):
        self.assertEqual(write_amplification(100, 300, 200), 2.0)
        # Four logical table probes includes two Bloom-filter negatives and is
        # independent of whether the other two hit cache.
        self.assertEqual(point_read_amplification(4, 2), 2.0)
        self.assertEqual(scan_amplification(10, 5), 1.5)
        self.assertEqual(scan_amplification(0, 20), 0.0)
        self.assertEqual(sorted_run_seeks_per_scan(12, 3), 4.0)
        self.assertEqual(space_amplification(1200, 1000), 1.2)


if __name__ == "__main__":
    unittest.main()
