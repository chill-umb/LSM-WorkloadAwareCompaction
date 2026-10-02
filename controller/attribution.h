// Per-level cost parts over each level's open decision interval, in counts
// and bytes (PATHWAYS D §4, Proposition D.16). Prices and priority weights
// are applied later, by the trainer.
#pragma once

#include <vector>

#include "rocksdb/rl_controller_host.h"

namespace rlc {

struct LevelParts {
  // Bytes written by jobs sourced at this level (flushes: L0), by
  // completion time. Trivial moves write nothing.
  double write_bytes = 0;
  // Raw reads at this level, before the slot-blocking rule. hit_reads is the
  // found key's block read: logged here, charged to the shared bucket only.
  double probes = 0;
  double fp_reads = 0;  // filter passes without a hit
  double seeks = 0;
  double hit_reads = 0;
  // Tables a Get or a user iterator reopened at this level (D-21), priced
  // at c_open; charged like the probes and seeks that reopened them.
  double reopens = 0;
  // Slot-blocking moves (D §4): out of L0 (level 0 only), into the level
  // whose job held the slot. Charged reads = raw - slot_out + slot_in.
  double slot_out_probes = 0, slot_out_fp_reads = 0, slot_out_seeks = 0;
  double slot_in_probes = 0, slot_in_fp_reads = 0, slot_in_seeks = 0;
  double slot_out_reopens = 0, slot_in_reopens = 0;
  // Operations served, and their kinds.
  double ops = 0, gets = 0, scans = 0, writes = 0, user_bytes = 0;
  // State inputs measured over the interval (G §3): operations served while
  // the compaction slot was busy (zeta), net bytes landing in this level
  // (the inflow ratio), and the slot waits of this level's releases (omega),
  // in operations: a wait is measured when its job starts and attributed
  // when the job ends, to the interval of the release.
  double busy_ops = 0;
  double inflow_bytes = 0;
  double wait_ops = 0, waits = 0;
  // Sum over the interval's operations of the level's bytes not being
  // compacted, B_i: D §4 charges shadowed garbage (1 - rho~_i) B_i per
  // operation served, so the trainer needs the integral, not an endpoint.
  double held_byte_ops = 0;
};

// What happened between two polls, with the tree state sampled at the first.
struct Segment {
  ROCKSDB_NAMESPACE::RLOpCounts ops;                        // differences
  std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts> reads;  // differences
  int slot_level = -1;       // start level of the job holding the slot; -1 idle
  bool l0_due = false;       // L0's score was at least 1
  int k0 = 0;                // L0 files, all of them (each is probed)
  int K0 = 0;                // the trigger in effect
  std::vector<double> held;  // B_i per level
};

// Adds a segment's reads and operations to every level's interval. While L0
// is due and a job sourced at level i >= 1 holds the slot, the share
// (k0 - K0)^+ / k0 of L0's probes, false-positive reads, seeks and reopens
// moves from L0 to level i. The total is unchanged (D.16). Each level also
// adds its held bytes times the operations served.
void AttributeSegment(const Segment& segment, std::vector<LevelParts>* parts);

// A finished job: its bytes to its start level (a flush's to L0), unless it
// failed or was a trivial move; the net bytes it landed to its output level.
void AttributeJobEnd(const ROCKSDB_NAMESPACE::RLJobRecord& job,
                     std::vector<LevelParts>* parts);

// A job of `level` started after waiting `wait_ops` operations for the slot.
void AttributeWait(int level, double wait_ops, std::vector<LevelParts>* parts);

}  // namespace rlc
