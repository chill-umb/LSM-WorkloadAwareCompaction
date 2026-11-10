#include "turn.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iterator>
#include <limits>
#include <sstream>

#include "mlp.h"

namespace rlc {

namespace {

constexpr double kTiny = 1e-9;
constexpr double kForkMin = 0.5;
constexpr double kForkMax = 2.0;
constexpr char kTurnMagic[9] = "RLTW0002";

double Get(const std::vector<double>& v, int i) {
  return i >= 0 && i < static_cast<int>(v.size()) ? v[i] : 0.0;
}

double Clamp(double x, double lo, double hi) {
  return std::min(hi, std::max(lo, x));
}

// The highest RocksDB score among levels other than `level`.
double OtherMaxScore(const TurnTree& t, int level) {
  double best = 0;
  for (int j = 0; j < static_cast<int>(t.score.size()); ++j) {
    if (j != level && std::isfinite(t.score[j])) best = std::max(best, t.score[j]);
  }
  return best;
}

int LiveCap(const TurnTree& t) {
  return t.k_live_cap > 0 ? t.k_live_cap : std::numeric_limits<int>::max();
}

int DeferL0(const TurnTree& t) {
  return t.n0 + static_cast<int>(std::ceil(std::max(0.0, t.f_hat))) + 1;
}

// Compact-now's target at level i >= 1 (the encoding table): due now, first
// in RocksDB's queue where possible, releasing at most R_i this turn.
double CompactTarget(const TurnTree& t, const TurnConfig& cfg, int level,
                     const std::vector<double>& m) {
  const double C = t.C[level];
  const double phi = t.B[level] / C;
  const double sigma = std::min(
      std::max(1 + cfg.margin, OtherMaxScore(t, level) + cfg.margin),
      cfg.sigma_max);
  double v = std::max(kForkMin, phi / sigma);
  if (level >= 2) v = std::max(v, m[level - 1] / t.T);
  v = std::max(v, (t.B[level] - GlideBudget(t, cfg, level)) / C);
  return v;
}

double DeferTarget(const TurnTree& t, const TurnConfig& cfg, int level,
                   double m_now) {
  return std::max(m_now, (1 + cfg.margin) *
                             (t.B[level] + std::max(0.0, Get(t.I_hat, level))) /
                             t.C[level]);
}

void RepairOrder(const TurnTree& t, std::vector<double>* m) {
  for (int i = 1; i + 1 < static_cast<int>(m->size()); ++i) {
    if ((*m)[i + 1] * t.T < (*m)[i]) (*m)[i + 1] = (*m)[i] / t.T;
  }
  for (int i = 1; i < static_cast<int>(m->size()); ++i) {
    (*m)[i] = Clamp((*m)[i], kForkMin, kForkMax);
  }
}

// Little-endian readers and writers for the weights file.
template <typename T>
void Put(std::string* s, T v) {
  char buf[sizeof(T)];
  std::memcpy(buf, &v, sizeof(T));
  s->append(buf, sizeof(T));
}

class Reader {
 public:
  Reader(const std::string& s, size_t pos) : s_(s), pos_(pos) {}
  template <typename T>
  bool Read(T* v) {
    if (pos_ + sizeof(T) > s_.size()) return false;
    std::memcpy(v, s_.data() + pos_, sizeof(T));
    pos_ += sizeof(T);
    return true;
  }
  bool Doubles(size_t n, std::vector<double>* out) {
    if (n > (s_.size() - pos_) / sizeof(double)) return false;
    out->resize(n);
    for (size_t i = 0; i < n; ++i) {
      if (!Read(&(*out)[i]) || !std::isfinite((*out)[i])) return false;
    }
    return true;
  }
  bool done() const { return pos_ == s_.size(); }

 private:
  const std::string& s_;
  size_t pos_;
};

// The guards on a drop (the design's "glide and guards"): the drop of
// level's anchor to `new_a` waits, below L0, while L0 is within
// guard_l0_flushes of its slowdown (L0 needs the slot) or the level's
// previous drop is still gliding (one drop at a time); and while the work
// owed passes guard_owed_bytes. For compact's drop that is the owed work at
// the new anchor. For the decay's it is RocksDB's pending estimate now: the
// glide already paces the new debt, and a level's own debt at the lower rung
// can pass the guard by itself (L3 full at 2x at T = 10 owes ~44 GiB), which
// would hold it above native for good (owner, 2026-11-07).
bool DropBlocked(const TurnTree& t, const TurnConfig& cfg,
                 const Ladder& k_ladder, const Ladder& m_ladder,
                 const std::vector<AnchorState>& an, int level, double new_a,
                 bool decay) {
  if (decay) {
    if (t.pending > cfg.guard_owed_bytes) return true;
  } else {
    std::vector<AnchorState> trial = an;
    trial[level].a = new_a;
    const TurnSettings at = RungSettings(t, k_ladder, m_ladder, trial);
    double owed = 0;
    for (double d : OwedWork(t, at.m, at.K)) owed += d;
    if (owed > cfg.guard_owed_bytes) return true;
  }
  return level >= 1 && (t.n0_all >= t.K_slow - cfg.guard_l0_flushes ||
                        an[level].glide_m > 0);
}

uint64_t TurnFeaturesHash(int acting) {
  std::string joined;
  const std::vector<std::string> names = TurnFeatureNames(acting);
  for (size_t i = 0; i < names.size(); ++i) {
    if (i) joined += ',';
    joined += names[i];
  }
  return Fnv1a64(joined.data(), joined.size());
}

}  // namespace

// --- Rungs and anchors -----------------------------------------------------

bool MakeLadder(const std::vector<double>& rungs, double native_value,
                Ladder* out, std::string* error) {
  Ladder l;
  l.native = -1;
  l.native_value = native_value;
  if (rungs.empty() || !(native_value > 0)) {
    *error = "a ladder needs rungs and a positive native value";
    return false;
  }
  for (size_t i = 0; i < rungs.size(); ++i) {
    if (!(rungs[i] > 0) || !std::isfinite(rungs[i]) ||
        (i > 0 && !(rungs[i] > rungs[i - 1]))) {
      *error = "ladder rungs must be positive and ascending";
      return false;
    }
    if (std::fabs(rungs[i] - native_value) <= kTiny * native_value) {
      l.native = static_cast<int>(i);
    }
    l.rungs.push_back(rungs[i]);
    l.logs.push_back(std::log2(rungs[i] / native_value));
  }
  if (l.native < 0) {
    *error = "the ladder must include the native value";
    return false;
  }
  *out = std::move(l);
  return true;
}

int RungIndex(const Ladder& ladder, double a) {
  int best = ladder.native;
  double best_d = std::fabs(ladder.logs[best] - a);
  for (int i = 0; i < static_cast<int>(ladder.logs.size()); ++i) {
    const double d = std::fabs(ladder.logs[i] - a);
    // Strictly nearer wins; at a tie, the one nearer native.
    if (d < best_d - kTiny ||
        (std::fabs(d - best_d) <= kTiny &&
         std::abs(i - ladder.native) < std::abs(best - ladder.native))) {
      best = i;
      best_d = d;
    }
  }
  return best;
}

double Decay(double a, double kappa) {
  const double v = a * std::exp(-1.0 / kappa);
  return std::fabs(v) < kTiny ? 0.0 : v;
}

std::vector<bool> DecayAnchors(const TurnTree& t, const TurnConfig& cfg,
                               const Ladder& k_ladder, const Ladder& m_ladder,
                               std::vector<AnchorState>* anchors) {
  std::vector<AnchorState>& an = *anchors;
  std::vector<bool> waited(an.size(), false);
  for (size_t i = 0; i < an.size(); ++i) {
    const Ladder& ladder = i == 0 ? k_ladder : m_ladder;
    const double next = Decay(an[i].a, cfg.kappa);
    // Only an anchor above native lowers its target as it decays; one below
    // native raises it, which makes no merge due and never waits.
    if (an[i].a > 0 && static_cast<int>(i) < t.num_levels &&
        RungIndex(ladder, next) < RungIndex(ladder, an[i].a) &&
        DropBlocked(t, cfg, k_ladder, m_ladder, an, static_cast<int>(i), next,
                    /*decay=*/true)) {
      waited[i] = true;
      continue;
    }
    an[i].a = next;
  }
  return waited;
}

std::vector<bool> DecayStep(bool decay, const TurnTree& t,
                            const TurnConfig& cfg, const Ladder& k_ladder,
                            const Ladder& m_ladder,
                            std::vector<AnchorState>* anchors) {
  if (!decay) return std::vector<bool>(anchors->size(), false);
  return DecayAnchors(t, cfg, k_ladder, m_ladder, anchors);
}

double Press(const Ladder& ladder, double a, bool up, double renew_band,
             bool* renew) {
  const int idx = RungIndex(ladder, a);
  const int top = static_cast<int>(ladder.logs.size()) - 1;
  *renew = false;
  if (up) {
    if (idx > ladder.native) {
      const double ar = ladder.logs[idx];
      const double boundary = (ar + ladder.logs[idx - 1]) / 2;
      if (a < ar - renew_band * (ar - boundary) - kTiny) {
        *renew = true;
        return ar;
      }
    }
    if (idx < top) return ladder.logs[idx + 1];
    *renew = true;
    return ladder.logs[top];
  }
  if (idx < ladder.native) {
    const double ar = ladder.logs[idx];
    const double boundary = (ar + ladder.logs[idx + 1]) / 2;
    if (a > ar + renew_band * (boundary - ar) + kTiny) {
      *renew = true;
      return ar;
    }
  }
  if (idx > 0) return ladder.logs[idx - 1];
  *renew = true;
  return ladder.logs[0];
}

// --- The tree ------------------------------------------------------------

bool Acting(const TurnTree& t, int acting, int level) {
  if (level < 0 || level >= acting || level >= t.num_levels) return false;
  if (level == 0) return t.num_levels > 1;
  return level < t.last;
}

std::vector<double> OwedWork(const TurnTree& t, const std::vector<double>& m,
                             int K) {
  const int n = t.num_levels;
  std::vector<double> d(n, 0.0);
  if (n < 2) return d;
  double carry = 0;
  const double b0 = Get(t.B_all, 0);
  if (t.n0_all >= K || b0 >= t.C[1]) {
    d[0] = b0 + Get(t.B_all, 1);
    carry = b0;
  }
  // MaxInputLevel is num_levels - 2 (version_set.cc:3674).
  for (int level = 1; level <= n - 2; ++level) {
    const double size = Get(t.B_all, level) + carry;
    carry = 0;
    const double target = m[level] * t.C[level];
    if (size > target) {
      const double excess = size - target;
      carry = excess;
      const double next = Get(t.B_all, level + 1);
      if (next > 0) d[level] += excess * (next / size + 1);
    }
  }
  return d;
}

double GlideBudget(const TurnTree& t, const TurnConfig& cfg, int level) {
  const double bi = std::max(Get(t.B, level), 1.0);
  const double below = Get(t.B, level + 1);
  const double r = cfg.glide_share * std::max(0.0, t.X_bar) / (1 + below / bi);
  const double one_file = level + 1 < t.num_levels ? Get(t.file_bytes, level + 1)
                                                   : Get(t.file_bytes, level);
  return std::max(r, one_file);
}

// --- Settings -----------------------------------------------------------------

TurnSettings RungSettings(const TurnTree& t, const Ladder& k_ladder,
                          const Ladder& m_ladder,
                          const std::vector<AnchorState>& anchors) {
  TurnSettings s;
  s.m.assign(t.num_levels, 1.0);
  for (int i = 1; i < t.num_levels; ++i) {
    s.m[i] = m_ladder.rungs[RungIndex(m_ladder, anchors[i].a)];
  }
  RepairOrder(t, &s.m);
  s.K = static_cast<int>(k_ladder.rungs[RungIndex(k_ladder, anchors[0].a)]);
  s.K = std::max(1, std::min(s.K, LiveCap(t)));
  return s;
}

L0Rungs L0RungMoves(const TurnTree& t, const Ladder& k_ladder, double anchor,
                    double renew_band) {
  const auto k_at = [&](double a) {
    const int K = static_cast<int>(k_ladder.rungs[RungIndex(k_ladder, a)]);
    return std::max(1, std::min(K, LiveCap(t)));
  };
  bool renew = false;
  L0Rungs r;
  r.now = k_at(anchor);
  r.up = k_at(Press(k_ladder, anchor, true, renew_band, &renew));
  r.down = k_at(Press(k_ladder, anchor, false, renew_band, &renew));
  return r;
}

double GlideStep(const TurnTree& t, const TurnConfig& cfg, int level,
                 double m_rung, AnchorState* anchor) {
  // Before the first turn: the multiplier in effect.
  const double applied = Get(t.m_applied, level);
  const double prev = anchor->last_m > 0 ? anchor->last_m
                      : applied > 0      ? applied
                                         : m_rung;
  // The target steps down by R_i / C_i a turn from the lower of last turn's
  // target and the fill, so each turn the level releases its inflow (as
  // native would) plus at most R_i of backlog, and the glide always ends.
  // (Pacing the whole release at R_i instead stalls whenever the inflow
  // reaches R_i: owner's 2x -> 1x check, 2026-11-07.)
  const double fill = t.B[level] / t.C[level];
  const double step = GlideBudget(t, cfg, level) / t.C[level];
  double g = 0;
  if (m_rung < prev - kTiny) g = std::max(m_rung, std::min(prev, fill) - step);
  if (g <= m_rung + kTiny) g = 0;
  anchor->glide_m = g;
  anchor->last_m = std::max(m_rung, g);
  return anchor->last_m;
}

TurnSettings AnchorSettings(const TurnTree& t, const TurnConfig& cfg,
                            const Ladder& k_ladder, const Ladder& m_ladder,
                            const std::vector<AnchorState>& anchors) {
  TurnSettings s = RungSettings(t, k_ladder, m_ladder, anchors);
  std::vector<AnchorState> copy = anchors;
  for (int i = 1; i < t.num_levels; ++i) {
    s.m[i] = std::max(s.m[i], GlideStep(t, cfg, i, s.m[i], &copy[i]));
  }
  RepairOrder(t, &s.m);
  return s;
}

// --- Masks ----------------------------------------------------------------

std::vector<TurnMask> Masks(const TurnTree& t, const TurnConfig& cfg,
                            const Ladder& k_ladder, const Ladder& m_ladder,
                            const std::vector<AnchorState>& anchors,
                            const TurnSettings& base) {
  const int n = t.num_levels;
  std::vector<TurnMask> masks(n);
  for (int i = 0; i < n; ++i) {
    TurnMask& mask = masks[i];
    mask.fill(false);
    mask[static_cast<int>(Action::kHold)] = true;
    if (!Acting(t, cfg.acting, i)) continue;
    bool renew = false;
    if (i == 0) {
      const bool near_slow = t.n0_all >= t.K_slow - cfg.safety_l0_flushes;
      const int limit = std::min(LiveCap(t), cfg.k_defer_max);
      // The compact floor (turn_compact_floor): at the lowest rung compact
      // would only send K = max(1, n0), below the ladder, so it is blocked.
      const bool at_floor = RungIndex(k_ladder, anchors[0].a) == 0;
      mask[static_cast<int>(Action::kCompact)] =
          t.n0 >= 1 && !(cfg.compact_floor && at_floor);
      mask[static_cast<int>(Action::kDefer)] =
          !near_slow && t.n0 + t.f_hat >= base.K && DeferL0(t) <= limit;
      const double up = Press(k_ladder, anchors[0].a, true, cfg.renew_band, &renew);
      const double k_up = k_ladder.rungs[RungIndex(k_ladder, up)];
      // The safety mask blocks only an expand above native: one that brings
      // a lowered K back toward native postpones no work.
      const bool above_native = k_up > k_ladder.native_value;
      mask[static_cast<int>(Action::kExpand)] =
          !(near_slow && above_native) && t.f_hat > 0 && k_up <= LiveCap(t);
      continue;
    }
    const double C = t.C[i];
    const double B = t.B[i];
    const double phi = B / C;
    const double m_now = base.m[i];
    const double below_T = i + 1 < n ? base.m[i + 1] * t.T
                                     : std::numeric_limits<double>::infinity();
    const bool deep_safety = t.pending > cfg.safety_pending_bytes;
    // compact: the override must make the level due now.
    const double sigma = std::min(
        std::max(1 + cfg.margin, OtherMaxScore(t, i) + cfg.margin),
        cfg.sigma_max);
    mask[static_cast<int>(Action::kCompact)] =
        B > 0 && phi / sigma >= kForkMin &&
        CompactTarget(t, cfg, i, base.m) < phi * (1 - kTiny);
    // defer: only if the level would otherwise compact this turn (due now,
    // or due within the margin once the expected inflow lands; with no
    // inflow it would not), and the override can last the turn.
    const double inflow = std::max(0.0, Get(t.I_hat, i));
    const double defer_m = DeferTarget(t, cfg, i, m_now);
    const bool would_compact =
        B / (m_now * C) >= 1 ||
        (inflow > 0 && (B + inflow) / (m_now * C) >= 1 - cfg.margin);
    mask[static_cast<int>(Action::kDefer)] =
        !deep_safety && would_compact && defer_m <= kForkMax &&
        defer_m <= below_T;
    const double up = Press(m_ladder, anchors[i].a, true, cfg.renew_band, &renew);
    const double m_up = m_ladder.rungs[RungIndex(m_ladder, up)];
    // As at L0: near the pending limit only an expand above native is
    // blocked. Bringing a shrunken level back toward native makes no merge
    // due and lowers RocksDB's pending estimate.
    const bool above_native = m_up > m_ladder.native_value;
    mask[static_cast<int>(Action::kExpand)] =
        !(deep_safety && above_native) && inflow > 0 && m_up <= kForkMax &&
        m_up <= below_T;
  }
  return masks;
}

// --- Encoding -----------------------------------------------------------------

TurnSettings Encode(const TurnTree& t, const TurnConfig& cfg,
                    const Ladder& k_ladder, const Ladder& m_ladder,
                    const std::vector<Action>& actions,
                    std::vector<AnchorState>* anchors,
                    std::vector<TurnNote>* notes) {
  const int n = t.num_levels;
  notes->assign(n, TurnNote());
  std::vector<AnchorState>& an = *anchors;
  // What holding would send: compact-now lowers from here, not from a glide
  // its own anchor drop starts.
  const TurnSettings hold = AnchorSettings(t, cfg, k_ladder, m_ladder, an);

  // 1. Anchor moves and renews.
  for (int i = 0; i < n; ++i) {
    const Action act = i < static_cast<int>(actions.size()) ? actions[i]
                                                            : Action::kHold;
    if (!Acting(t, cfg.acting, i)) continue;
    const Ladder& ladder = i == 0 ? k_ladder : m_ladder;
    const int idx0 = RungIndex(ladder, an[i].a);
    bool renew = false;
    if (act == Action::kExpand) {
      an[i].a = Press(ladder, an[i].a, true, cfg.renew_band, &renew);
    } else if (act == Action::kCompact) {
      const double down = Press(ladder, an[i].a, false, cfg.renew_band, &renew);
      const bool blocked =
          !renew && RungIndex(ladder, down) < idx0 &&
          DropBlocked(t, cfg, k_ladder, m_ladder, an, i, down, /*decay=*/false);
      if (blocked) {
        (*notes)[i].guard_blocked = true;
      } else {
        an[i].a = down;
      }
    }
    (*notes)[i].renewed = renew && act != Action::kHold &&
                          !(*notes)[i].guard_blocked;
    (*notes)[i].moved = RungIndex(ladder, an[i].a) != idx0;
  }

  // 2. Rungs, then the glide on every level.
  TurnSettings s = RungSettings(t, k_ladder, m_ladder, an);
  for (int i = 1; i < n; ++i) {
    s.m[i] = std::max(s.m[i], GlideStep(t, cfg, i, s.m[i], &an[i]));
  }
  RepairOrder(t, &s.m);

  // 3. One-turn overrides, top down so each level sees its upper neighbour's
  // final target.
  for (int i = 0; i < n; ++i) {
    const Action act = i < static_cast<int>(actions.size()) ? actions[i]
                                                            : Action::kHold;
    if (!Acting(t, cfg.acting, i)) continue;
    if (i == 0) {
      if (act == Action::kCompact) {
        s.K = std::max(1, t.n0);
        (*notes)[0].overridden = true;
      } else if (act == Action::kDefer) {
        s.K = std::max(s.K, DeferL0(t));
        (*notes)[0].overridden = true;
      }
      continue;
    }
    if (act == Action::kCompact) {
      s.m[i] = std::min(hold.m[i], CompactTarget(t, cfg, i, s.m));
      (*notes)[i].overridden = true;
    } else if (act == Action::kDefer) {
      s.m[i] = std::max(s.m[i], DeferTarget(t, cfg, i, s.m[i]));
      (*notes)[i].overridden = true;
    }
  }
  RepairOrder(t, &s.m);
  // The glide continues from what was sent: after compact-now it goes on
  // down to the new rung at most R_i a turn; after a defer the postponed
  // work is paid through it (the design's encoding table).
  const TurnSettings rungs = RungSettings(t, k_ladder, m_ladder, an);
  for (int i = 1; i < n; ++i) {
    an[i].last_m = s.m[i];
    if ((*notes)[i].overridden) {
      an[i].glide_m = s.m[i] > rungs.m[i] + kTiny ? s.m[i] : 0;
    }
  }
  return s;
}

bool TopUpL0(int n0, double flushes_left, int cap, int k_defer_max, int* K) {
  if (n0 + 1 < *K) return true;  // not within one file of due
  const int want =
      n0 + static_cast<int>(std::ceil(std::max(0.0, flushes_left))) + 1;
  const int limit = std::min(cap > 0 ? cap : std::numeric_limits<int>::max(),
                             k_defer_max);
  if (want > limit) return false;
  *K = std::max(*K, want);
  return true;
}

bool TopUpLevel(double B, double C, double inflow_left, double margin,
                double m_below_T, double* m) {
  if (B / (*m * C) < 1 / (1 + margin / 2)) return true;
  const double want = (1 + margin) * (B + std::max(0.0, inflow_left)) / C;
  if (want > kForkMax || want > m_below_T) return false;
  *m = std::max(*m, want);
  return true;
}

// --- State ------------------------------------------------------------------

int TurnBlockStart(int level) { return level == 0 ? 6 : 11 + 6 * (level - 1); }
int TurnBlockSize(int level) { return level == 0 ? 5 : 6; }

std::vector<std::string> TurnFeatureNames(int acting) {
  std::vector<std::string> names = {"put_share",    "get_share",
                                    "scan_share",   "pending_soft",
                                    "stall_share",  "busy_share",
                                    "l0_files_k",   "l0_anchor",
                                    "l0_slow",      "l0_flushes_hat",
                                    "l0_probes_get"};
  for (int i = 1; i < acting; ++i) {
    const std::string p = "l" + std::to_string(i) + "_";
    for (const char* f :
         {"score", "anchor", "backlog", "owed", "inflow_hat", "probes_get"}) {
      names.push_back(p + f);
    }
  }
  return names;
}

std::vector<double> TurnInputs(const TurnTree& t, const TurnConfig& cfg,
                               const std::vector<AnchorState>& anchors,
                               const TurnSettings& base,
                               const TurnSettings& rungs,
                               const std::vector<double>& owed) {
  std::vector<double> x(TurnFeatureNames(cfg.acting).size(), 0.0);
  x[0] = t.put_share;
  x[1] = t.get_share;
  x[2] = t.scan_share;
  x[3] = t.soft_limit > 0 ? t.pending / t.soft_limit : 0;
  x[4] = t.stall_share;
  x[5] = t.busy_share;
  x[6] = base.K > 0 ? static_cast<double>(t.n0) / base.K : 0;
  x[7] = anchors.empty() ? 0 : anchors[0].a;
  x[8] = t.K_slow > 0 ? static_cast<double>(t.n0_all) / t.K_slow : 0;
  x[9] = t.f_hat;
  x[10] = Get(t.probes_per_get, 0);
  for (int i = 1; i < cfg.acting; ++i) {
    if (i >= t.num_levels || i > t.last) continue;  // padded with zeros
    const int b = TurnBlockStart(i);
    const double C = t.C[i];
    if (!(C > 0)) continue;
    x[b + 0] = t.B[i] / (base.m[i] * C);
    x[b + 1] = anchors[i].a;
    x[b + 2] = std::max(0.0, base.m[i] - rungs.m[i]);  // glide still to release
    x[b + 3] = Get(owed, i) / C;
    x[b + 4] = Get(t.I_hat, i) / C;
    x[b + 5] = Get(t.probes_per_get, i);
  }
  for (double& v : x) {
    if (!std::isfinite(v)) v = 0;
  }
  return x;
}

// --- Networks ---------------------------------------------------------------

std::string SerializeTurnNet(const TurnNet& net) {
  std::string payload;
  Put<uint32_t>(&payload, static_cast<uint32_t>(net.arch));
  Put<uint32_t>(&payload, static_cast<uint32_t>(net.acting));
  Put<uint32_t>(&payload, net.n_in);
  Put<uint64_t>(&payload, TurnFeaturesHash(net.acting));
  Put<double>(&payload, net.clip);
  Put<double>(&payload, net.spread);
  for (double v : net.mean) Put<double>(&payload, v);
  for (double v : net.scale) Put<double>(&payload, v);
  Put<uint32_t>(&payload, static_cast<uint32_t>(net.nets.size()));
  for (const TurnNet::Net& n : net.nets) {
    Put<uint32_t>(&payload, static_cast<uint32_t>(n.view.size()));
    for (int32_t v : n.view) Put<int32_t>(&payload, v);
    Put<uint32_t>(&payload, static_cast<uint32_t>(n.layers.size()));
    for (const TurnNet::Layer& l : n.layers) {
      Put<uint32_t>(&payload, l.out);
      Put<uint32_t>(&payload, l.in);
      for (double w : l.w) Put<double>(&payload, w);
      for (double b : l.b) Put<double>(&payload, b);
    }
  }
  std::string file(kTurnMagic, 8);
  Put<uint64_t>(&file, net.version);
  Put<uint64_t>(&file, payload.size());
  Put<uint64_t>(&file, Fnv1a64(payload.data(), payload.size()));
  file += payload;
  return file;
}

TurnLoad PeekTurnVersion(const std::string& path, uint64_t* version,
                         std::string* error) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    *error = "cannot open " + path;
    return TurnLoad::kMissing;
  }
  char head[16];
  if (!in.read(head, sizeof(head)) || std::memcmp(head, kTurnMagic, 8) != 0) {
    *error = "turn weights: bad header in " + path;
    return TurnLoad::kInvalid;
  }
  std::memcpy(version, head + 8, sizeof(uint64_t));
  if (*version == 0) {
    *error = "turn weights: version 0 in " + path;
    return TurnLoad::kInvalid;
  }
  return TurnLoad::kOk;
}

TurnLoad LoadTurnNet(const std::string& path, int arch, int acting,
                     TurnNet* out, std::string* error) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    *error = "cannot open " + path;
    return TurnLoad::kMissing;
  }
  const std::string file((std::istreambuf_iterator<char>(in)),
                         std::istreambuf_iterator<char>());
  const auto bad = [&](const std::string& why) {
    *error = "turn weights " + path + ": " + why;
    return TurnLoad::kInvalid;
  };
  if (file.size() < 32 || std::memcmp(file.data(), kTurnMagic, 8) != 0) {
    return bad("bad header");
  }
  TurnNet net;
  uint64_t length = 0, checksum = 0;
  std::memcpy(&net.version, file.data() + 8, 8);
  std::memcpy(&length, file.data() + 16, 8);
  std::memcpy(&checksum, file.data() + 24, 8);
  if (net.version == 0) return bad("version 0");
  if (length != file.size() - 32) return bad("payload length");
  if (Fnv1a64(file.data() + 32, length) != checksum) return bad("checksum");
  const std::string payload = file.substr(32);
  Reader r(payload, 0);
  uint32_t a = 0, act = 0, n_nets = 0;
  uint64_t hash = 0;
  if (!r.Read(&a) || !r.Read(&act) || !r.Read(&net.n_in) || !r.Read(&hash) ||
      !r.Read(&net.clip) || !r.Read(&net.spread)) {
    return bad("truncated");
  }
  net.arch = static_cast<int>(a);
  net.acting = static_cast<int>(act);
  if (net.arch != arch) return bad("arch differs from the config's");
  if (net.acting != acting) return bad("acting levels differ from the config's");
  if (net.n_in != TurnFeatureNames(acting).size()) return bad("input count");
  if (hash != TurnFeaturesHash(acting)) return bad("feature names differ");
  if (!(net.clip > 0) || !std::isfinite(net.clip)) return bad("clip");
  if (!(net.spread > 0) || !std::isfinite(net.spread)) return bad("spread");
  if (!r.Doubles(net.n_in, &net.mean) || !r.Doubles(net.n_in, &net.scale)) {
    return bad("scaling");
  }
  for (double s : net.scale) {
    if (!(s > 0)) return bad("scale must be > 0");
  }
  if (!r.Read(&n_nets)) return bad("truncated");
  const uint32_t want_nets = arch == 0 ? 1u : static_cast<uint32_t>(acting);
  if (n_nets != want_nets) return bad("net count");
  for (uint32_t k = 0; k < n_nets; ++k) {
    TurnNet::Net n;
    uint32_t n_view = 0, n_layers = 0;
    if (!r.Read(&n_view) || n_view == 0 || n_view > 4096) return bad("view");
    n.view.resize(n_view);
    for (uint32_t j = 0; j < n_view; ++j) {
      if (!r.Read(&n.view[j]) || n.view[j] < -1 ||
          n.view[j] >= static_cast<int32_t>(net.n_in)) {
        return bad("view index");
      }
    }
    if (!r.Read(&n_layers) || n_layers == 0 || n_layers > 16) return bad("layers");
    uint32_t prev = n_view;
    for (uint32_t j = 0; j < n_layers; ++j) {
      TurnNet::Layer l;
      if (!r.Read(&l.out) || !r.Read(&l.in) || l.in != prev || l.out == 0 ||
          l.out > 4096) {
        return bad("layer shape");
      }
      if (!r.Doubles(static_cast<size_t>(l.out) * l.in, &l.w) ||
          !r.Doubles(l.out, &l.b)) {
        return bad("layer values");
      }
      prev = l.out;
      n.layers.push_back(std::move(l));
    }
    const uint32_t want_out = arch == 0 ? 1u + 4u * act : 4u;
    if (prev != want_out) return bad("output count");
    net.nets.push_back(std::move(n));
  }
  if (!r.done()) return bad("trailing bytes");
  *out = std::move(net);
  return TurnLoad::kOk;
}

std::vector<std::array<double, kNumActions>> TurnScores(
    const TurnNet* net, const std::vector<double>& inputs, int acting) {
  std::vector<std::array<double, kNumActions>> scores(
      acting, std::array<double, kNumActions>{0, 0, 0, 0});
  if (net == nullptr || net->nets.empty()) return scores;
  std::vector<double> x(net->n_in, 0.0);
  for (uint32_t i = 0; i < net->n_in && i < inputs.size(); ++i) {
    double v = std::isfinite(inputs[i]) ? (inputs[i] - net->mean[i]) / net->scale[i]
                                        : 0.0;
    x[i] = Clamp(v, -net->clip, net->clip);
  }
  const auto forward = [&](const TurnNet::Net& n) {
    std::vector<double> h(n.view.size());
    for (size_t j = 0; j < n.view.size(); ++j) {
      h[j] = n.view[j] < 0 ? 0.0 : x[n.view[j]];
    }
    for (size_t k = 0; k < n.layers.size(); ++k) {
      const TurnNet::Layer& l = n.layers[k];
      std::vector<double> o(l.out);
      for (uint32_t r = 0; r < l.out; ++r) {
        double sum = l.b[r];
        const double* w = &l.w[static_cast<size_t>(r) * l.in];
        for (uint32_t c = 0; c < l.in; ++c) sum += w[c] * h[c];
        o[r] = k + 1 < n.layers.size() ? std::max(0.0, sum) : sum;
      }
      h.swap(o);
    }
    return h;
  };
  if (net->arch == 0) {
    const std::vector<double> o = forward(net->nets[0]);
    for (int i = 0; i < acting; ++i) {
      for (int a = 0; a < kNumActions; ++a) scores[i][a] = o[1 + 4 * i + a];
    }
  } else {
    for (int i = 0; i < acting && i < static_cast<int>(net->nets.size()); ++i) {
      const std::vector<double> o = forward(net->nets[i]);
      for (int a = 0; a < kNumActions; ++a) scores[i][a] = o[a];
    }
  }
  return scores;
}

std::vector<Action> Greedy(
    const std::vector<std::array<double, kNumActions>>& scores,
    const std::vector<TurnMask>& masks) {
  std::vector<Action> out(masks.size(), Action::kHold);
  for (size_t i = 0; i < masks.size(); ++i) {
    if (i >= scores.size()) continue;
    int best = 0;  // hold is always allowed
    for (int a = 1; a < kNumActions; ++a) {
      if (masks[i][a] && scores[i][a] > scores[i][best]) best = a;
    }
    out[i] = static_cast<Action>(best);
  }
  return out;
}

int Explore(double eps, const std::vector<TurnMask>& masks,
            std::vector<Action>* actions, std::mt19937_64* rng) {
  std::uniform_real_distribution<double> unit(0.0, 1.0);
  if (!(unit(*rng) < eps)) return -1;
  std::vector<int> levels;
  for (size_t i = 0; i < masks.size(); ++i) {
    int allowed = 0;
    for (bool b : masks[i]) allowed += b ? 1 : 0;
    if (allowed >= 2) levels.push_back(static_cast<int>(i));
  }
  if (levels.empty()) return -1;
  std::uniform_int_distribution<size_t> pick_level(0, levels.size() - 1);
  const int level = levels[pick_level(*rng)];
  std::vector<int> allowed;
  for (int a = 0; a < kNumActions; ++a) {
    if (masks[level][a]) allowed.push_back(a);
  }
  std::uniform_int_distribution<size_t> pick_action(0, allowed.size() - 1);
  (*actions)[level] = static_cast<Action>(allowed[pick_action(*rng)]);
  return level;
}

double ExploreRate(const TurnConfig& cfg, double turn) {
  if (cfg.explore_turns <= 0 || turn >= cfg.explore_turns) return cfg.explore_end;
  return cfg.explore_start +
         (cfg.explore_end - cfg.explore_start) * turn / cfg.explore_turns;
}

}  // namespace rlc
