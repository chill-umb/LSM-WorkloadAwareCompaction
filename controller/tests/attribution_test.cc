// Per-level cost parts (D §4, Proposition D.16): the slot-blocking split sums
// to 1 and preserves totals, hit reads go only to the bucket, reopens are
// charged where they happen (D-21), writes go to the source level (plan §6.2
// attribution_test).
#include "attribution.h"

#include <random>

#include "gtest/gtest.h"
#include "test_util.h"

namespace rlc {
namespace {

using test::FakeHost;

Segment L0Blocked(int slot_level, int k0, int K0) {
  Segment s;
  s.ops.keys_read = 70;
  s.ops.keys_written = 30;
  s.reads.resize(5);
  // probes, passes, hits, seeks, Get and iterator reopens, reopen nanos
  s.reads[0] = {90, 30, 6, 40, 12, 6, 180000};
  s.reads[2] = {50, 10, 8, 20, 4, 2, 60000};
  s.slot_level = slot_level;
  s.l0_due = true;
  s.k0 = k0;
  s.K0 = K0;
  return s;
}

struct Totals {
  double probes = 0, fp = 0, seeks = 0, reopens = 0;
};

// Charged reads: raw, less what the slot rule moved out, plus what it moved in.
Totals Charged(const std::vector<LevelParts>& parts) {
  Totals t;
  for (const LevelParts& p : parts) {
    t.probes += p.probes - p.slot_out_probes + p.slot_in_probes;
    t.fp += p.fp_reads - p.slot_out_fp_reads + p.slot_in_fp_reads;
    t.seeks += p.seeks - p.slot_out_seeks + p.slot_in_seeks;
    t.reopens += p.reopens - p.slot_out_reopens + p.slot_in_reopens;
  }
  return t;
}

TEST(Attribution, SlotBlockingMovesTheExcessShareAndKeepsTheTotal) {
  std::vector<LevelParts> parts(5);
  AttributeSegment(L0Blocked(2, 6, 4), &parts);  // share (6 - 4) / 6
  EXPECT_NEAR(parts[0].slot_out_probes, 30, 1e-12);
  EXPECT_NEAR(parts[0].slot_out_fp_reads, 8, 1e-12);
  EXPECT_NEAR(parts[0].slot_out_seeks, 40.0 / 3, 1e-12);
  EXPECT_EQ(parts[2].slot_in_probes, parts[0].slot_out_probes);
  EXPECT_EQ(parts[2].slot_in_fp_reads, parts[0].slot_out_fp_reads);
  EXPECT_EQ(parts[2].slot_in_seeks, parts[0].slot_out_seeks);
  // L0's 18 reopens move with its probes and seeks (D-21).
  EXPECT_NEAR(parts[0].slot_out_reopens, 6, 1e-12);
  EXPECT_EQ(parts[2].slot_in_reopens, parts[0].slot_out_reopens);
  // L0 keeps 2/3, level 2 takes 1/3: the split sums to 1.
  const double kept = parts[0].probes - parts[0].slot_out_probes;
  EXPECT_NEAR(kept + parts[2].slot_in_probes, parts[0].probes, 1e-12);
  const Totals t = Charged(parts);
  EXPECT_NEAR(t.probes, 140, 1e-9);
  EXPECT_NEAR(t.fp, 24 + 2, 1e-9);
  EXPECT_NEAR(t.seeks, 60, 1e-9);
  EXPECT_NEAR(t.reopens, 24, 1e-9);
}

// D-21: each level is charged the reopens its Gets and iterators made, both
// kinds at the one price c_open; the time is not a charge.
TEST(Attribution, ReopensAreChargedWhereTheyHappen) {
  std::vector<LevelParts> parts(5);
  AttributeSegment(L0Blocked(-1, 2, 4), &parts);
  EXPECT_EQ(parts[0].reopens, 18);
  EXPECT_EQ(parts[2].reopens, 6);
  EXPECT_EQ(parts[1].reopens, 0);
  EXPECT_EQ(parts[0].slot_out_reopens + parts[2].slot_in_reopens, 0);
}

TEST(Attribution, NoMoveUnlessL0IsDueAndAnotherLevelHoldsTheSlot) {
  for (const Segment& s : {L0Blocked(-1, 6, 4),    // slot idle
                           L0Blocked(0, 6, 4),     // L0's own job
                           L0Blocked(2, 4, 4),     // not beyond the trigger
                           L0Blocked(9, 6, 4)}) {  // no such level
    std::vector<LevelParts> parts(5);
    AttributeSegment(s, &parts);
    for (const LevelParts& p : parts) {
      EXPECT_EQ(p.slot_out_probes + p.slot_in_probes, 0);
      EXPECT_EQ(p.slot_out_reopens + p.slot_in_reopens, 0);
    }
  }
  Segment not_due = L0Blocked(2, 6, 4);
  not_due.l0_due = false;
  std::vector<LevelParts> parts(5);
  AttributeSegment(not_due, &parts);
  EXPECT_EQ(parts[2].slot_in_probes, 0);
}

TEST(Attribution, HitReadsGoOnlyToTheBucket) {
  std::vector<LevelParts> parts(5);
  AttributeSegment(L0Blocked(-1, 2, 4), &parts);
  EXPECT_EQ(parts[2].hit_reads, 8);
  EXPECT_EQ(parts[2].fp_reads, 2);  // passes 10 - hits 8
  EXPECT_EQ(parts[0].fp_reads, 24);
  const Totals t = Charged(parts);
  EXPECT_EQ(t.fp, 26);  // the 14 hits are not charged to any level
}

// D §4 charges shadowed garbage per operation served, so the interval logs
// the integral of B_i over its operations, not B_i at the close.
TEST(Attribution, HeldBytesAreIntegratedOverOperations) {
  std::vector<LevelParts> parts(5);
  Segment early = L0Blocked(-1, 2, 4);  // 100 operations
  early.held = {0, 1000, 5000, 0, 0};
  Segment late = L0Blocked(-1, 2, 4);
  late.ops.keys_read = 20;  // 50 operations
  late.held = {0, 3000, 5000, 0, 0};
  AttributeSegment(early, &parts);
  AttributeSegment(late, &parts);
  EXPECT_EQ(parts[1].held_byte_ops, 1000.0 * 100 + 3000.0 * 50);
  EXPECT_EQ(parts[2].held_byte_ops, 5000.0 * 150);
  EXPECT_EQ(parts[0].held_byte_ops, 0);
}

TEST(Attribution, OperationsAndBusyShare) {
  std::vector<LevelParts> parts(5);
  AttributeSegment(L0Blocked(3, 2, 4), &parts);
  AttributeSegment(L0Blocked(-1, 2, 4), &parts);
  for (const LevelParts& p : parts) {
    EXPECT_EQ(p.ops, 200);
    EXPECT_EQ(p.gets, 140);
    EXPECT_EQ(p.writes, 60);
    EXPECT_EQ(p.busy_ops, 100);
  }
}

TEST(Attribution, WritesGoToTheSourceLevel) {
  std::vector<LevelParts> parts(5);
  AttributeJobEnd(FakeHost::Flush(64), &parts);
  AttributeJobEnd(FakeHost::Compaction(true, 1, 1, 100, 200, 270), &parts);
  auto trivial = FakeHost::Compaction(true, 2, 2, 50, 0, 0);
  trivial.trivial = true;
  AttributeJobEnd(trivial, &parts);
  auto failed = FakeHost::Compaction(true, 3, 3, 10, 10, 20);
  failed.ok = false;
  AttributeJobEnd(failed, &parts);
  AttributeJobEnd(FakeHost::Compaction(false, 4, 1, 1, 1, 0),
                  &parts);  // a begin
  EXPECT_EQ(parts[0].write_bytes, 64);
  EXPECT_EQ(parts[1].write_bytes, 270);  // not the output level
  EXPECT_EQ(parts[2].write_bytes, 0);    // trivial: nothing written
  EXPECT_EQ(parts[3].write_bytes, 0);    // failed
  EXPECT_EQ(parts[0].inflow_bytes, 64);
  EXPECT_EQ(parts[2].inflow_bytes, 70);  // X - O landed in L2
  EXPECT_EQ(parts[3].inflow_bytes, 50);  // the moved file
}

TEST(Attribution, RandomSegmentsPreserveEveryTotal) {
  std::mt19937 rng(16);
  std::vector<LevelParts> parts(5);
  Totals raw;
  for (int i = 0; i < 1000; ++i) {
    Segment s;
    s.reads.resize(5);
    for (auto& r : s.reads) {
      r.filter_hits = rng() % 20;
      r.filter_passes = r.filter_hits + rng() % 20;
      r.probes = r.filter_passes + rng() % 100;
      r.seeks = rng() % 50;
      r.get_reopens = rng() % 10;
      r.iter_reopens = rng() % 5;
      raw.probes += r.probes;
      raw.fp += r.filter_passes - r.filter_hits;
      raw.seeks += r.seeks;
      raw.reopens += r.get_reopens + r.iter_reopens;
    }
    s.slot_level = static_cast<int>(rng() % 6) - 1;
    s.l0_due = rng() % 2;
    s.k0 = 1 + rng() % 12;
    s.K0 = 2 + rng() % 6;
    AttributeSegment(s, &parts);
  }
  const Totals t = Charged(parts);
  EXPECT_NEAR(t.probes, raw.probes, 1e-6);
  EXPECT_NEAR(t.fp, raw.fp, 1e-6);
  EXPECT_NEAR(t.seeks, raw.seeks, 1e-6);
  EXPECT_NEAR(t.reopens, raw.reopens, 1e-6);
}

// Cost model 2 (D §4 as amended 2026-10-03, D-23).
TEST(Attribution, HiddenStepsAreChargedToTheLevelAbove) {
  std::vector<LevelParts> parts(5);
  Segment s;
  s.ops.seeks = 10;
  s.reads.resize(5);
  s.reads[0].hidden_steps = 7;   // L0's own entries: L0
  s.reads[1].hidden_steps = 11;  // L1's: L0
  s.reads[2].hidden_steps = 13;  // L2's: L1
  s.reads[4].hidden_steps = 17;  // the last level's: L3
  s.steps.iter_skips = 7 + 11 + 13 + 17 + 5;  // 5 over memtable entries
  AttributeSegment(s, &parts);
  EXPECT_EQ(parts[0].hidden_steps, 18);
  EXPECT_EQ(parts[1].hidden_steps, 13);
  EXPECT_EQ(parts[2].hidden_steps, 0);
  EXPECT_EQ(parts[3].hidden_steps, 17);
  EXPECT_EQ(parts[4].hidden_steps, 0);
  // The memtable bucket's share, and the global counts, in every record.
  for (const LevelParts& p : parts) {
    EXPECT_EQ(p.memtable_hidden, 5);
    EXPECT_EQ(p.fg_iter_skips, 53);
    EXPECT_EQ(p.l0_hidden, 18);
  }
}

TEST(Attribution, SlotBlockingMovesL0sHiddenStepChargeToo) {
  std::vector<LevelParts> parts(5);
  Segment s = L0Blocked(2, 6, 4);  // share 1/3
  s.reads[0].hidden_steps = 30;
  s.reads[1].hidden_steps = 30;
  s.steps.iter_skips = 60;
  AttributeSegment(s, &parts);
  EXPECT_NEAR(parts[0].slot_out_hidden, 20, 1e-12);
  EXPECT_EQ(parts[2].slot_in_hidden, parts[0].slot_out_hidden);
  double charged = 0;
  for (const LevelParts& p : parts) {
    charged += p.hidden_steps - p.slot_out_hidden + p.slot_in_hidden;
  }
  EXPECT_NEAR(charged, 60, 1e-12);  // the total is unchanged (D.16)
}

TEST(Attribution, JobsAreCountedByKindAtTheirStartLevel) {
  std::vector<LevelParts> parts(5);
  AttributeJobEnd(FakeHost::Flush(1000), &parts);
  AttributeJobEnd(FakeHost::Compaction(true, 1, 0, 400, 300, 650), &parts);
  AttributeJobEnd(FakeHost::Compaction(true, 2, 2, 100, 200, 280), &parts);
  auto move = FakeHost::Compaction(true, 3, 3, 500, 0, 0);
  move.trivial = true;
  AttributeJobEnd(move, &parts);
  auto failed = FakeHost::Compaction(true, 4, 2, 100, 200, 0);
  failed.ok = false;
  AttributeJobEnd(failed, &parts);
  EXPECT_EQ(parts[0].jobs_flush, 1);
  EXPECT_EQ(parts[0].jobs_l0, 1);
  EXPECT_EQ(parts[0].read_bytes, 700);
  EXPECT_EQ(parts[2].jobs_deep, 1);  // the failed job is not counted
  EXPECT_EQ(parts[2].read_bytes, 300);
  EXPECT_EQ(parts[3].jobs_move, 1);
  EXPECT_EQ(parts[3].read_bytes, 0);  // a move reads nothing
  EXPECT_EQ(parts[3].write_bytes, 0);
}

TEST(Attribution, L0sPerFileReadsAndK0AreInEveryRecord) {
  std::vector<LevelParts> parts(5);
  Segment s = L0Blocked(-1, 3, 4);
  s.l0_due = false;
  s.steps.probes = 140;
  AttributeSegment(s, &parts);
  for (const LevelParts& p : parts) {
    EXPECT_EQ(p.k0_ops, 3 * 100);
    EXPECT_EQ(p.l0_probes, 90);
    EXPECT_EQ(p.l0_block_probes, 30);  // every filter pass reads a block
    EXPECT_EQ(p.l0_seeks, 40);
    EXPECT_EQ(p.l0_reopens, 18);
    EXPECT_EQ(p.fg_probes, 140);
  }
}

}  // namespace
}  // namespace rlc
