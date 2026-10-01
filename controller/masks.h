// Which actions are allowed in a state (Pathway A §2 "allowed when",
// A-Impl-6, A-Impl-7). A masked action is never taken, and is excluded from
// the learning target (Proposition H.2).
#pragma once

#include <array>
#include <vector>

#include "actions.h"

namespace rlc {

using Mask = std::array<bool, kNumActions>;

// The fork's own validity test (ColumnFamilyData::ValidateOptions): m[0] = 1,
// every m[i >= 1] finite and in [m_min, m_max], and targets never shrink going
// down, m[i+1] * T >= m[i].
bool Admissible(const std::vector<double>& m, double T, const Bounds& b);

// Level `level` >= 1 with control c and fill phi; m holds every level's
// multiplier, of which only the neighbours are read. On top of Pathway A §2's
// rules, an action is allowed only if its result keeps m in [m_min, m_max]
// and keeps the targets ordered against the neighbours. The last level
// holds or expands only (H §1).
//   compact  s < 1 and phi >= phi_min
//   defer    s >= 1 - eps and phi (1 + eps) <= m_max
//   expand   anchor < m_max
Mask LevelMask(const std::vector<double>& m, int level, bool last,
               const LevelControl& c, double phi, double T, const Bounds& b);

// L0, with k0 files not being compacted and the trigger K0 in effect:
//   compact  k0_min <= k0 < K0 (makes L0 due now)
//   defer    k0 >= K0 and k0 + 1 <= k0_cap: L0 is due and the new trigger
//            k0 + 1 is a change (at k0 = K0 - 1 it would be a no-op)
//   expand   anchor < k0_cap
Mask L0Mask(const L0Control& c, int k0, int K0, const Bounds& b);

// Relaxation is not an action, and levels relax on their own clocks, so it
// can break the ordering. Raises each m[i+1] below m[i] / T to the smallest
// value that passes the fork's test, top-down, after clamping into the
// bounds. Returns the levels changed.
std::vector<int> Repair(std::vector<double>* m, double T, const Bounds& b);

// Exploration's primitive: uniform over the allowed actions, u in [0, 1).
Action PickAllowed(const Mask& mask, double u);

}  // namespace rlc
