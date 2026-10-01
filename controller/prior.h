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
// g(K) = beta_W c_w u m_1 C_1 / (K F) + beta_R (K / 2) [q_pt (c_f + eps c_blk)
// + q_sc c_sk], over one L0 turnover. Compact is charged the write change at
// once but credited the read change only while the slot is idle (H §7).
Values L0Prior(const View& v, const L0Control& c, const Config& cfg);

}  // namespace rlc
