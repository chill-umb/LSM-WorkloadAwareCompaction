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

  // Cost model 2 (D §4 as amended 2026-10-03, D-23; log schema 4).
  // Jobs sourced here that completed in the interval, by kind (a flush
  // counts at L0), and the compaction bytes S + O their merges read.
  double jobs_flush = 0, jobs_l0 = 0, jobs_deep = 0, jobs_move = 0;
  double read_bytes = 0;
  // Hidden steps charged here: over the entries of level i + 1 (L0: of L0
  // and L1), the fork's per-level counter (D-24 §2's provisional scan rule,
  // the interim interface §5.3). Slot blocking moves L0's share as it moves
  // its probes (D §4).
  double hidden_steps = 0;
  double slot_out_hidden = 0, slot_in_hidden = 0;
  // The same in every level's record: the interval's global foreground
  // steps (the host's step counters, differenced), the hidden steps over
  // memtable entries (the memtable bucket), the sum over its operations of
  // L0's file count k0, and L0's raw read counts, so each level's state can
  // split the read cost per operation into L0's per-file part and the rest
  // (H §2 item 3). With gets, scans and writes above they also give the
  // shared buckets' counts (H §3).
  double fg_probes = 0, fg_block_probes = 0, fg_run_seeks = 0;
  double fg_reopens = 0, fg_nexts_found = 0, fg_iter_skips = 0;
  double memtable_hidden = 0;
  double k0_ops = 0;
  double l0_probes = 0, l0_block_probes = 0, l0_seeks = 0, l0_reopens = 0;
  double l0_hidden = 0;  // hidden steps charged to L0 (entries of L0 and L1)
};

// What happened between two polls, with the tree state sampled at the first.
struct Segment {
  ROCKSDB_NAMESPACE::RLOpCounts ops;                        // differences
  std::vector<ROCKSDB_NAMESPACE::RLLevelReadCounts> reads;  // differences
  ROCKSDB_NAMESPACE::RLStepCounts steps;                    // differences
  int slot_level = -1;       // start level of the job holding the slot; -1 idle
  bool l0_due = false;       // L0's score was at least 1
  int k0 = 0;                // L0 files, all of them (each is probed)
  int K0 = 0;                // the trigger in effect
  std::vector<double> held;  // B_i per level
};

// Adds a segment's reads and operations to every level's interval. While L0
// is due and a job sourced at level i >= 1 holds the slot, the share
// (k0 - K0)^+ / k0 of L0's probes, false-positive reads, seeks, reopens and
// hidden steps moves from L0 to level i. The total is unchanged (D.16). Each
// level also adds its held bytes times the operations served. Hidden entries
// at level j >= 2 are charged to level j - 1, those at L0 and L1 to L0; the
// hidden steps the per-level counters do not place are the memtable's.
void AttributeSegment(const Segment& segment, std::vector<LevelParts>* parts);

// A finished job: its bytes to its start level (a flush's to L0), unless it
// failed or was a trivial move; the net bytes it landed to its output level;
// its kind's count and, for a merge, its bytes read S + O, to its start
// level (cost model 2; the evaluator's kinds: a trivial move is "move", a
// merge sourced at L0 "l0", deeper "deep").
void AttributeJobEnd(const ROCKSDB_NAMESPACE::RLJobRecord& job,
                     std::vector<LevelParts>* parts);

// A job of `level` started after waiting `wait_ops` operations for the slot.
void AttributeWait(int level, double wait_ops, std::vector<LevelParts>* parts);

}  // namespace rlc
