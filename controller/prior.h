// The analytic prior b (PATHWAYS H §7): the expected change in normalised
// COST over one turnover from taking an action instead of holding, in the
// units of Q (cost / (c_w C_j); L0: C_0 = K0_cfg F). Lower is better:
// positive means the action costs more than holding. G §3 writes Q as a
// reward (Q = b + f + delta), so a learner must negate b before adding it;
// the logs name it prior_cost to keep the sign explicit (an owner decision
// is pending on which section changes). Hold is 0. Each value is clipped to
// [-b_max, b_max]. A term whose input is not measured yet contributes 0, so
// a cold start prices every action by what is known.
#pragma once

#include <array>

#include "actions.h"
#include "config.h"
#include "state.h"

namespace rlc {

using Values = std::array<double, kNumActions>;

// Level >= 1 at control c. Terms (per unit of C_j):
//   compact  beta_W (1 - xi_j) released * c_j (o_now - f_j), the merged
//            share of the bytes released early (trivial moves write nothing,
//            Lemma D.7), o_now = T phi_{j+1}/phi_j (Lemma D.8) and
//            released = phi - m', plus the
//            slot-blocking charge: the share of L0's priced reads the job
//            would take over if L0 falls due while it holds the slot (D §4)
//   defer    beta_S sigma_j (1 - rho~_j) (m' - m)^+, the garbage kept, plus
//            beta_W (rho_{j+1} + o_{j+1}) times the burst that overflows the
//            level below (Theorem A.1)
//   expand   beta_S sigma_j (m' - m)^+, the space bound (Lemma D.14)
Values LevelPrior(const View& v, int level, const LevelControl& c,
                  const Config& cfg);

// L0 at control c, from Proposition D.11's cost per operation
// g(K) = beta_W c_w u m_1 C_1 / (K F) + beta_R (K / 2) [q_pt (c_f + eps c_blk
// + rho_g c_open) + q_sc (c_sk + rho_s c_open)], over one L0 turnover, with
// rho_g and rho_s L0's reopens per probe and per seek (D-21; L0ReadPrices).
// Compact is charged the write change at once but credited the read change
// only while the slot is idle (H §7).
Values L0Prior(const View& v, const L0Control& c, const Config& cfg);

// Proposition D.11's two terms of g(K), per operation, from the View's
// counts (L0Prior's and the turn mode's L0 prior's one formula): the L0->L1
// merges' write (cost model 2 adds each merge's job price and L1's overlap
// read) and the reads' K / 2 L0 files.
struct L0Costs {
  double write = 0, read = 0;
  double total() const { return write + read; }
};
L0Costs L0TriggerCosts(const View& v, const Config& cfg, double K);

// The turn mode's L0 prior (turn_prior; owner's go 2026-11-09): each L0
// action's change of one turn's cost, turn_ops (g(K_after) - g(K_now)) in J
// per turn, lower better, g from L0TriggerCosts on `w`, a View whose counts
// are the forecast window's (the last turn_forecast_turns turns: the
// owner's recent-window rule). Expand goes to K_up, compact to K_down
// (L0RungMoves); hold and defer keep K (0). All 0 when the window has no
// operation or no flush, or a cost is not finite.
Values L0TurnPrior(const View& w, const Config& cfg, int k_now, int k_up,
                   int k_down);

}  // namespace rlc
