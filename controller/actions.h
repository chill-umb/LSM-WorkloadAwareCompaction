// The four actions and what they do to a level's multiplier or to the L0
// trigger (PATHWAYS Pathway A §2). Masks are masks.h's job.
#pragma once

#include "config.h"

namespace rlc {

enum class Action : int { kHold = 0, kCompact = 1, kDefer = 2, kExpand = 3 };
constexpr int kNumActions = 4;

const char* ActionName(Action action);

// Level i >= 1: m_i = anchor * timing. The anchor is the level's capacity;
// the timing factor is short-lived.
struct LevelControl {
  double anchor = 1;  // \bar m_i
  double timing = 1;  // d_i
  double m() const { return anchor * timing; }
};

// The control after `action` at fill phi = B_i / C_i:
//   compact  d <- phi / (anchor (1 + eps)), so s = 1 + eps
//   defer    d <- phi (1 + eps) / anchor,   so s = 1 / (1 + eps)
//   expand   anchor <- min(alpha anchor, m_max) and, so the level stays
//            deferred, d <- max(1, phi (1 + eps) / anchor) (plan §3, review
//            fix 1; PATHWAYS' table still reads d <- 1)
LevelControl Act(const LevelControl& c, Action action, double phi,
                 const Bounds& b);

// Relaxation over `turnovers` (Delta N / N_i, G-i): the timing factor toward
// 1 with time constant kappa_d, the anchor toward 1 with kappa_a (the anchor
// decay that stops expansion ratcheting, ACT-5). A value within epsilon / 10
// of 1 snaps to 1, so a relaxed level stops causing SetOptions calls
// (A-Impl-5).
LevelControl Relax(const LevelControl& c, double turnovers, const Bounds& b);

// L0 (A §2, "L0 analogues"): K_0 = round(anchor + offset), clamped to
// [k0_min, k0_cap]. The anchor decays to the configured trigger, the offset
// to 0.
struct L0Control {
  double anchor = 0;  // \bar K_0
  double offset = 0;
  int K0(const Bounds& b) const;
};

//   compact  K_0 <- max(k0_min, k0), so L0 is due now
//   defer    K_0 <- min(k0_cap, k0 + 1)
//   expand   anchor <- min(anchor + 1, k0_cap), and the offset keeps L0 not
//            due: K_0 >= min(k0_cap, k0 + 1) (the analogue of review fix 1)
// k0 is L0's file count not being compacted, the count its score uses.
L0Control ActL0(const L0Control& c, Action action, int k0, const Bounds& b);
L0Control RelaxL0(const L0Control& c, double turnovers, int k0_configured,
                  const Bounds& b);

}  // namespace rlc
