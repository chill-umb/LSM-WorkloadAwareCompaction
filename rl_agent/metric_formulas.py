"""Reference formulas shared by metric unit tests and analysis tooling."""

from __future__ import annotations


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def write_amplification(flush_bytes: int, compaction_bytes_written: int,
                        user_logical_bytes_written: int) -> float:
    return safe_ratio(flush_bytes + compaction_bytes_written,
                      user_logical_bytes_written)


def point_read_amplification(logical_sst_probes: int, point_reads: int) -> float:
    return safe_ratio(logical_sst_probes, point_reads)


def scan_amplification(returned_entries: int,
                       internal_entries_skipped: int) -> float:
    return safe_ratio(returned_entries + internal_entries_skipped,
                      returned_entries)


def sorted_run_seeks_per_scan(sorted_run_seeks: int, scans: int) -> float:
    return safe_ratio(sorted_run_seeks, scans)


def space_amplification(total_sst_bytes: int, live_logical_bytes: int) -> float:
    return safe_ratio(total_sst_bytes, live_logical_bytes)
