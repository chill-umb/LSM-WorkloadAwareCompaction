// The turn learner's pure parts (turn.h; the design is
// ~/latex/2026-11-07-rl-design/rl-design.tex, stage 1's test list).
#include "turn.h"

#include <cmath>
#include <unistd.h>

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <vector>

#include "gtest/gtest.h"

namespace rlc {
namespace {

constexpr double kMiB = 1024.0 * 1024.0;
constexpr double kGiB = 1024.0 * kMiB;

TurnConfig Cfg() {
  TurnConfig c;
  c.turn_ops = 64000;
  c.acting = 5;
  c.arch = 0;
  c.kappa = 50;
  c.margin = 0.1;
  c.sigma_max = 4.0 / 3.0;
  c.glide_share = 0.5;
  c.forecast_turns = 8;
  c.guard_owed_bytes = 16 * kGiB;
  c.guard_l0_flushes = 4;
  c.safety_l0_flushes = 2;
  c.safety_pending_bytes = 32 * kGiB;
  c.k_defer_max = 18;
  c.renew_band = 0.5;
  c.max_topups = 2;
  c.explore_start = 0.5;
  c.explore_end = 0.05;
  c.explore_turns = 200;
  c.k_rungs = {2, 3, 4, 6, 8};
  c.m_rungs = {0.5, std::sqrt(0.5), 1, std::sqrt(2.0), 2};
  return c;
}

struct Ladders {
  Ladder k, m;
};
Ladders MakeLadders(const TurnConfig& c, int k_cfg = 4) {
  Ladders l;
  std::string error;
  EXPECT_TRUE(MakeLadder(c.k_rungs, k_cfg, &l.k, &error)) << error;
  EXPECT_TRUE(MakeLadder(c.m_rungs, 1.0, &l.m, &error)) << error;
  return l;
}

// T = 6, C_1 = 128 MiB, six levels, data down to L4 (the last level).
TurnTree Tree() {
  TurnTree t;
  t.num_levels = 6;
  t.T = 6;
  t.C = {0, 128 * kMiB, 768 * kMiB, 4608 * kMiB, 27648 * kMiB, 165888 * kMiB};
  t.B = {16 * kMiB, 100 * kMiB, 700 * kMiB, 4000 * kMiB, 20000 * kMiB, 0};
  t.B_all = t.B;
  t.score = {0.5, 100.0 / 128, 700.0 / 768, 4000.0 / 4608, 20000.0 / 27648, 0};
  t.m_applied = {1, 1, 1, 1, 1, 1};
  t.file_bytes = {8 * kMiB, 8 * kMiB, 8 * kMiB, 8 * kMiB, 8 * kMiB, 8 * kMiB};
  t.n0 = 2;
  t.n0_all = 2;
  t.K_applied = 4;
  t.K_cfg = 4;
  t.K_slow = 20;
  t.k_live_cap = 8;
  t.last = 4;
  t.pending = 1 * kGiB;
  t.soft_limit = 64 * kGiB;
  t.f_hat = 3.7;
  t.I_hat = {0, 60 * kMiB, 40 * kMiB, 30 * kMiB, 25 * kMiB, 0};
  t.X_bar = 400 * kMiB;
  t.put_share = 0.3;
  t.get_share = 0.6;
  t.scan_share = 0.1;
  t.probes_per_get = {1.5, 0.9, 0.8, 0.7, 0.6, 0};
  return t;
}

std::vector<AnchorState> Native(int n) { return std::vector<AnchorState>(n); }

std::vector<Action> Holds(int n) { return std::vector<Action>(n, Action::kHold); }

bool Admissible(const TurnTree& t, const TurnSettings& s) {
  for (int i = 1; i < t.num_levels; ++i) {
    if (!(s.m[i] >= 0.5 - 1e-12 && s.m[i] <= 2 + 1e-12)) return false;
    if (i + 1 < t.num_levels && s.m[i + 1] * t.T < s.m[i] - 1e-12) return false;
  }
  return s.m[0] == 1.0 && s.K >= 1;
}

// --- Rungs, decay, press ---------------------------------------------------

TEST(TurnLadder, RoundsInLog2AndTiesGoTowardNative) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  EXPECT_EQ(l.m.native, 2);
  EXPECT_EQ(RungIndex(l.m, 0.0), 2);
  EXPECT_EQ(RungIndex(l.m, 0.26), 3);
  EXPECT_EQ(RungIndex(l.m, 0.25), 2);  // the tie goes to native
  EXPECT_EQ(RungIndex(l.m, -0.25), 2);
  EXPECT_EQ(RungIndex(l.m, 0.8), 4);
  EXPECT_EQ(RungIndex(l.m, 5.0), 4);
  EXPECT_EQ(l.k.native, 2);
  EXPECT_EQ(l.k.rungs[RungIndex(l.k, std::log2(6.0 / 4))], 6);
  EXPECT_EQ(l.k.rungs[RungIndex(l.k, -1.0)], 2);
  std::string error;
  Ladder bad;
  EXPECT_FALSE(MakeLadder({0.5, 0.7, 1.5}, 1.0, &bad, &error));
  EXPECT_FALSE(MakeLadder({1, 0.5}, 1.0, &bad, &error));
}

// The design's table: from 2x the first rung change comes after ~14 turns
// and native after ~69; from 1.41x native after ~35.
TEST(TurnDecay, ReturnTimesMatchTheDesign) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  const auto turns_until = [&](double a, int rung) {
    for (int t = 1; t < 1000; ++t) {
      a = Decay(a, c.kappa);
      if (RungIndex(l.m, a) == rung) return t;
    }
    return -1;
  };
  EXPECT_EQ(turns_until(1.0, 3), 15);
  EXPECT_EQ(turns_until(1.0, 2), 70);
  EXPECT_EQ(turns_until(-1.0, 1), 15);
  EXPECT_EQ(turns_until(-1.0, 2), 70);
  EXPECT_EQ(turns_until(0.5, 2), 35);
  EXPECT_EQ(Decay(0.0, c.kappa), 0.0);
  // K from 6 returns to 4 too.
  double a = std::log2(6.0 / 4);
  int t = 0;
  while (l.k.rungs[RungIndex(l.k, a)] != 4 && t < 1000) {
    a = Decay(a, c.kappa);
    ++t;
  }
  EXPECT_GT(t, 30);
  EXPECT_LT(t, 40);
}

TEST(TurnPress, MovesARungOrRenews) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  bool renew = true;
  EXPECT_DOUBLE_EQ(Press(l.m, 0.0, true, c.renew_band, &renew), 0.5);
  EXPECT_FALSE(renew);
  // One turn after reaching 1.41x the anchor has barely decayed: a press
  // moves on to 2x.
  EXPECT_DOUBLE_EQ(Press(l.m, Decay(0.5, c.kappa), true, c.renew_band, &renew), 1.0);
  EXPECT_FALSE(renew);
  // Twenty turns later it has decayed past half the band: a renew.
  double a = 0.5;
  for (int i = 0; i < 20; ++i) a = Decay(a, c.kappa);
  EXPECT_EQ(RungIndex(l.m, a), 3);
  EXPECT_DOUBLE_EQ(Press(l.m, a, true, c.renew_band, &renew), 0.5);
  EXPECT_TRUE(renew);
  // At the top a press renews.
  EXPECT_DOUBLE_EQ(Press(l.m, 1.0, true, c.renew_band, &renew), 1.0);
  EXPECT_TRUE(renew);
  // The mirror image downward.
  EXPECT_DOUBLE_EQ(Press(l.m, 0.0, false, c.renew_band, &renew), -0.5);
  EXPECT_FALSE(renew);
  EXPECT_DOUBLE_EQ(Press(l.m, -a, false, c.renew_band, &renew), -0.5);
  EXPECT_TRUE(renew);
  EXPECT_DOUBLE_EQ(Press(l.m, -1.0, false, c.renew_band, &renew), -1.0);
  EXPECT_TRUE(renew);
  // Pressing against the side the anchor is on moves toward native.
  EXPECT_DOUBLE_EQ(Press(l.m, 0.5, false, c.renew_band, &renew), 0.0);
  EXPECT_FALSE(renew);
}

// A renew keeps the rung, so the settings sent do not change: no call.
// The L0 prior's three triggers (turn_prior): the anchor's rung now and after
// an expand or a compact press, as Encode presses and RungSettings rounds.
TEST(TurnPress, L0RungMovesFollowThePresses) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  const auto moves = [&](double a) {
    const L0Rungs r = L0RungMoves(t, l.k, a, c.renew_band);
    return std::vector<int>{r.now, r.up, r.down};
  };
  EXPECT_EQ(moves(0.0), (std::vector<int>{4, 6, 3}));
  EXPECT_EQ(moves(l.k.logs[4]), (std::vector<int>{8, 8, 6}));  // the top renews
  EXPECT_EQ(moves(l.k.logs[0]), (std::vector<int>{2, 3, 2}));  // the bottom renews
  // An anchor decayed past half the band renews on an expand: K stays.
  double a = l.k.logs[3];
  for (int i = 0; i < 20; ++i) a = Decay(a, c.kappa);
  ASSERT_EQ(RungIndex(l.k, a), 3);
  const L0Rungs r = L0RungMoves(t, l.k, a, c.renew_band);
  EXPECT_EQ(r.now, 6);
  EXPECT_EQ(r.up, 6);
  // The live cap bounds all three.
  t.k_live_cap = 4;
  EXPECT_EQ(moves(0.0), (std::vector<int>{4, 4, 3}));
}

TEST(TurnPress, RenewMakesNoSettingsChange) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.I_hat[2] = 40 * kMiB;
  std::vector<AnchorState> an = Native(t.num_levels);
  an[2].a = 0.5;
  for (int i = 0; i < 20; ++i) an[2].a = Decay(an[2].a, c.kappa);
  an[2].last_m = std::sqrt(2.0);
  const TurnSettings before = AnchorSettings(t, c, l.k, l.m, an);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[2] = Action::kExpand;
  std::vector<TurnNote> notes;
  const TurnSettings after = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_TRUE(notes[2].renewed);
  EXPECT_FALSE(notes[2].moved);
  EXPECT_EQ(after.m, before.m);
  EXPECT_EQ(after.K, before.K);
  EXPECT_DOUBLE_EQ(an[2].a, 0.5);
}

// The decay's step down goes through compact's guards; its step up never
// waits (it makes no merge due).
TEST(TurnDecay, StepsDownWaitForTheGuardsStepsUpNever) {
  TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 1000 * kMiB;
  const double edge_up = 0.2501;  // one decay step crosses 1.41 -> 1
  std::vector<AnchorState> an = Native(t.num_levels);
  // 1. Its previous drop still gliding: wait (one drop at a time).
  an[2].a = edge_up;
  an[2].glide_m = 1.6;
  std::vector<bool> w = DecayAnchors(t, c, l.k, l.m, &an);
  EXPECT_TRUE(w[2]);
  EXPECT_DOUBLE_EQ(an[2].a, edge_up);
  // 2. The glide done: the step goes ahead.
  an[2].glide_m = 0;
  w = DecayAnchors(t, c, l.k, l.m, &an);
  EXPECT_FALSE(w[2]);
  EXPECT_EQ(RungIndex(l.m, an[2].a), 2);
  // 3. L0 within four flushes of its slowdown: wait.
  an[2].a = edge_up;
  t.n0_all = 16;
  EXPECT_TRUE(DecayAnchors(t, c, l.k, l.m, &an)[2]);
  t.n0_all = 2;
  // 4. RocksDB's pending estimate now past the guard: wait.
  t.pending = 20 * kGiB;
  EXPECT_TRUE(DecayAnchors(t, c, l.k, l.m, &an)[2]);
  t.pending = 1 * kGiB;
  // 5. The level's own debt at the lower rung past the guard does not hold
  // the decay (it would hold the level above native for good); it does hold
  // compact's drop.
  c.guard_owed_bytes = 100 * kMiB;
  t.pending = 50 * kMiB;  // the tree itself is calm
  EXPECT_FALSE(DecayAnchors(t, c, l.k, l.m, &an)[2]);
  EXPECT_EQ(RungIndex(l.m, an[2].a), 2);
  {
    std::vector<AnchorState> fresh = Native(t.num_levels);
    std::vector<Action> acts = Holds(t.num_levels);
    acts[2] = Action::kCompact;
    std::vector<TurnNote> notes;
    Encode(t, c, l.k, l.m, acts, &fresh, &notes);
    EXPECT_TRUE(notes[2].guard_blocked);
  }
  c = Cfg();
  // 6. A decay inside a rung, or upward from below native, never waits,
  // whatever the guards say.
  t.n0_all = 19;
  an[2].a = 0.9;     // stays on the 2x rung
  an[3].a = -0.2501; // 0.71 -> 1: a rise
  an[3].glide_m = 0;
  w = DecayAnchors(t, c, l.k, l.m, &an);
  EXPECT_FALSE(w[2]);
  EXPECT_FALSE(w[3]);
  EXPECT_EQ(RungIndex(l.m, an[3].a), 2);
}

TEST(TurnDecay, TheFlagDecaysOrKeepsEveryAnchor) {
  // DECAY: DecayStep(true) is DecayAnchors; with false
  // every anchor, above or below native, stays where the presses left it.
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  std::vector<AnchorState> set = Native(t.num_levels);
  set[0].a = std::log2(8.0 / 4);  // K 8 from 4
  set[1].a = -0.5;                // L1 one rung below native
  set[2].a = 0.2501;              // one decay step from crossing down
  set[3].a = 1.0;                 // L3 at the top rung
  std::vector<AnchorState> on = set, off = set, ref = set;
  EXPECT_EQ(DecayStep(true, t, c, l.k, l.m, &on),
            DecayAnchors(t, c, l.k, l.m, &ref));
  for (int i = 0; i < t.num_levels; ++i) {
    EXPECT_DOUBLE_EQ(on[i].a, ref[i].a) << i;
  }
  EXPECT_LT(on[0].a, set[0].a);
  EXPECT_GT(on[1].a, set[1].a);
  for (int turn = 0; turn < 1000; ++turn) {
    const std::vector<bool> w = DecayStep(false, t, c, l.k, l.m, &off);
    EXPECT_EQ(w, std::vector<bool>(t.num_levels, false));
  }
  for (int i = 0; i < t.num_levels; ++i) {
    EXPECT_EQ(off[i].a, set[i].a) << i;
  }
  // So with the decay off, a press always moves a rung (an anchor on its
  // rung has nothing to renew), except at the ladder's end.
  bool renew = false;
  const double k6 = std::log2(6.0 / 4);
  EXPECT_DOUBLE_EQ(Press(l.k, k6, true, c.renew_band, &renew), std::log2(8.0 / 4));
  EXPECT_FALSE(renew);
  EXPECT_DOUBLE_EQ(Press(l.k, off[0].a, true, c.renew_band, &renew), off[0].a);
  EXPECT_TRUE(renew);
}

// --- Owed work and the glide ------------------------------------------------

TEST(TurnOwed, SplitsRocksDBsEstimateByLevel) {
  TurnTree t = Tree();
  t.n0_all = 4;
  t.B_all[0] = 32 * kMiB;
  t.B_all[1] = 150 * kMiB;
  const std::vector<double> m = {1, 1, 1, 1, 1, 1};
  const std::vector<double> d = OwedWork(t, m, 4);
  // L0 due: its bytes and the base level's.
  EXPECT_DOUBLE_EQ(d[0], 32 * kMiB + 150 * kMiB);
  // L1: 150 + 32 carried over 128, times (1 + B_2 / size).
  const double size = 182 * kMiB, excess = 54 * kMiB;
  EXPECT_NEAR(d[1], excess * (700 * kMiB / size + 1), 1);
  // L2: 700 + 54 is under 768: nothing.
  EXPECT_EQ(d[2], 0);
  EXPECT_EQ(d[3], 0);
  // A level over target above an empty level adds nothing (RocksDB too).
  TurnTree u = Tree();
  u.B_all[4] = 30000 * kMiB;
  u.B_all[5] = 0;
  EXPECT_EQ(OwedWork(u, m, 4)[4], 0);
  // L0 not due: nothing at L0 and no carry.
  EXPECT_EQ(OwedWork(Tree(), m, 4)[0], 0);
  // Smaller targets owe more.
  const std::vector<double> half = {1, 0.5, 1, 1, 1, 1};
  EXPECT_GT(OwedWork(Tree(), half, 4)[1], 0);
}

TEST(TurnGlide, BudgetFormulaAndFloor) {
  const TurnConfig c = Cfg();
  TurnTree t = Tree();
  const double r = 0.5 * 400 * kMiB / (1 + 4000.0 / 700.0);
  EXPECT_NEAR(GlideBudget(t, c, 2), r, 1);
  t.X_bar = 0;  // a read-only phase: at least one output file
  EXPECT_DOUBLE_EQ(GlideBudget(t, c, 2), 8 * kMiB);
}

// A level full at 2x cut to 0.71x releases at most R per turn, its target
// never rises, and the glide ends.
TEST(TurnGlide, ReleasesAtMostTheBudgetPerTurn) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 1536 * kMiB;
  AnchorState a;
  a.a = -0.5;
  a.last_m = 2.0;
  double prev_g = 2.0;
  int turns = 0;
  for (; turns < 100; ++turns) {
    const double rung = l.m.rungs[RungIndex(l.m, a.a)];
    const double target = GlideStep(t, c, 2, rung, &a);
    const double due = t.B[2] - target * t.C[2];
    EXPECT_LE(due, GlideBudget(t, c, 2) + 1) << "turn " << turns;
    EXPECT_LE(target, prev_g + 1e-12);
    prev_g = target;
    if (a.glide_m == 0) break;
    t.B[2] -= std::max(0.0, due);  // the slot releases what is due
  }
  EXPECT_GT(turns, 5);
  EXPECT_LT(turns, 60);
  EXPECT_EQ(a.glide_m, 0);
}

// The owner's 2x -> 1x check (2026-11-07): with inflow at or above R_i the
// glide must still end; each turn releases the inflow plus at most R_i.
TEST(TurnGlide, EndsEvenWhenInflowExceedsTheBudget) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 1536 * kMiB;
  AnchorState a;
  a.a = 0.0;  // the rung fell from 2x to native
  a.last_m = 2.0;
  const double backlog = t.B[2] - t.C[2];
  const double inflow = 2 * GlideBudget(t, c, 2);
  int turns = 0;
  for (; turns < 200; ++turns) {
    const double target = GlideStep(t, c, 2, 1.0, &a);
    const double due = std::max(0.0, t.B[2] - target * t.C[2]);
    EXPECT_LE(due, inflow + GlideBudget(t, c, 2) + 1) << "turn " << turns;
    t.B[2] += inflow - due;  // the inflow lands, the due part is released
    if (a.glide_m == 0) break;
  }
  EXPECT_EQ(a.glide_m, 0);
  EXPECT_LE(turns, static_cast<int>(backlog / GlideBudget(t, c, 2)) + 10);
}

// The decay's own step down is glided too.
TEST(TurnGlide, DecayStepDownIsGlided) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 1500 * kMiB;
  std::vector<AnchorState> an = Native(t.num_levels);
  an[2].a = 0.74;  // rounds to 1.41 now; it was at 2x last turn
  an[2].last_m = 2.0;
  std::vector<TurnNote> notes;
  const TurnSettings s = Encode(t, c, l.k, l.m, Holds(t.num_levels), &an, &notes);
  EXPECT_GT(s.m[2], std::sqrt(2.0));
  EXPECT_GT(an[2].glide_m, 0);
  EXPECT_LE(t.B[2] - s.m[2] * t.C[2], GlideBudget(t, c, 2) + 1);
}

// Raising a target needs no glide.
TEST(TurnGlide, UpwardMovesAreImmediate) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  std::vector<AnchorState> an = Native(t.num_levels);
  an[1].last_m = 1;
  std::vector<Action> acts = Holds(t.num_levels);
  acts[1] = Action::kExpand;
  std::vector<TurnNote> notes;
  const TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_DOUBLE_EQ(s.m[1], std::sqrt(2.0));
  EXPECT_EQ(an[1].glide_m, 0);
  EXPECT_TRUE(notes[1].moved);
}

// --- Compact, defer and the guards --------------------------------------------

TEST(TurnEncode, CompactMakesTheLevelDueWithinTheBudget) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[1] = t.B_all[1] = 120 * kMiB;
  t.score = {0.5, 120.0 / 128, 0.9, 0.88, 0.7, 0};
  std::vector<AnchorState> an = Native(t.num_levels);
  const TurnSettings base = AnchorSettings(t, c, l.k, l.m, an);
  EXPECT_TRUE(Masks(t, c, l.k, l.m, an, base)[1][1]);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[1] = Action::kCompact;
  std::vector<TurnNote> notes;
  const TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  const double score = t.B[1] / (s.m[1] * t.C[1]);
  EXPECT_GE(score, 1.0);
  EXPECT_NEAR(score, 1.1, 1e-9);  // sigma = max(1 + eps, 0.9 + eps)
  EXPECT_LE(t.B[1] - s.m[1] * t.C[1], GlideBudget(t, c, 1) + 1);
  EXPECT_TRUE(notes[1].overridden);
  EXPECT_TRUE(notes[1].moved);  // the anchor went one rung down
  EXPECT_DOUBLE_EQ(an[1].a, -0.5);
  EXPECT_TRUE(Admissible(t, s));
}

TEST(TurnEncode, L0CompactAndDefer) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.n0 = t.n0_all = 3;
  std::vector<AnchorState> an = Native(t.num_levels);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[0] = Action::kCompact;
  std::vector<TurnNote> notes;
  TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_EQ(s.K, 3);  // due now
  EXPECT_EQ(l.k.rungs[RungIndex(l.k, an[0].a)], 3);  // anchor one rung down
  an = Native(t.num_levels);
  acts[0] = Action::kDefer;
  s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_EQ(s.K, 3 + 4 + 1);  // n0 + ceil(f_hat) + 1
  EXPECT_EQ(an[0].a, 0);      // defer moves no anchor
}

TEST(TurnEncode, DeferHoldsThroughTheTurn) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 760 * kMiB;
  std::vector<AnchorState> an = Native(t.num_levels);
  const TurnSettings base = AnchorSettings(t, c, l.k, l.m, an);
  ASSERT_TRUE(Masks(t, c, l.k, l.m, an, base)[2][2]);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[2] = Action::kDefer;
  std::vector<TurnNote> notes;
  const TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  // With the expected inflow landed, the level is still not due.
  EXPECT_LT((t.B[2] + t.I_hat[2]) / (s.m[2] * t.C[2]), 1.0);
  EXPECT_TRUE(Admissible(t, s));
  // A top-up when the inflow runs ahead.
  double m = s.m[2];
  EXPECT_TRUE(TopUpLevel(t.C[2] * m * 0.97, t.C[2], 20 * kMiB, c.margin,
                         s.m[3] * t.T, &m));
  EXPECT_GT(m, s.m[2]);
  // Beyond the fork's 2x the defer breaks.
  double m2 = 1.9;
  EXPECT_FALSE(TopUpLevel(t.C[2] * 1.9, t.C[2], 300 * kMiB, c.margin, 12, &m2));
  EXPECT_DOUBLE_EQ(m2, 1.9);
}

TEST(TurnEncode, TopUpL0) {
  int K = 5;
  EXPECT_TRUE(TopUpL0(3, 1.2, 8, 18, &K));
  EXPECT_EQ(K, 5);  // not within a file of due
  EXPECT_TRUE(TopUpL0(4, 1.2, 8, 18, &K));
  EXPECT_EQ(K, 7);
  EXPECT_FALSE(TopUpL0(7, 2, 8, 18, &K));
  EXPECT_EQ(K, 7);
}

TEST(TurnEncode, GuardsBlockTheDropButNotCompactNow) {
  TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[3] = t.B_all[3] = 4500 * kMiB;
  t.score = {0.5, 0.78, 0.91, 4500.0 / 4608, 0.72, 0};
  // Owed work at the lower anchor beyond the guard.
  c.guard_owed_bytes = 1 * kGiB;
  std::vector<AnchorState> an = Native(t.num_levels);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[3] = Action::kCompact;
  std::vector<TurnNote> notes;
  TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_TRUE(notes[3].guard_blocked);
  EXPECT_EQ(an[3].a, 0);
  EXPECT_TRUE(notes[3].overridden);
  EXPECT_GE(t.B[3] / (s.m[3] * t.C[3]), 1.0);  // still due now
  // L0 near its slowdown blocks a deeper drop.
  c = Cfg();
  t.n0_all = 16;
  an = Native(t.num_levels);
  s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_TRUE(notes[3].guard_blocked);
  // One drop at a time: a running glide blocks the next.
  t.n0_all = 2;
  an = Native(t.num_levels);
  an[3].glide_m = 1.2;
  an[3].last_m = 1.2;
  s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_TRUE(notes[3].guard_blocked);
  // With none of them, the drop goes ahead.
  an = Native(t.num_levels);
  s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  EXPECT_FALSE(notes[3].guard_blocked);
  EXPECT_DOUBLE_EQ(an[3].a, -0.5);
}

// After compact-now the glide carries the anchor's drop on, at most R_i a
// turn, down to the new rung; after a defer the postponed work is paid the
// same way.
TEST(TurnEncode, CompactAndDeferHandOverToTheGlide) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.B[2] = t.B_all[2] = 760 * kMiB;
  t.score = {0.5, 0.78, 760.0 / 768, 0.87, 0.72, 0};
  std::vector<AnchorState> an = Native(t.num_levels);
  std::vector<Action> acts = Holds(t.num_levels);
  acts[2] = Action::kCompact;
  std::vector<TurnNote> notes;
  TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  ASSERT_DOUBLE_EQ(an[2].a, -0.5);
  double prev = s.m[2];
  int turns = 0;
  for (; turns < 80; ++turns) {
    t.B[2] -= std::max(0.0, t.B[2] - s.m[2] * t.C[2]);  // released
    s = Encode(t, c, l.k, l.m, Holds(t.num_levels), &an, &notes);
    EXPECT_LE(t.B[2] - s.m[2] * t.C[2], GlideBudget(t, c, 2) + 1);
    EXPECT_LE(s.m[2], prev + 1e-12);
    prev = s.m[2];
    if (an[2].glide_m == 0) break;
  }
  EXPECT_GT(turns, 2);
  EXPECT_NEAR(s.m[2], std::sqrt(0.5), 1e-12);
  // Defer, then hold: the next turn releases at most R_i.
  t = Tree();
  t.B[2] = t.B_all[2] = 760 * kMiB;
  t.I_hat[2] = 150 * kMiB;  // a backlog of several turns' budget
  an = Native(t.num_levels);
  acts[2] = Action::kDefer;
  s = Encode(t, c, l.k, l.m, acts, &an, &notes);
  t.B[2] += t.I_hat[2];  // the inflow landed and nothing left
  s = Encode(t, c, l.k, l.m, Holds(t.num_levels), &an, &notes);
  EXPECT_GT(an[2].glide_m, 0);
  EXPECT_LE(t.B[2] - s.m[2] * t.C[2], GlideBudget(t, c, 2) + 1);
  EXPECT_GT(t.B[2] - s.m[2] * t.C[2], 0);  // but it does pay
}

// --- Masks ------------------------------------------------------------------

TEST(TurnMasks, FollowTheDesign) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  std::vector<AnchorState> an = Native(t.num_levels);
  TurnSettings base = AnchorSettings(t, c, l.k, l.m, an);
  std::vector<TurnMask> mk = Masks(t, c, l.k, l.m, an, base);
  ASSERT_EQ(mk.size(), 6u);
  // Hold always; the last level (L4) and those below only hold.
  for (int i = 0; i < 6; ++i) EXPECT_TRUE(mk[i][0]);
  for (int i = 4; i < 6; ++i) {
    EXPECT_FALSE(mk[i][1] || mk[i][2] || mk[i][3]) << i;
  }
  // L1 at 78% with 60 MiB to land (125%): compact, defer and expand.
  EXPECT_TRUE(mk[1][1]);
  EXPECT_TRUE(mk[1][2]);
  EXPECT_TRUE(mk[1][3]);
  // With 10 MiB to land (86%) it would not compact this turn: no defer.
  t.I_hat[1] = 10 * kMiB;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[1][2]);
  t.I_hat[1] = 60 * kMiB;
  // A level under ~55% cannot be made due (the fork's floor 0.5).
  t.B[1] = 64 * kMiB;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[1][1]);
  // Deep safety: above half the soft limit no deep defer or expand.
  t = Tree();
  t.B[2] = 760 * kMiB;
  t.pending = 40 * kGiB;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[2][2]);
  EXPECT_FALSE(mk[2][3]);
  EXPECT_TRUE(mk[0][3]);  // L0 is not a deep level
  // ... but bringing a shrunken level back toward native stays allowed.
  {
    std::vector<AnchorState> low = Native(t.num_levels);
    low[2].a = -1.0;
    const auto mk2 = Masks(t, c, l.k, l.m, low, AnchorSettings(t, c, l.k, l.m, low));
    EXPECT_TRUE(mk2[2][3]);
    EXPECT_FALSE(mk2[2][2]);
  }
  // L0 within two flushes of slowdown: no L0 defer or expand.
  t = Tree();
  t.n0 = t.n0_all = 18;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[0][2]);
  EXPECT_FALSE(mk[0][3]);
  EXPECT_TRUE(mk[0][1]);
  // ... but K back up toward native (2 -> 3) stays allowed.
  {
    std::vector<AnchorState> low = Native(t.num_levels);
    low[0].a = -1.0;
    const auto mk2 = Masks(t, c, l.k, l.m, low, AnchorSettings(t, c, l.k, l.m, low));
    EXPECT_TRUE(mk2[0][3]);
  }
  // L0 defer beyond the live cap is masked.
  t = Tree();
  t.n0 = t.n0_all = 5;
  t.f_hat = 3.2;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[0][2]);  // 5 + 4 + 1 = 10 > 8
  // Expand at a rung above the live cap is masked.
  t = Tree();
  t.k_live_cap = 5;
  mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  EXPECT_FALSE(mk[0][3]);  // 4 -> 6 > 5
  // Levels past the acting count only hold.
  TurnConfig c3 = c;
  c3.acting = 3;
  mk = Masks(Tree(), c3, l.k, l.m, an, AnchorSettings(Tree(), c3, l.k, l.m, an));
  EXPECT_FALSE(mk[3][1] || mk[3][2] || mk[3][3]);
}

// COMPACT_FLOOR (turn_compact_floor): off, L0 compact is allowed whenever L0
// has a file, at the lowest rung too, where it sends K = max(1, n0) with no
// rung change; on, it is masked while the anchor rounds to the lowest rung
// (decayed from it too) and allowed one rung up. Other levels are unchanged.
TEST(TurnMasks, CompactFloorBlocksL0CompactAtTheLowestRung) {
  TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.n0 = t.n0_all = 1;
  std::vector<AnchorState> an = Native(t.num_levels);
  const auto compact_ok = [&](const TurnConfig& cfg, double a) {
    an[0].a = a;
    return Masks(t, cfg, l.k, l.m, an, AnchorSettings(t, cfg, l.k, l.m, an))[0][1];
  };
  const double k2 = std::log2(2.0 / 4), k3 = std::log2(3.0 / 4);
  EXPECT_TRUE(compact_ok(c, k2));
  EXPECT_TRUE(compact_ok(c, k3));
  EXPECT_TRUE(compact_ok(c, 0));
  // Off: compact at the lowest rung sends K = 1, below the ladder.
  {
    std::vector<AnchorState> low = Native(t.num_levels);
    low[0].a = k2;
    std::vector<Action> acts = Holds(t.num_levels);
    acts[0] = Action::kCompact;
    std::vector<TurnNote> notes;
    const TurnSettings s = Encode(t, c, l.k, l.m, acts, &low, &notes);
    EXPECT_EQ(s.K, 1);
    EXPECT_EQ(RungIndex(l.k, low[0].a), 0);
    EXPECT_FALSE(notes[0].moved);
  }
  TurnConfig f = c;
  f.compact_floor = true;
  EXPECT_FALSE(compact_ok(f, k2));
  EXPECT_FALSE(compact_ok(f, k2 + 0.25));  // decayed, still rounds to K = 2
  EXPECT_TRUE(compact_ok(f, k3));
  EXPECT_TRUE(compact_ok(f, 0));
  // No file: masked either way.
  t.n0 = t.n0_all = 0;
  EXPECT_FALSE(compact_ok(c, k3));
  EXPECT_FALSE(compact_ok(f, k3));
  // Only L0's mask moves.
  t = Tree();
  an = Native(t.num_levels);
  an[0].a = k2;
  const auto off = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  const auto on = Masks(t, f, l.k, l.m, an, AnchorSettings(t, f, l.k, l.m, an));
  for (int i = 0; i < t.num_levels; ++i) {
    for (int a = 0; a < kNumActions; ++a) {
      if (i == 0 && a == static_cast<int>(Action::kCompact)) continue;
      EXPECT_EQ(off[i][a], on[i][a]) << i << " " << a;
    }
  }
  EXPECT_TRUE(off[0][1]);
  EXPECT_FALSE(on[0][1]);
}

// COMPACT_STEP (turn_compact_step): on, L0's compact only
// steps the K anchor one rung down. It is allowed at every rung above the
// lowest, with or without a file in L0, and masked at the lowest; Encode
// sends the new rung's K with no one-turn override, so K never leaves the
// ladder. Off, compact also sends K = max(1, n0) for the turn. Other levels
// and L0's other actions are unchanged.
TEST(TurnMasks, CompactStepMovesTheRungOnly) {
  const TurnConfig c = Cfg();
  TurnConfig step = c;
  step.compact_step = true;
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.n0 = t.n0_all = 1;
  std::vector<AnchorState> an = Native(t.num_levels);
  const auto compact_ok = [&](const TurnConfig& cfg, double a) {
    an[0].a = a;
    return Masks(t, cfg, l.k, l.m, an, AnchorSettings(t, cfg, l.k, l.m, an))[0][1];
  };
  const double k2 = std::log2(2.0 / 4), k3 = std::log2(3.0 / 4);
  // Masked at the lowest rung (decayed from it too), allowed above it.
  EXPECT_FALSE(compact_ok(step, k2));
  EXPECT_FALSE(compact_ok(step, k2 + 0.25));
  EXPECT_TRUE(compact_ok(step, k3));
  EXPECT_TRUE(compact_ok(step, 0));
  EXPECT_TRUE(compact_ok(step, 1));  // K = 8, the top rung
  // No file in L0: still allowed above the lowest rung (off: masked).
  t.n0 = t.n0_all = 0;
  EXPECT_TRUE(compact_ok(step, k3));
  EXPECT_TRUE(compact_ok(step, 1));
  EXPECT_FALSE(compact_ok(step, k2));
  EXPECT_FALSE(compact_ok(c, k3));
  // The floor flag changes nothing once the step is on.
  TurnConfig both = step;
  both.compact_floor = true;
  EXPECT_FALSE(compact_ok(both, k2));
  EXPECT_TRUE(compact_ok(both, k3));

  // Encode: one rung down, the K sent is that rung's, no override, whatever
  // L0 holds.
  for (int n0 : {0, 1, 3}) {
    t.n0 = t.n0_all = n0;
    std::vector<AnchorState> a = Native(t.num_levels);  // K = 4
    std::vector<Action> acts = Holds(t.num_levels);
    acts[0] = Action::kCompact;
    std::vector<TurnNote> notes;
    const TurnSettings s = Encode(t, step, l.k, l.m, acts, &a, &notes);
    EXPECT_EQ(s.K, 3) << n0;
    EXPECT_DOUBLE_EQ(a[0].a, k3) << n0;
    EXPECT_TRUE(notes[0].moved) << n0;
    EXPECT_FALSE(notes[0].overridden) << n0;
  }
  // Four presses from the top rung walk the whole ladder, 8 -> 6 -> 4 -> 3
  // -> 2, never below it.
  {
    t.n0 = t.n0_all = 1;
    std::vector<AnchorState> a = Native(t.num_levels);
    a[0].a = 1;
    std::vector<Action> acts = Holds(t.num_levels);
    acts[0] = Action::kCompact;
    std::vector<TurnNote> notes;
    for (int want : {6, 4, 3, 2}) {
      const TurnSettings s = Encode(t, step, l.k, l.m, acts, &a, &notes);
      EXPECT_EQ(s.K, want);
    }
    EXPECT_FALSE(Masks(t, step, l.k, l.m, a,
                       AnchorSettings(t, step, l.k, l.m, a))[0][1]);
  }
  // Off, the same press also merges at once: K = max(1, n0) for the turn.
  {
    t.n0 = t.n0_all = 1;
    std::vector<AnchorState> a = Native(t.num_levels);
    std::vector<Action> acts = Holds(t.num_levels);
    acts[0] = Action::kCompact;
    std::vector<TurnNote> notes;
    const TurnSettings s = Encode(t, c, l.k, l.m, acts, &a, &notes);
    EXPECT_EQ(s.K, 1);
    EXPECT_DOUBLE_EQ(a[0].a, k3);
    EXPECT_TRUE(notes[0].overridden);
  }
  // Only L0's compact moves in the masks.
  t = Tree();
  an = Native(t.num_levels);
  const auto off = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  const auto on = Masks(t, step, l.k, l.m, an, AnchorSettings(t, step, l.k, l.m, an));
  for (int i = 0; i < t.num_levels; ++i) {
    for (int a = 0; a < kNumActions; ++a) {
      if (i == 0 && a == static_cast<int>(Action::kCompact)) continue;
      EXPECT_EQ(off[i][a], on[i][a]) << i << " " << a;
    }
  }
  // A deeper level's compact keeps its compact-now override.
  {
    std::vector<AnchorState> a0 = Native(t.num_levels), a1 = a0;
    std::vector<Action> acts = Holds(t.num_levels);
    acts[2] = Action::kCompact;
    std::vector<TurnNote> n0, n1;
    const TurnSettings s0 = Encode(t, c, l.k, l.m, acts, &a0, &n0);
    const TurnSettings s1 = Encode(t, step, l.k, l.m, acts, &a1, &n1);
    EXPECT_EQ(s0.K, s1.K);
    EXPECT_EQ(s0.m, s1.m);
    EXPECT_EQ(n0[2].overridden, n1[2].overridden);
  }
}

// R4: with only reads there is no inflow; defer and expand are masked, compact
// still acts, and the anchors still decay.
TEST(TurnMasks, ReadOnlyPhase) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  TurnTree t = Tree();
  t.f_hat = 0;
  t.I_hat.assign(6, 0);
  t.X_bar = 0;
  std::vector<AnchorState> an = Native(t.num_levels);
  an[1].a = 0.5;
  const auto mk = Masks(t, c, l.k, l.m, an, AnchorSettings(t, c, l.k, l.m, an));
  for (int i = 0; i < 4; ++i) {
    EXPECT_FALSE(mk[i][2]) << i;
    EXPECT_FALSE(mk[i][3]) << i;
  }
  EXPECT_TRUE(mk[0][1]);
  EXPECT_TRUE(mk[2][1]);
  EXPECT_LT(Decay(an[1].a, c.kappa), an[1].a);
}

// Whatever the actions, what is sent is admissible to the fork.
TEST(TurnEncode, AlwaysAdmissible) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  std::mt19937_64 rng(7);
  std::uniform_real_distribution<double> u(0, 1);
  for (int iter = 0; iter < 2000; ++iter) {
    TurnTree t = Tree();
    t.T = 2 + 8 * u(rng);
    for (int i = 1; i < 5; ++i) {
      t.C[i] = 128 * kMiB * std::pow(t.T, i - 1);
      t.B[i] = t.B_all[i] = t.C[i] * 2.2 * u(rng);
      t.score[i] = t.B[i] / t.C[i];
    }
    t.n0 = t.n0_all = static_cast<int>(u(rng) * 20);
    std::vector<AnchorState> an = Native(t.num_levels);
    for (int i = 0; i < 5; ++i) {
      an[i].a = -1 + 2 * u(rng);
      an[i].last_m = i >= 1 ? 0.5 + 1.5 * u(rng) : 0;
    }
    for (int step = 0; step < 3; ++step) {
      const TurnSettings base = AnchorSettings(t, c, l.k, l.m, an);
      const auto mk = Masks(t, c, l.k, l.m, an, base);
      std::vector<Action> acts = Holds(t.num_levels);
      for (int i = 0; i < t.num_levels; ++i) {
        const int a = static_cast<int>(u(rng) * 4);
        if (mk[i][a]) acts[i] = static_cast<Action>(a);
      }
      std::vector<TurnNote> notes;
      const TurnSettings s = Encode(t, c, l.k, l.m, acts, &an, &notes);
      ASSERT_TRUE(Admissible(t, s)) << "iteration " << iter;
      for (AnchorState& x : an) x.a = Decay(x.a, c.kappa);
    }
  }
}

// --- State --------------------------------------------------------------------

TEST(TurnState, InputsAndPadding) {
  const TurnConfig c = Cfg();
  const Ladders l = MakeLadders(c);
  EXPECT_EQ(TurnFeatureNames(5).size(), 35u);
  EXPECT_EQ(TurnBlockStart(0), 6);
  EXPECT_EQ(TurnBlockStart(1), 11);
  EXPECT_EQ(TurnBlockStart(4), 29);
  TurnTree t = Tree();
  t.last = 2;  // L3 and below hold no bytes
  t.B[3] = t.B[4] = 0;
  std::vector<AnchorState> an = Native(t.num_levels);
  const TurnSettings base = AnchorSettings(t, c, l.k, l.m, an);
  const TurnSettings rungs = RungSettings(t, l.k, l.m, an);
  const std::vector<double> x = TurnInputs(t, c, an, base, rungs,
                                           OwedWork(t, rungs.m, rungs.K));
  ASSERT_EQ(x.size(), 35u);
  EXPECT_DOUBLE_EQ(x[0], 0.3);
  EXPECT_DOUBLE_EQ(x[6], 2.0 / 4);
  EXPECT_DOUBLE_EQ(x[11], 100.0 / 128);
  EXPECT_DOUBLE_EQ(x[11 + 4], 60.0 / 128);
  for (int k = TurnBlockStart(3); k < 35; ++k) EXPECT_EQ(x[k], 0) << k;
  for (double v : x) EXPECT_TRUE(std::isfinite(v));
}

// --- Networks, choice, exploration --------------------------------------------

TurnNet LinearNet(int arch, int acting) {
  TurnNet net;
  net.version = 3;
  net.arch = arch;
  net.acting = acting;
  net.n_in = static_cast<uint32_t>(TurnFeatureNames(acting).size());
  net.clip = 5;
  net.mean.assign(net.n_in, 0.0);
  net.scale.assign(net.n_in, 1.0);
  const int nets = arch == 0 ? 1 : acting;
  for (int k = 0; k < nets; ++k) {
    TurnNet::Net n;
    for (uint32_t j = 0; j < net.n_in; ++j) n.view.push_back(static_cast<int32_t>(j));
    TurnNet::Layer h;
    h.out = 2;
    h.in = net.n_in;
    h.w.assign(h.out * h.in, 0.0);
    h.w[0] = 1.0;  // unit 0 reads input 0
    h.b = {0, 1};
    TurnNet::Layer o;
    o.out = arch == 0 ? 1 + 4 * acting : 4;
    o.in = 2;
    o.w.assign(o.out * o.in, 0.0);
    o.b.resize(o.out);
    for (uint32_t r = 0; r < o.out; ++r) {
      o.b[r] = arch == 0 ? r : 10 * k + r;
      o.w[r * 2 + 0] = r == 1 ? 1.0 : 0.0;  // output 1 also reads unit 0
    }
    n.layers = {h, o};
    net.nets.push_back(n);
  }
  return net;
}

std::string TempPath(const std::string& name) {
  return (std::filesystem::temp_directory_path() /
          ("turn_test_" + std::to_string(::getpid()) + "_" + name))
      .string();
}

TEST(TurnNetTest, RoundTripScoresAndRefusals) {
  for (int arch : {0, 1}) {
    const TurnNet net = LinearNet(arch, 5);
    const std::string path = TempPath("w" + std::to_string(arch));
    {
      std::ofstream out(path, std::ios::binary);
      out << SerializeTurnNet(net);
    }
    uint64_t version = 0;
    std::string error;
    ASSERT_EQ(PeekTurnVersion(path, &version, &error), TurnLoad::kOk) << error;
    EXPECT_EQ(version, 3u);
    TurnNet loaded;
    ASSERT_EQ(LoadTurnNet(path, arch, 5, &loaded, &error), TurnLoad::kOk) << error;
    std::vector<double> x(35, 0.0);
    x[0] = 9.0;  // clipped to 5
    const auto s = TurnScores(&loaded, x, 5);
    ASSERT_EQ(s.size(), 5u);
    if (arch == 0) {
      // out[r] = r, plus unit 0 (= 5) on output 1: level 0's hold.
      EXPECT_DOUBLE_EQ(s[0][0], 1 + 5);
      EXPECT_DOUBLE_EQ(s[0][1], 2);
      EXPECT_DOUBLE_EQ(s[2][3], 1 + 4 * 2 + 3);
    } else {
      EXPECT_DOUBLE_EQ(s[0][1], 1 + 5);
      EXPECT_DOUBLE_EQ(s[3][2], 32);
    }
    // The other arch, another level count, a flipped byte: refused.
    EXPECT_EQ(LoadTurnNet(path, 1 - arch, 5, &loaded, &error), TurnLoad::kInvalid);
    EXPECT_EQ(LoadTurnNet(path, arch, 4, &loaded, &error), TurnLoad::kInvalid);
    std::string bytes = SerializeTurnNet(net);
    bytes[40] ^= 1;
    {
      std::ofstream out(path, std::ios::binary);
      out << bytes;
    }
    EXPECT_EQ(LoadTurnNet(path, arch, 5, &loaded, &error), TurnLoad::kInvalid);
    std::remove(path.c_str());
    EXPECT_EQ(LoadTurnNet(path, arch, 5, &loaded, &error), TurnLoad::kMissing);
  }
  // No net: every score ties at 0.
  for (const auto& level : TurnScores(nullptr, std::vector<double>(35, 1.0), 5)) {
    for (double v : level) EXPECT_EQ(v, 0);
  }
}

TEST(TurnChoice, GreedyRespectsMasksAndTiesGoToHold) {
  std::vector<std::array<double, kNumActions>> scores = {
      {0, 0, 0, 0}, {1, 5, 3, 2}, {1, 5, 3, 2}, {2, 2, 1, 0}};
  std::vector<TurnMask> masks(4, TurnMask{true, true, true, true});
  masks[2][1] = false;
  const std::vector<Action> g = Greedy(scores, masks);
  EXPECT_EQ(g[0], Action::kHold);
  EXPECT_EQ(g[1], Action::kCompact);
  EXPECT_EQ(g[2], Action::kDefer);
  EXPECT_EQ(g[3], Action::kHold);
}

TEST(TurnChoice, ExploreOneLevelOnly) {
  std::mt19937_64 rng(1);
  std::vector<TurnMask> masks(5, TurnMask{true, false, false, false});
  masks[1] = {true, true, false, true};
  masks[3] = {true, false, true, false};
  int seen1 = 0, seen3 = 0;
  for (int i = 0; i < 400; ++i) {
    std::vector<Action> acts(5, Action::kHold);
    const int level = Explore(1.0, masks, &acts, &rng);
    ASSERT_TRUE(level == 1 || level == 3);
    seen1 += level == 1;
    seen3 += level == 3;
    for (int j = 0; j < 5; ++j) {
      EXPECT_TRUE(masks[j][static_cast<int>(acts[j])]);
      if (j != level) {
        EXPECT_EQ(acts[j], Action::kHold);
      }
    }
  }
  EXPECT_GT(seen1, 100);
  EXPECT_GT(seen3, 100);
  std::vector<Action> acts(5, Action::kHold);
  EXPECT_EQ(Explore(0.0, masks, &acts, &rng), -1);
  const TurnConfig c = Cfg();
  EXPECT_DOUBLE_EQ(ExploreRate(c, 0), 0.5);
  EXPECT_NEAR(ExploreRate(c, 100), 0.275, 1e-12);
  EXPECT_DOUBLE_EQ(ExploreRate(c, 200), 0.05);
  EXPECT_DOUBLE_EQ(ExploreRate(c, 5000), 0.05);
}

// The level clock (turn_periods): phases, who decides, the masks, and the
// slow levels' own exploration.
TEST(TurnClock, PhasesAndDecisionTurns) {
  EXPECT_EQ(LevelPhase(1), 0);
  EXPECT_EQ(LevelPhase(16), 12);
  EXPECT_EQ(LevelPhase(64), 48);
  TurnConfig c = Cfg();
  // No periods: every level decides every turn, and none is slow.
  for (int level = 0; level < 5; ++level) {
    EXPECT_TRUE(Decides(c, level, 7));
    EXPECT_FALSE(SlowLevel(c, level));
  }
  c.acting = 4;
  c.periods = {1, 16, 64, 0};
  EXPECT_FALSE(SlowLevel(c, 0));
  EXPECT_TRUE(SlowLevel(c, 1));
  EXPECT_TRUE(SlowLevel(c, 3));
  EXPECT_FALSE(SlowLevel(c, 4));  // beyond the acting levels
  int both = 0, l1 = 0, l2 = 0;
  for (uint64_t t = 0; t < 640; ++t) {
    EXPECT_TRUE(Decides(c, 0, t));
    EXPECT_FALSE(Decides(c, 3, t));  // period 0: never
    EXPECT_TRUE(Decides(c, 4, t));
    EXPECT_EQ(Decides(c, 1, t), t % 16 == 12) << t;
    EXPECT_EQ(Decides(c, 2, t), t % 64 == 48) << t;
    l1 += Decides(c, 1, t);
    l2 += Decides(c, 2, t);
    both += Decides(c, 1, t) && Decides(c, 2, t);
  }
  EXPECT_EQ(l1, 40);
  EXPECT_EQ(l2, 10);
  EXPECT_EQ(both, 0);  // staggered
}

TEST(TurnClock, MasksHoldBetweenDecisionsAndDropDefer) {
  TurnConfig c = Cfg();
  c.acting = 3;
  c.periods = {1, 16, 64};
  const TurnMask all{true, true, true, true};
  const TurnMask hold{true, false, false, false};
  const TurnMask no_defer{true, true, false, true};
  std::vector<TurnMask> masks(4, all);  // one level beyond the acting ones
  std::vector<bool> decided = ClockMasks(c, 5, &masks);
  EXPECT_EQ(decided, (std::vector<bool>{true, false, false, true}));
  EXPECT_EQ(masks[0], all);
  EXPECT_EQ(masks[1], hold);
  EXPECT_EQ(masks[2], hold);
  EXPECT_EQ(masks[3], all);
  masks.assign(4, all);
  decided = ClockMasks(c, 12, &masks);
  EXPECT_EQ(decided, (std::vector<bool>{true, true, false, true}));
  EXPECT_EQ(masks[1], no_defer);
  EXPECT_EQ(masks[2], hold);
  masks.assign(4, all);
  decided = ClockMasks(c, 48, &masks);
  EXPECT_EQ(decided, (std::vector<bool>{true, false, true, true}));
  EXPECT_EQ(masks[2], no_defer);
  // The fast levels' exploration never sees a slow level.
  const std::vector<TurnMask> fast = FastMasks(c, std::vector<TurnMask>(4, all));
  EXPECT_EQ(fast[0], all);
  EXPECT_EQ(fast[1], hold);
  EXPECT_EQ(fast[2], hold);
  EXPECT_EQ(fast[3], all);
  // No periods: the masks are untouched.
  TurnConfig plain = Cfg();
  masks.assign(4, all);
  decided = ClockMasks(plain, 5, &masks);
  EXPECT_EQ(decided, (std::vector<bool>(4, true)));
  for (const TurnMask& m : masks) EXPECT_EQ(m, all);
  EXPECT_EQ(FastMasks(plain, masks), masks);
}

TEST(TurnClock, SlowLevelsExploreAtTheirOwnDecisionsOnly) {
  TurnConfig c = Cfg();
  c.acting = 3;
  c.periods = {1, 16, 64};
  c.slow_explore_decisions = 10;
  EXPECT_DOUBLE_EQ(SlowExploreRate(c, 0), 0.5);
  EXPECT_DOUBLE_EQ(SlowExploreRate(c, 5), 0.275);
  EXPECT_DOUBLE_EQ(SlowExploreRate(c, 10), 0.05);
  EXPECT_DOUBLE_EQ(SlowExploreRate(c, 99), 0.05);
  const TurnMask open{true, true, false, true};
  const TurnMask hold{true, false, false, false};
  std::mt19937_64 rng(7);
  std::vector<uint64_t> decisions;
  int explored1 = 0, moved0 = 0;
  for (int n = 0; n < 4000; ++n) {
    // L1 decides (three allowed actions), L2 does not, L0 is fast.
    const std::vector<TurnMask> masks = {open, open, hold};
    const std::vector<bool> decided = {true, true, false};
    std::vector<Action> actions(3, Action::kHold);
    if (n % 10 == 0) decisions.assign(3, 0);  // rate 0.5 .. 0.095 again
    const std::vector<bool> ex =
        ExploreSlow(c, masks, decided, &decisions, &actions, &rng);
    ASSERT_EQ(ex.size(), 3u);
    EXPECT_FALSE(ex[0]);
    EXPECT_FALSE(ex[2]);
    moved0 += actions[0] != Action::kHold;
    EXPECT_EQ(actions[2], Action::kHold);
    EXPECT_NE(actions[1], Action::kDefer);
    if (!ex[1]) {
      EXPECT_EQ(actions[1], Action::kHold);
    }
    explored1 += ex[1];
  }
  EXPECT_EQ(moved0, 0);            // the fast level is never touched here
  EXPECT_EQ(decisions[1], 10u);    // one decision counted per deciding turn
  EXPECT_EQ(decisions[2], 0u);
  // Mean rate over decisions 0..9 is 0.2975: 4000 draws give about 1190.
  EXPECT_GT(explored1, 1050);
  EXPECT_LT(explored1, 1330);
  // A deciding slow level with one allowed action counts no decision.
  decisions.assign(3, 0);
  std::vector<Action> actions(3, Action::kHold);
  ExploreSlow(c, {open, hold, hold}, {true, true, true}, &decisions, &actions,
              &rng);
  EXPECT_EQ(decisions[1], 0u);
}

}  // namespace
}  // namespace rlc
