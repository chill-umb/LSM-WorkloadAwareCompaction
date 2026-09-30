# PATHWAYS theory review — 2026-09-29

Scope: every numbered result in `docs/PATHWAYS.md` (revision 2026-09-29, with
the item-3 fixes of this session), every RocksDB claim, every citation, and the
owner's four-point checklist. Nothing here changes PATHWAYS; the fixes proposed
in §6 wait for the owner's decision.

**How it was checked.**

- RocksDB claims were read at the commit the branch records, `25468bbaa`,
  from a scratch clone. The working tree was not used, because it lags that
  commit.
- RusKey claims were checked against the arXiv PDF (2308.07013). Fig. 9 was
  checked by rendering its page.
- Numbers were checked against `docs/PREREGISTRATION.md`, the 2026-09-23 audit
  and the history. Numbers that exist nowhere in the repo were traced to their
  scratch scripts.
- Every proof was re-derived by hand, and every table was recomputed.

## 1. Verdict in one paragraph

**No fabricated facts, and no proof is wrong in its conclusion.** Every RocksDB
behaviour, RusKey statement and quoted number that could be checked matches its
source. Eleven results have small gaps: a missing assumption, a word that is
too strong, or a notation clash (§4). The serious findings are about the
design, measured against your checklist, not the maths:

1. The **"expand" action does not always defer.** It can leave a level due, and
   it cancels a deferral already in force.
2. In **read priority**, the only lever that can change reads during a run is
   the **L0 trigger**. Interior agents cannot deliver a dynamic read gain, and
   the reward even discourages the one read-helping move they have.
3. **Propagation is expected to do nothing at T = 10.** Only L2 would pass
   admission there, and a pool of one level has nothing to share.
4. The **"global state" is thin.** The neighbour charge covers only the two
   adjacent levels, and only writes and bursts.
5. **Space priority is empty unless space is priced in money.** The document
   also allows a currency in which the space price is zero.

## 2. Terms used below

- **Score**: RocksDB's "how overdue is this level" number. A level is compacted
  only when its score is at least 1.
- **Multiplier $m_i$**: the new knob. It divides the score, so $m_i > 1$ lets a
  level grow past its normal size before it is compacted.
- **Turnover**: the time it takes new data to fill one level's worth of space.
  Deep levels have long turnovers, so they produce few learning samples.
- **Pool / propagation**: several levels train one shared model, after
  converting their data into level-free units.
- **Counterfactual (neighbour) charge**: "how much worse off is my neighbour
  because I acted instead of doing nothing". It is added to each level's cost.
- **$\beta^\star$**: how many times more a unit of the prioritised cost is
  worth than its price.

## 3. Your checklist

### Item 1 — actuation through the score (trigger, defer, defer and expand)

| Check | Verdict | Evidence |
| --- | --- | --- |
| Option spec: `vector<double>`, one per level, entry 0 = 1.0, set through `SetOptions`, rejected with dynamic sizing or a non-leveled style, score $=$ bytes\_not\_compacting $/(\texttt{MaxBytesForLevel}(i)\,m_i)$ | **Matches your spec word for word** (A §1) | — |
| Native score it modifies | **Correct.** Level $i \ge 1$: compensated bytes not being compacted over `MaxBytesForLevel(i)`. L0: $\max(k_0/K_0,\ \text{L0 bytes}/\texttt{max\_bytes\_for\_level\_base})$ (A3′) | `db/version_set.cc` `ComputeCompactionScore` @25468bbaa |
| L0 trigger direction | **Correct and consistent with your statement.** Lower $K_0$ gives more writes and fewer probes and seeks (Prop. D.11) | — |
| Does a change take effect at once? | **Yes, verified.** `SetOptions` appends a new Version, which recomputes the scores, and installs a SuperVersion. It waits its turn behind flush and compaction installs. A-Impl-4's "verify" is answered | `db/db_impl/db_impl.cc` `DBImpl::SetOptions` |
| Cost of `SetOptions` | **Verified: every call writes a full OPTIONS file** (outside the DB mutex). A-Impl-5's "(to verify)" is answered, and ACT-2 must include this I/O | `WriteOptionsFile` |
| Side effects of $K_0$ | Verified. $K_0$ also sets the L0 part of the pending-bytes estimate and the compaction speed-up threshold $\min(2K_0,\ K_0 + (K_{\text{slow}}-K_0)/4)$. The latter has no effect here, because there is one compaction slot either way. RocksDB assumes $K_0 \le K_{\text{slow}}$ but only enforces it at open (`SetOptions` calls `ValidateOptions`, not `SanitizeOptions`; there is a FIXME saying so), so the cap $K_{\text{cap}} \le K_{\text{slow}}-1$ (A-Impl-6) is **required, and present** | `db/column_family.cc` `GetL0FileCountForCompactionSpeedup` |
| **"Expand (defer and increase capacity)"** | **Gap.** It sets $\bar m_i \leftarrow \min(\alpha\bar m_i, m_{\max})$ and $d_i \leftarrow 1$. The score is then $\varphi_i/(\alpha\bar m_i)$, which is still $\ge 1$ whenever $\varphi_i \ge \alpha\bar m_i$, so the level stays due. Resetting $d_i \leftarrow 1$ also cancels a deferral already in force | A §2 table |
| "Compact" size | **Clarify.** $d_i$ is set so the score is exactly $1+\epsilon$ at the current fill. RocksDB then runs about one job (one 512 KiB file plus its overlap) and the score falls below 1. "Compact" therefore means "trigger one compaction", not "empty the level". The prior $b$ must price it that way | A §2, Thm A.1 |
| Bounds against masks | **Gap.** "Compact" needs $\varphi_i \ge m_{\min}(1+\epsilon)$, or the clamp to $m_{\min}$ cancels it. So $\varphi_{\min} \ge m_{\min}(1+\epsilon)$ must hold. "Defer" is impossible once $\varphi_i(1+\epsilon) > m_{\max}$ (about 1.9 at $m_{\max}=2$). Neither condition is in the "allowed when" column | A §2, A-Impl-7 |
| L0 actions | "Compact" makes L0 due only if $k_0 \ge 2$. "Defer" cannot defer once $k_0 \ge K_{\text{cap}}$. The table has no allowed-when column for L0 | A §2 |
| Compaction style | The fork also has `kCompactionStyleRL` (4), which the score code treats as leveled. State whether Programme 1 runs style 0 and whether style 4 accepts multipliers | A §1 |

**Verdict: good mechanism, with two action-definition gaps (expand, bounds)
that would make actions silently not do what they are named for.**

### Item 2 — one RL controller per level, with propagation inspired by RusKey but not copying it

| Check | Verdict |
| --- | --- |
| One agent per level | **Yes.** L0 agent, one agent per interior level, and a last-level agent. Each level decides from its own $Q_j$ and its own state (G-iii) |
| RusKey facts (G §1) | **All verified against the paper**, see §5 |
| Differs from RusKey | **Yes, in substance.** A value estimate propagates, not a copied $K$. It runs concurrently rather than learn-then-transfer. Deep levels keep learning. Time is measured in turnovers, not seconds. The reward is a counterfactual cost, not a latency mix. Shared ideas (one agent per level, one-step actions) are credited |
| Theory (G.1–G.5) | **Sound as conditional statements** (§4) |
| **Does it help where it is needed?** | **Expected not to at T = 10.** By G §4's own expected failures: L1 fails, the last level is excluded, and the level above the last fails whenever the last level is far below target. At T = 10 on `Assoc` that leaves **L2 alone**, and a pool of one level propagates nothing. L3, the data-starved level ($\tau \approx 230$ s), keeps its own model. The document states "the T=10 pool may be L2 alone" but never says what that implies. At T = 2 a pool of L2–L6 is plausible |
| What pooled agents can deliver | **Write and space timing only.** G §5 says their read effect is static, and the neighbour charge does not credit read shifts |
| "Each level has its own controller" | In decisions, yes. In weights, pooled levels share $f_\theta$ plus a small per-level correction $\delta_j$. That is the design; confirm it is what you want |

**Verdict: the propagation law is new and correctly argued, but on the planned
T = 10 cell it is predicted to be inactive.** Decide whether that is acceptable
(propagation becomes a T = 2-style result). The alternative is to let
non-members use $f_\theta$ as a starting point with a larger $\delta_j$, which
rule G-v currently forbids.

### Item 3 — neighbour and global context; proactive decisions

| Check | Verdict |
| --- | --- |
| Neighbour state | **Present:** $\varphi_{j\pm1}$, $m_{j\pm1}$, and the incoming burst $b_j$. The L0 agent sees $\varphi_1$ |
| Global state | **Thin.** It has $\beta$, the price ratios, L0's $k_0/K_0$ and the slot's busy share. There is no depth, no distance to the last level, no pending-compaction total and no global cost rate. The pooled model needs a level-free state (condition c1), so a raw whole-tree vector would break pooling. Level-free summaries would not (for example pending-compaction bytes over their mean, or global write rate over its mean) |
| "Don't help myself and hurt the tree" | **The mechanism is right**: a counterfactual (difference-reward) charge, H §3. Prop. H.1(b) is correct |
| Coverage of that charge | **Partial.** (a) Only levels $i\pm1$; a burst cascading to $i+2$ and beyond is uncharged. (b) Read shifts are neither charged nor credited, as H §3 says explicitly. A level that expands pays its own extra block reads and gets no credit for the probes it saves deeper down. **In read priority this pushes interior agents away from the one read-helping move they have.** (c) The "echo" double count is acknowledged and deferred to §0.6 item 11 |
| Proactive | **Yes.** Compact below threshold, defer above it, value look-ahead of $n_H$ turnovers, and a prior over one turnover |

**Verdict: neighbour-aware, but not whole-tree-aware; and the read side of
"don't hurt the tree" is not modelled.**

### Item 4 — the parameterised cost and its three modes

| Check | Verdict |
| --- | --- |
| Read mode = maximise read gain while minimising write and space loss | **Yes, exactly**, by Proposition D.2's gain–loss identity: $J_\beta(\pi_0) - J_\beta(\pi) = \beta^\star\,(\text{prioritised decrease}) - \sum(\text{other increases})$. The proof is correct |
| Write mode and space mode | **Yes.** Only $\beta$ changes: $(\beta^\star,1,1)$ and $(1,1,\beta^\star)$. Gate N5 runs all three |
| "Minimising loss" | It is a **priced trade**, not a limit: the other costs may rise when the prioritised saving is worth $\beta^\star$ times more. For "reads first, then keep the others as low as possible", use $\beta^\star > \bar\beta$ (Prop. D.4, which is correct) |
| **Space mode feasibility** | **Gap.** D §1 allows "device-seconds if space is not priced". In that currency $c_s = 0$, and space priority multiplies zero. Space mode requires the money currency with $c_s > 0$; say so in OBJ-2 |
| Per-level space signal | Shadowed garbage only, which undercounts (acknowledged, OBJ-3). `Assoc` holds 3–8% garbage, so space mode needs the high-garbage workloads of Pathway B |
| Read-mode room | Only the L0 trigger acts dynamically (D §5, G §5); interior profiles and depth are static. **This is the main risk to the headline mode** |
| Regret in C.5 | **Gap.** Regret is a ratio $J(\pi)/J(\theta^\star)-1$. The denominator includes a cost no policy can change: holding the live bytes, which D.15 shows is the same under every policy, times $\beta^\star$ in space mode. That shrinks every regret on high-space workloads and can change which workload is worst. Use $J$ minus the policy-independent live-byte term, or a difference scaled by a fixed constant |

**Verdict: the cost function does what you asked in all three modes; space
mode needs $c_s > 0$ stated, and read mode leans on the L0 agent alone.**

## 4. Every numbered result

"Correct" means the conclusion follows from the stated assumptions and the
algebra or table was re-derived.

| Result | Verdict | Note |
| --- | --- | --- |
| Lemma D.1 | Correct | Additivity |
| Prop D.2 | Correct | — |
| Prop D.3 | Correct; wording | "In metric units, $-dP/dX = \text{price}_X/(\beta^\star\text{price}_P)$" leaves out the volume factors (user bytes for $W$, Gets for $R$). Read "price" as price × volume |
| Prop D.4 | Correct | $\bar\beta = M/\Delta$ checked |
| Prop D.5, Remark D.6 | Correct | Standard [Das & Dennis; Miettinen] |
| Lemma D.7 | Correct | $X = S(\rho+o)$. "Trivial move is whole-job" verified (`BackgroundCompaction` → `PerformTrivialMove`) |
| Lemma D.8 | Correct | Under uniform keys |
| Cor D.9 | Correct | The product telescopes to $B_L/(K_0F)$. Needs $L \ge 2$ (at $L=1$ the two definitions of $f_0$ collide) |
| Lemma D.10 | Correct | Seek semantics verified: `BlockBasedTableIterator::SeekImpl` ticks only on a keyed seek; `LevelIterator::SeekToFirst` ticks once per level. Key and op type come from one draw (`rand_v`), verified |
| Prop D.11 | Correct; hidden assumption | The proof writes $a_0o_0 = m_1C_1/(K_0F)$, i.e. takes $a_0 = 1$ (no memtable drops). In general the write term and $K_0^{\star2}$ scale by $a_0$ |
| Cor D.12 | Correct; tie caveat | Needs the phases' *sets* of best triggers to be disjoint. With a tie, one fixed trigger can be best in both phases |
| Prop D.13 | Correct | (i) by AM–GM. (iv): derivative $f(1-\ln f)$ checked; $3.59(\ln 3.59-1)=0.998$ |
| Lemma D.14, D.15, Prop D.16 | Correct | — |
| Lemma A.5 | Correct; slightly strong | The picker walks eligible levels by score and takes the **first one it can form a job from**, skipping busy files and possibly falling back to intra-L0 (`LevelCompactionBuilder::SetupInitialFiles`). That is not always the top-scoring level |
| Thm A.1 | Correct ("about") | Containment is a valid necessary condition |
| Thm A.2 (i)–(iii) | Correct | Lagrange step checked. All four table rows recomputed: 0.9984, 0.9931, 0.9519, 0.7807 |
| Lemma A.6, Cor A.7 | Correct; selective evidence | A.7 quotes reads falling 6.3% (T=2) and 3.4% (T=10) at $s=1.5$, but omits T=6, where reads **rose** 3.1% (audit §3 table) |
| Prop G.1, G.2, G.3 | Correct | G.2's units and $\lambda_i = \bar\lambda_1\pi_i$ checked |
| Prop G.4 | Correct as a conditional | Simulation-lemma bound is conservative but valid. The Wasserstein form re-derived. The Lipschitz constant is written $L$, which clashes with $L$ = last level |
| Lemma G.5 | Correct for regression | It holds for ridge regression on fixed targets. $\delta_j$ is fitted to bootstrapped TD targets (targets built from the model's own estimates), so it is an analogy there |
| Admission test | Correct | The combined test (every statistic must pass) has size at most 5%. KS figure 0.433 reproduced (`ks_boot_sim.out`) |
| Prop H.1 | Correct | — |
| Prop H.2 | Correct; wording | The strictness condition should read "under $\tilde Q$, a forbidden action's value strictly exceeds every allowed action's at $s'$" |
| Prop H.3 | **Needs an assumption** | Regret is strictly positive only if the optimal constant action is unique, with a strict gap. Otherwise exploring an equally good action costs nothing. "Cost on every interval where its action differs" should go through the performance-difference lemma (which sums the per-step advantage of the optimal policy), since an off-policy action also changes later states |
| Prop H.4 | Correct; wording | "does not" should be "need not" |
| Prop B.1′, B.1″ | Correct | The saving $\sum_j D_j(1+\sum_{i>j}w_i)$ and the native-relative bound are both re-derived |
| Conj B.3′, Cor B.4 | Correct as labelled | — |
| Prop C.1, Cor C.2, C.3 | Correct | — |
| Prop C.4 | Correct without ties | With ties, *some* minimiser is a vertex; $\theta^\star_\beta$ need not be |
| Def C.5 | Design gap | See item 4 (ratio regret) |
| Prop F.1, F.2 | Correct | — |

## 5. Fabrication check

| Claim | Source checked | Verdict |
| --- | --- | --- |
| RusKey: independent DDPG per level; actions $K-1, K, K+1$; reward $\alpha t_i + (1-\alpha)t'$ | paper §5.1.2–5.1.4 | ✓ |
| RusKey Case 1 / Case 2 / Lemma 5.1 / example (9, 7, 3, 1) | §5.2 | ✓ |
| RusKey Case 2 bullet says *lower* bits-per-key at shallower levels, contradicting Monkey's $f_i = T^{i-1}f_1$ | §5.2 bullet vs Lemma 5.1 proof | ✓ the inconsistency is real |
| Fig. 9 shows K = 10, 8, 3, 1 while the text says "lazier" with depth | rendered p. 19 | ✓ |
| "Training every level separately failed from Level 3" | §7: "fails to achieve the optimum policy starting from Level 3 due to insufficient samples" | ✓ |
| "…because FLSM merges a whole level at a time" | §5.2 says only "exponentially less frequent" | ✗ **the document's own explanation, presented as RusKey's** |
| One flush slot, one compaction slot at `bg2`; db_bench defaults −1 | `GetBGJobLimits`, `db_bench_tool.cc`, `config.sh` | ✓ |
| `is_trivial_move()` set only by the universal picker; trivial branch leaves `total_input_bytes` empty | `compaction_picker_universal.cc:797`, `db_impl_compaction_flush.cc` | ✓ |
| `max_bytes_for_level_multiplier_additional` is `vector<int>`, ignored under dynamic sizing; dynamic default true since 8.4 | `advanced_options.h`, `HISTORY.md` | ✓ |
| 935 / 1,079 / 927 releases; 40% early compaction (D-7) | history table | ✓ |
| +0.68 GB, −0.07 GB, 38–42 s, 27–39%, E-5 6–17× then 0–1.4%, 34–74%, depth equal in all 18 arms | audit | ✓ |
| Drift +2.9 to +4.1% at T=2 | PREREGISTRATION D-12 | ✓ |
| $S_{\text{flow}} = 1.386$, 0.006% over 108 runs, 0.6–0.8% space spread, runtime 4.2% / 2.6% | PREREGISTRATION D-3, D-5 | ✓ |
| η at L1 0.84 / 0.86 / 0.93 | PREREGISTRATION D-4 | ✓ |
| 58 / 35 / 35% constant-survival estimate | audit | ✓ |
| 42–71% trivially moved on 30 uniform runs; 26–70% overstatement | `deprecated/pre-gate2-2026-09-20/results/prior_shadow/…` via scratch `r3/moved_share.py` | ✓, but **the script lives only in /tmp** |
| 26–32% whole-run bound; 44% of Gets on unwritten keys; KS 0.43 | scratch reviewer scripts | ✓ reproduced, but **only in /tmp** |
| Reference list: authors, venues, volumes | — | ✓, except: [O'Neil et al.] is never cited; [How to Grow] lacks authors (D. Mo, S. Luo, S. Idreos; PACMMOD 3(3), Art. 173, 2025) |

## 6. Proposed fixes (not applied; owner's call)

Ranked by effect on the programme.

1. **Expand must defer.** Set $d_i \leftarrow \max\{1,\ \varphi_i(1+\epsilon)/\bar m_i^{\text{new}}\}$ after the anchor update. Mask expand when $\varphi_i(1+\epsilon) > m_{\max}$. Add the bound conditions ($\varphi_{\min} \ge m_{\min}(1+\epsilon)$; defer only while $\varphi_i(1+\epsilon) \le m_{\max}$) to the allowed-when column. Add an L0 allowed-when column.
2. **Space mode:** state in D §1 and OBJ-2 that space priority requires $c_s > 0$ (money currency). With $c_s = 0$, balanced mode silently drops space.
3. **Read-mode risk:** record in D §5, and in the Gate N5 claim, that read priority's dynamic room is the L0 agent's alone. Consider whether a read-shift credit belongs in the neighbour charge, or accept the bias and say so.
4. **Propagation at T=10:** either state that G is expected inert at T=10, or relax G-v so that non-members start from $f_\theta$.
5. **Global state:** decide which level-free global summaries to add, if any.
6. **Regret:** define C.5 on $J_\beta$ minus the policy-independent live-byte term.
7. **Proof hygiene:**
   - H.3: add the uniqueness-and-gap assumption.
   - D.11: carry $a_0$.
   - D.12: disjoint best sets.
   - C.4 and H.2: tie and strictness wording.
   - H.4: "need not".
   - A.5: "first pickable level by score".
   - G.4: rename the Lipschitz constant.
   - G.5: note that it is an analogy under TD.
   - A.7: add the T=6 row.
   - G §1: attribute the "whole level" explanation to this document, not RusKey.
8. **Record-keeping:**
   - mark A-Impl-4 and A-Impl-5 as verified, with ACT-2 counting the OPTIONS-file write;
   - move the scratch scripts (`r3/moved_share.py`, `ks_boot_sim.py`, the 26–32% and 44% computations) into the repo, or cite them from PREREGISTRATION before `/tmp` is cleared;
   - drop [O'Neil] and add authors to [How to Grow].
9. **Tests vs CLAUDE.md:** A-Impl-1, ACT-1 and ARCH-2 require unit tests; CLAUDE.md forbids adding any. Either restate them as node-run checks or amend the rule.

## 7. Owner decisions, 2026-09-29, and what was applied

- **Checklist 1, OPTIONS file.** The OPTIONS-file write must not enter the
  metric. Verified that it doesn't: $W$ adds up only SST bytes (flush
  `table_file_creation` sizes plus compaction `total_output_size`, per
  `04_generate_graphs.py`). $H$ counts only live SST files, and $J_\beta$
  contains no time. Applied to PATHWAYS:
  - the D §1 $W$ row states the exclusion;
  - A-Impl-4 and A-Impl-5 are rewritten as verified facts, with "no call on
    hold, one call per round";
  - ACT-2 is marked as an overhead diagnostic, not part of $J_\beta$.

  This supersedes §3 item 1's "ACT-2 must include this I/O": it measures the
  I/O's time cost but prices nothing.
- **Checklist 3, global state.** Adopted inputs 1, 2 and 4 of the global-state
  proposal:
  - **queue position:** the number of due levels ahead of a compaction at $j$,
    and where the running job's level lies relative to $j$;
  - **backlog:** the pending-compaction estimate over held bytes $H$, and L0's
    distance to the slowdown trigger. It is measured against $H$ because the
    pipeline's soft limit is 64 GiB, against about 3 GiB held;
  - **fills two levels down and the last level's free room.**

  Applied to G §3, G.4's list of dynamic inputs, H §2 (L0 and last-level
  agents) and Gate N0 item 3. Inputs 3 (read exposure) and 5–7 are not adopted.
- **Checklist 4, catch 1 (space price).** Applied:
  - D §1: money only, with $c_s > 0$ required; the device-seconds option is
    removed; results are also reported at $c_s/2$ and $2c_s$.
  - OBJ-2 is updated to match, and §0.6 item 1 now includes the instance and
    storage prices.
- **Checklist 4, catch 2.** Applied three fixes:
  - **A1, the hit-read bucket.** The block read of the table where a Get finds
    its key is charged to no level (D §4). Updated to match: D.1's scope, D.16
    and its proof, OBJ-1, OBJ-4, G §2's $e^b_i$, H §3 and G §5.
  - **B1, the slot-blocking charge.** While L0 is due but waiting for the one
    compaction slot, the share $(k_0-K_0)^+/k_0$ of L0's read cost goes to the
    level whose job holds the slot (D §4). Also added to: D.16, G.2 (term
    $\tilde y_i$), the prior (H §7), D §5 and G §5.
  - **B2, L0 compacts early while the slot is idle.** Added as Pathway A §4
    lever (e), with interior slot-yielding as (f). The L0 state gains
    whether the slot is idle and the active memtable's fill (H §2). Both rules
    are added to Gate N3, and Gate N0 item 3 gets the instruments.
- **Stall rule.** Every claim also needs paired stall seconds and throughput no
  worse than the comparator's beyond margins fixed in advance (Global
  acceptance; D §1; OBJ-6; §0.6 item 12). Stalls stay unpriced.
- **Checklist 2, propagation at T=10.** Option 1 is adopted: claim propagation
  only in (workload, $T$) cells whose pool holds at least two admitted levels.
  That means T=2 at the current size, and T=6 if the admission test admits two
  levels; never T=10 at about 3 GiB. Applied to:
  - G §4 (scope decision);
  - Global acceptance claim 4;
  - Gate N1 (where no pool exists, run length is set by L2).

  Pooling a level that failed admission is rejected. The escalation path is
  about 17 GiB at T=10, gated on one static run showing that L3 passes
  admission.
- **Implementation plan.** Written as `docs/IMPLEMENTATION_PLAN_PROGRAMME1.md`,
  on four decisions:
  - the controller is a separately loaded `.so` behind a host interface;
  - the old socket, picker and coordinator stack is deleted, the guard is
    parked for Programme 2, and Gate N6's socket comparison uses the plugin's
    remote-inference mode;
  - no test suite: ACT-1 and ARCH-2 become node-run checks and log audits;
  - level targets never shrink going down the tree (A-Impl-7).

  PATHWAYS updated to match:
  - A-Impl-1: the multiplier is folded into `MaxBytesForLevel` through the
    fork's `capacity_scales_`;
  - A-Impl-3: now automatic;
  - A-Impl-7: the target-ordering rule;
  - ACT-1, ARCH-2, Gate N0 item 2 and Gate N6.
- **Tests.** The owner reversed the 2026-09-13 "no tests" rule: "I want
  everything to be okay and passing all tests before I commit to a long multi
  hour node time". Applied:
  - CLAUDE.md "No tests" becomes "Tests": three tiers (local Python `unittest`
    plus `-fsyntax-only`; node Debug-tree gtest for the fork and plugin; node
    preflight), and a preflight marker bound to the hashes that every long-run
    driver requires;
  - the plan's §6 lists every suite and case;
  - the plan's §7 attaches tests to every step;
  - PATHWAYS: ACT-1 and ARCH-2 are tests again, and Gate N0 gains item 9.
