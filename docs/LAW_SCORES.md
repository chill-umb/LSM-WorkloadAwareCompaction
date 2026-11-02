# Cases 1-2 propagation law: scores

Updated 2026-10-29 03:38 UTC. Kept current by the operator: every time a score changes, this file changes with it.

- **Single-change runs:** 675 runs, one K change from a settled K = 4 tree. Law round 18 (`v2/score_k_v2r18.py`), scored
  in settled windows. Pass = at least 90% in every workload. Count: **9 of 13**.
- **Multi-change runs:** 135 runs, 1,430 changes, 7-16 per run. Law r7 (`clean/r7`, parts d, p, c, o, w, s, r, j, u, e, g; =
  r5 + the write rate at L0 merge start, the calm before-stretch, the brake forecast on the new K only, an L0 merge's
  dropped copies, the workload's write rate since its last step, the L0 merge already running at the change, and each
  L0 merge's L1→L2 burst in calm trees, busy #5/#5b against the run's own last stretch, with no old-K stand-in, and the
  deepest level's own intake from its merges, timed on whole pours). At
  each K change
  the law predicts every level from the run's own last stretch; the prediction is checked against the run's own next
  stretch (no twin run). Tolerance: the noise band from the original repeats. Pass = at least 90% hits in every
  workload. Count: **5 of the 10 scored**.

| # | What it checks | Single-change runs (round 18) | Multi-change runs (r7) |
|---|---|---|---|
| 1 | K above the cap acts as the cap | FAIL (W4) | not scored |
| 2 | Mean L0 file count | PASS | **PASS** (91-100%; W4 93, W5 91) |
| 3 | Files per L0→L1 merge | PASS | FAIL: W4 82, W5 86 |
| 4 | L0→L1 bytes written per op | PASS | **PASS** (90-93%; W2 221/245 is half a hit above the bar) |
| 5 | L1→L2 overlap | FAIL | FAIL in 4 (W1 79, W3 38, W4 63, W5 77; W2 91 passes) |
| 5b | L1→L2 bytes written per op | PASS (W1 accepted as a limitation) | FAIL in 4 (W1 84, W3 71, W4 69, W5 83; **W2 91 passes**) |
| 6 | L2 and deeper | FAIL | FAIL in all 5 (L2 misses everywhere; deepest level W1 89 (235/264, bar 237.6), W2 78, W3 82, W4 43, W5 80) |
| 7 | Writes slowed or not | PASS | **PASS** (94-100%) |
| 8a | When the first L0 merge after the change starts | PASS | **PASS** (92-100%; W5 92) |
| 8b | Files in that first merge | PASS | **PASS** (93-100%) |
| 9 | L0 settles within one cycle | PASS | not scored |
| 10 | Flows out of L1 and deeper | FAIL (W4) | FAIL (L1 and L2 miss everywhere) |
| 11 | Probes per Get | PASS (W1/W2 accepted as a limitation) | not scored |

## Why the multi-change runs fail

- **#3:** W4 (82; K raised 73, K lowered 90) and W5 (86): not yet re-diagnosed under the run's-own scoring. Earlier
  signs: the timing of the 64 GiB brake, and trees neither L0-bound nor brake-bound, which the transition model does
  not cover.
- **#5:** The overlap shifts during the transition after a change. Even with measured sizes plugged in, the formula
  tops out at W3 59, W4 63 and W5 81. W1 is the hot key range; W2 is one hit short (188 of 209).
- **#5b:** calm trees now count each L0 merge's L1→L2 burst (part u), which passes W2. W3/W4 follow L1's size and #5
  (below). W1 (84) is the hot key range. W5 (282/344, bar 309.6): W5 writes in three phases of 13.33M ops (90%, 50%,
  10% writes), and the multi-change schedule puts a change 0.5M ops before and 0.5M after each phase boundary. With no
  phase boundary near the change, #5b hits 218/236 (92%); with the boundary in the last stretch, 40/54 (part r now
  measures the new rate there); with the boundary in the next stretch, 24/54: the write rate changes after the change,
  and nothing in the run's past shows it. Five late W5 calm stretches also miss because the L0 schedule's flush line is
  fitted over the last 5M ops, which spans the boundary.
- **#5 (2026-10-28 diagnosis, `clean/diag5/`):** RocksDB's L1→L2 merge takes the L1 file with the least L2 data per
  byte (kMinOverlappingRatio, `version_set.cc` SortFileByOverlappingRatio). Replaying every L1→L2 merge from the
  MANIFEST (611,188 merges, 0 mismatches), #5 = c × G: G = L2/L1 bytes at the picks, c = the min-pick discount (0.43-0.48
  in W2-W5, W1 0.29-0.36). With the measured G and c held fixed, #5 reaches only W3 58, W4 68, W5 88: c also moves
  with the change (K raised −4%, lowered +5%, medians). The busy misses are mostly G: after a change the law's L1 and
  L2 move too little or the wrong way (the inflow forecast; see #6). W1's c moves ±12-16% (hot key range).
- **#5 busy level paths (2026-10-28, `clean/diag5/` path5*, paths5*):** replaying every flush and merge, a level below
  its new size gets no merges of its own (0-6% of its inflow in W3), but one speed per level from the last stretch
  fails. The path has phases set by RocksDB's code: after a K raise L0 is not merged until its first merge (#8a), and
  the worker drains the over-full levels, mostly L2 and deeper (62-82% of its reads); after a K lower, L0 takes most of
  the worker and L2 is starved (L0's score is files / K, a level's is bytes / target; dynamic level sizes are off). A
  forecast built from the law's own pause and drain is right at the pause's end (L1, L2 within 0-2%, medians) but
  drifts in the rhythm after it. #5 is a change in plain mean L2 / mean L1 (with the measured means the base form
  reaches W3 142, W4 168, W5 212 busy hits); with the path's means it reaches 131, 132, 205 (busy; part e: 102, 169,
  201). Not adopted: it trades W4 for W3.
- **#5's pick discount c over two levels (2026-10-28, `clean/diag5/c5two.py`, `c5two_show.py`, diagnostic only):**
  under each picked L1 file, L1 holds 1.75-1.96x its mean density and L2 only 0.81-0.86x its mean (busy W3-W5,
  medians): L2→L3 pushes take L2's densest ranges (2.1x its mean in W4/W5) and leave thin L2 behind, and the next L1
  picks land there. L1 file edges add almost nothing (whole / prorated L2 bytes 1.00-1.07), because RocksDB cuts an
  output file at a grandparent file boundary once it holds 50-90% of the target size (`compaction_outputs.cc`
  340-363). A key-cell model fed only the measured flows (no keys; L1 and L2 cells, L3 uniform; RocksDB's pick rule)
  reproduces L2's thinness (0.81-0.86x), slightly under-states L1's (1.64-1.79x), and with the measured G and trivial
  share lifts busy #5 from W3 191, W4 223, W5 266 (c held) to W3 237-248, W4 234-238, W5 270-274 (2048-8192 cells;
  measured c: 264, 267, 278). L1 cells alone (the one-level cursor) give only 205-209, 221-225, 273-274. The model's own
  trivial share makes it worse (W3 211, W4 214), and starting it from the real tree at each change is no better than
  running it from the run's start. Still a simulation, not a formula, and it needs the law's G to be right first.
- **#5's G as a score ratio (2026-10-28, `clean/diag5/g5score.py`, `g5form.py`, `g5law.py`, `g5fill.py`, `g5cal.py`,
  `g5ceil.py`; `paths5.py` PATHS_RHY=water; diagnostic only):** an L1→L2 merge starts only when L1's score is the
  highest, so at every pick G = T s2/s1. Measured in busy W3-W5: an L1 run ends with L2 taking the worker (82-96%),
  and at low K L1's low point is L0's score at its merges (1.00-1.01; the water level). So G/T = (s2 / water) f(x),
  f(x) = ln(1 + x)/x, x = L1's gain per L0 merge over L1 at the water level. With measured inputs the form scores #5
  like the measured G (c held: W3 153, W4 184, W5 231; measured G: 157, 184, 239). Its main input is L2 against the
  water level after the change: after a K raise L2 is at it (1.00); after a K lower L2 is below it, gets no merges of
  its own and fills at L1's net push up to the water level (oracle within 5%: W3 122/135, W4 105/134, W5 94/130),
  while the law stops it at its settled L2 (W3 0.62 of the water level; measured 0.74). Using the fill path only where
  L2 cannot reach the water level within the stretch: busy #5 W3 102 → 118, W4 169 → 176, W5 201 → 197; the W5 losses
  are late low-write phases and phase boundaries, where the law's water level after the change is too high (median
  1.18x after a K lower). Ceiling of the form with every input and c measured: W3 224, W4 241, W5 254 busy (bar 242,
  242): busy #5 cannot pass W3 even with perfect inputs.
- **#6:** The deepest level keeps growing as the run adds data; part g adds its own intake from its merges, timed on
  whole pours (W1 34 → 89, W2 1 → 78). On the no-change twins the pour-timed rate hits W1 96, W2 97, so W2's
  remaining misses (K raised 73, K lowered 85) are the law's own change at the deepest level, not the growth rate.
  In busy W3/W4, L1/L2 sizes are off: K raised, the law drops L1 too much; K lowered, it raises L1 too little.
  In W3 after a raise, the law's forecast of incoming bytes goes negative and it predicts no change at all (57 changes,
  8% hit). W1/W2/W5 miss at L2 in bytes written and flows (not yet diagnosed).
- **#6 L2 size from the busy level path (2026-10-28, `clean/diag5/g6fill.py`, `paths5.py` PATHS_RHY=water2,
  diagnostic only):** in busy W3/W4 the size limit is 2-3% of L2 (median; W5 11%), the typical change 14%. The path
  forecast's time-mean L2 over the next stretch (pause from the law's walk, then the rhythm) beats the law's settled
  L2 on busy changes: law W3 33, W4 47, W5 125 hits; path with L2 filling toward the law's settled L2: 61, 62, 159;
  with RocksDB's water-level rule (water2: below the water level sigma' S2 L2 gets no merges of its own and fills at
  L1's net push up to it; above it, it is served and drains at the spare read): 75, 64, 159 (n 269, 269, 273).
  K lowered gains most (W3 16 → 44). No verdict changes (#6 fails everywhere); not in the law yet.
- **#6 deepest level with the tree's own growth (2026-10-29, `clean/diag5/g6last.py`, diagnostic only):** the law
  gives the deepest level what the upper levels give up, with no growth, but the tree grows by the data added. Adding
  the run's own growth (total bytes of levels >= 1 between the two stretches before the change, per op) over the
  stretch shift: calm trees W1 89 → 213 of 264 (33 → 80%), W2 3 → 174 of 244, W5 calm 65 → 59 of 65; busy trees get
  much worse (W3 222 → 1, W4 110 → 8), because there the total also moves with the water levels. Calm trees only
  would give W1 80%, W2 71%, W3 82%, W4 40%, W5 78%. Not in the law yet. The calm-only switch is fitted (calm = W1/W2,
  busy = W3/W4 in these runs, W5 mixed), so this form is dropped (owner, 2026-10-29: "These fixes are becoming
  workload specific, no?").
- **#6 wobble check (2026-10-29, `clean/diag5/g6wob.py`, log `g6wob.log`, diagnostic only):** the same before and
  after stretches and limits as #6, scored on the setting's no-change natives. There is no noise ceiling: each
  back-to-back repeat predicts from the other two at 98-100% at every level and in every workload. The twin (the multi
  run's worker) predicted from the w2 repeats' mean: busy W3 89-92%, W4 86-94%, W5 70-79%. Busy trees' middle levels
  move a lot by themselves: a no-change guess on the twin hits W3 L2 65, L3 23, W4 L2 48, L3 12, L4+ 12 (law on the
  multi runs 12, 20, 17, 16, 12, the same as a no-change guess). The move repeats from run to run but is not a steady
  trend (the twin's own last move per op: 36-63%), and it depends on the run's K history (the multi run against the
  twin's move, an oracle: W3 15-51, W4 27-33). Calm deepest level: the twin's own past growth per op hits W1 88, W2 97
  (multi runs with g6last's total-bytes growth: 80, 71, polluted by earlier changes); W3's deepest level does not move
  in natives (no merges reach it).
- **#6 deepest level, general growth rules (2026-10-29, `clean/diag5/g6grow.py`, log `g6grow.log`, diagnostic
  only; owner: "Test a general bottom level growth rule"):** no calm/busy switch and no workload numbers. Net new data
  per stretch = flushed bytes minus what merges drop (flush − Σ(s + o − x)); it equals the change in the tree's end
  bytes exactly (self-check, 1,565 stretches). Deepest-level hits, W1/W2/W3/W4/W5 (law 34/1/83/41/79):
  A (net new data per op over the stretch shift, only if a merge reached the deepest level in the before-stretch)
  70/72/82/43/72; A2 (the same over two stretches) 75/76/78/38/69; B (the deepest level's own inflow from its merge
  records, x − o + trivial bytes from the level above, per op over the shift) 86/63/82/43/78. In calm trees the levels
  above sit at their targets and about all new data reaches the deepest level (median share 0.91-1.05). On the
  no-change twins: A2 W1 81, W2 96; B W1 96, W2 67 (W2's deepest level fills in a few big merges, so one stretch's
  inflow is a noisy rate; W1's new-data count carries the upper levels' wobble). An oracle with the true growth
  (after-seeing) hits W1 99, W2 53, W3 0: through conservation the deepest level inherits the upper levels' errors,
  which exceed its limit (0.0-0.4% of the level). No rule passes; B gains most with no loss beyond 1 point. Adopted as
  r7 part g (2026-10-29 02:51 UTC, see Changes).
- **#6 deepest level, intake timed on whole pours (2026-10-29, owner: "Start 1. Then we will see the results";
  `clean/diag5/g6pour.py`, log `g6pour.log`, diagnostic only):** RocksDB pushes a level down only while it is over its
  target (static targets B T^(i−1)), so a pour of the level above into the deepest level is the run of its merges from
  the first after it went over its target to the one that leaves it under. Level sizes rebuilt from the host log's
  flush and merge records (self-check: every stretch's end bytes, every level, exact). Pw = the bytes of whole pours
  covering the last stretch (from the last pour end before the change back to the first end at least a stretch's
  length earlier) / the ops between those two ends; zero unless a merge reached the deepest level in the last stretch.
  Deepest-level hits W1/W2/W3/W4/W5 (part g 227/155/221/116/269 of 264/245/269/269/344): Pw **235/192**/221/116/276
  (bars 237.6/220.5/242.1/242.1/309.6; W1 3 short); pour ends inside the stretch only (BIC) 228/162/221/116/278; every
  pour since the first (BIC) 225/176/221/116/277. On the no-change twins Pw hits W1 96, W2 97 (part g 96, 67). Median 3
  pours per stretch in W1/W2, 1 job per pour in W1, 3 in W2. Adopted as part g's rate (2026-10-29 03:38 UTC, see
  Changes).
- **#6 deepest level, why the remaining misses miss (2026-10-29, owner: "Go with a"; `clean/diag5/g6split.py`, log
  `g6split.log`, diagnostic only):** the deepest level changes only by its intake, and part g's forecast hits the
  no-change twins at 96-97%, so the measured change minus part g's growth is the realized K effect. W1: all 29 misses
  have the law's K part under half a limit while the realized effect is just beyond it (median miss 1.2 limits; K
  raised 16, lowered 13). W2: of 53 misses, 19 would hit with growth alone and 19 more have the law's K part about
  twice the realized effect (median realized / law 0.49; K raised 37 misses, lowered 16); 10 have a K part near zero.
  By pours landing in the next stretch (`g6ratio.py`, log `g6ratio.log`): W2 K raised, realized / law = 0.49 with no
  pour (n 33), 0.85 with 1 (n 34), 0.87 with 2 (n 22), 1.28 with 3-4 (n 10): the deepest level gets the law's K part
  only as pours land. W2 K lowered shows no pattern (1.03, 1.14, 0.56, 0.79). W1's law K part is almost always zero
  (7 changes above half a limit); there the realized effect has a steady sign: K raised −0.39 limits (median;
  quartiles −0.80, +0.01), K lowered +0.19 (−0.13, +0.60); W2 the same (−0.32, +0.22). The law's K part scaled by
  the share that pours down within the stretch was tried and changes nothing (see Trials not adopted).
- **#6 deepest level, which half of the balance misses (2026-10-29, owner: "GO with a"; `clean/diag5/g6oracle.py`,
  log `g6oracle.log`, diagnostic, measured inputs after seeing):** the tree's bytes change only by flushes in and
  copies dropped, so the deepest level's change = N (the change in the tree's mean total, every level) − the measured
  changes of the levels above, exactly (self-check: 0 of 1,391 off). Hits W1/W2/W3/W4/W5, law 235/192/221/116/276:
  N measured with the law's own predicted changes above (L0 = its #2 × bytes per flush) **262**/208/0/10/204; part g's
  growth with the measured levels above 214/**220**/0/4/211; the before-stretch's net new data per op × shift with the
  measured levels above 153/197/2/**245**/252. So in W1 the law's upper levels are right and the miss is the new-data
  forecast: the tree's new data beats part g's growth by +0.22 limits when K is raised and falls short by 0.43 when
  lowered (medians), while L0 holds +0.61 / −0.63 limits more (the law's #2 gets L0 right: −0.40 vs −0.39 after
  subtracting). Copies are dropped only when merged, so bytes L0 holds longer keep their stale copies. In W2 the main
  miss is the levels above (measured: 220, half a hit under the bar). In W3/W5 the deepest level takes almost nothing
  (the data stays above), so its balance says nothing; in W4 a plain net-rate balance would pass (245) if the busy
  middle levels were right. Strict balance with no measured input (part g's growth − the law's changes above):
  203/190/18/66/266; without L0 233/163/36/65/266; with Nf 148/173/0/10/190: none beats the law.
- **#6 L2, which quantity misses (2026-10-29, owner: "Go with a, but why only W1 and W2?" → all five;
  `clean/diag5/g6l2.py`, log `g6l2.log`, diagnostic only; law and no-change columns equal the scorer's L2 rows).**
  Law hit % size / bytes written / rewrite per byte / flow out: W1 99/79/93/65, W2 89/59/98/39, W3 12/31/35/29, W4
  17/41/49/38, W5 56/68/80/65 (W5 calm windows 100/94/98/95, busy 45/62/75/58). Flow out and bytes written fail in
  every workload, and every such miss in W1, W2 and W4's flow out is one where the law gives no change (calm flows are
  pinned). A no-change guess on the no-change twin between the same two stretches hits flow out only 85/55/88/83/69%
  (repeats 88/55/91/85/75), bytes written 95/74/88/83/72: L2's outflow moves by itself, the same in every repeat
  (L2 pushes in pours), so a law with K effects only cannot reach 90% on L2's flow out in any workload. The runs still
  move more than the twins (median |change| W1 0.63 vs 0.36 limits, W2 1.39 vs 0.90, W3 3.43 vs 0.24, W4 1.91 vs 0.28),
  so K moves it too. Busy L2 size: the law's change is mostly the wrong size (W3 big 79, small 53; W4 big 92, small
  46 of 222 misses); the twin holds still 65% (W3), 48% (W4). W2 size: 19 of 28 misses are the law's change pushing a
  no-change hit out (K raised 83%, lowered 95%).
- **#10:** L1 and L2 flows miss in every workload (34-80%). They track the L1/L2 write misses under #6; not diagnosed
  separately.
- **#1, #9, #11:** there is no multi-run scorer for these yet.

## Trials not adopted (multi-change runs)

- **#5 from the busy level path (2026-10-28, `clean/diag5/paths5.py`, diagnostic):** busy hits W3 92 → 131, W4 150 →
  132, W5 206 → 205 (path means for both levels). Adding the first L0 merge's batch after the pause, the spare worker
  time to a rising L1, the tree's growth trend in L2's target, or the previous change's path as the before-stretch
  each made it worse (W4 down to 103-128). Part f (part e on #5 only, #5b keeps om5) scores #5 like e and leaves #5b
  where it was.

- **#5 with om5's L2 baseline from the run's own last stretch (2026-10-28, diagnostic only):** busy K raised W3 52 →
  66, W4 72 → 86; K lowered W3 40 → 37, W4 78 → 71, W5 89 → 72. It is only right where the law's L1 is right too:
  in W5 the tree shrinks after many K lowers (L1 −27%, L2 −25%), the law keeps L1 where it was, and taking L2's drop
  alone turns a rise into a fall. Waits for the busy level paths. `clean/diag5/diag5_show.py`, `own5_flips.py`.

- **r7 parts a and b (2026-10-28):** part a cut the write rate x0.64 per L0 file above 20 when an L0 merge starts. It
  passed #2 in W5 but broke single-run #4 W4 (first window 86 → 84, bar 85). The runs show a merge that starts while
  writes are slowed starts at the 8 MiB/s cap, so part d replaced it. Part b (the same cut from 0 files under the brake)
  lowered W4 #2 (83 → 82); dropped. Logs `clean/r7/r7ac_*`, `r7ab_*`, `r7_*`.
- **r7 part q (2026-10-28):** inside the 64 GiB brake stretches, predict the change from the run's own before-stretch
  instead of the law's forecast of the tree at the old K. W4 #2 stayed at 86 (K raised 82 → 88, K lowered 89 → 83), #3 W4
  74 → 65, and single-run #2/#3 W4 broke (windows 2-6: 99 → 47, 87 → 44). Rejected. Logs `clean/r7/r7dpcq_*`.
  That was under the twin measure. Under the run's-own measure its form is part of part o (adopted 12:12 UTC).
- **r7 part r with a fixed span (2026-10-28):** #4's after-state at the write rate of the last 0.25M, 0.5M or 1M ops
  before the change. Only 0.5M passed W5 (306 / 317 / 300 of 344, bar 310), so the span was tuned to these runs;
  counting whole flushes in a fixed span also broke W1/W2 (92 → 81, 91 → 19) until both rates were timed on the
  flushes. The step test timed on flush ends broke W3 (248 → 238: a flush's end moves with the write slowdowns).
  Replaced by the step rule on memtable switches (adopted). Logs `clean/r7/r_*`, `r1m_*`, `r25f_*`, `rcp_*`.

- **r6 (2026-10-28, owner chose "stop for now"):** fixes for busy trees' level sizes (#6). It forecasts incoming bytes
  as flushed bytes minus the copies L0 merges drop (never negative), counts the files L0 holds under the new K, drops
  copies as bytes move down, and moves bytes only downward. It left every verdict unchanged (multi 2 of 10, single 9 of
  13).
  - L1-L3 hits against r5: W3 +92/−44, W4 +6/−4, W5 +32/−59. #5 W3 went from 37 to 48 (still FAIL).
  - On the same-machine replays it fixed the target, W3 case 2 (K lowered): L1 28 → 71%, L2 14 → 28%.
  - It overfills L1/L2 where they barely move (W5 L1: +203 predicted, +15 real).
  - Case 1 (K raised) did not move. There, L3's false growth comes from a separate rule for the level below the
    levels that push data down, which spills L2's excess into L3 whole. r6 does not touch that rule.
  - Code and logs: `~/node_ops/diag/kcase/clean/r6/`.
- **#6 deepest level, intake timed on its own merges (2026-10-29, owner: "Yes, do it"; `clean/diag5/g6time.py`, log
  `g6time.log`, `g6burst.py`):** part g's intake per op over the last stretch, timed instead on the merges into the
  deepest level (part r's rule: intervals between merges, rate since the last BIC step). Self-checks exact (the
  merges' intake per stretch = the windows'; law + g = the adopted rows). Deepest-level hits W1/W2/W3/W4/W5 (part g
  86/63/82/43/78): merges back to the level's first, BIC step 56/68/83/43/77, no step 36/60/82/43/56; merges inside
  the last stretch only, BIC step 81/22/83/43/76. On the no-change twins: part g W1 96, W2 67; back to the first merge
  56, 86. Why: W2's merges into the deepest level come in bursts (74% of gaps under a tenth of the mean gap, CV 1.97;
  W1 33%, CV 1.14), one burst per pour of the level above's excess, so single-merge timing inside a stretch
  over-counts; the long history helps W2 but W1's intake rate drifts over the run. Not adopted; part g stays.
- **#6 deepest level with L0 in the conservation (2026-10-29, `clean/diag5/g6split.py`):** the bytes L0 holds more
  after a K raise (the law's #2 change × bytes per flush) taken off the deepest level: hits W1 235 → 199, W2 192 → 109,
  W3 221 → 61, W4 116 → 77, W5 276 → 275; with the measured L0 change (oracle) 202, 114, 64, 75, 278. Wrong: not adopted.
- **#6 deepest level, K part scaled by the share poured down in time (2026-10-29, owner: "Go with 1";
  `clean/diag5/g6tshare.py`, log `g6tshare.log`, diagnostic only):** one delivery at a time uniform in the first τ
  after the change (τ = the run's own pour spacing into the deepest level before the change, over pour_rate's span),
  stretch of A ops scored on its mean: share = 1 − τ/2A if τ ≤ A, else A/2τ. Hits W1/W2/W3/W4/W5 (law
  235/192/221/116/276) → 234/193/221/116/276 (W2 +12 −11). W1: the law's K part is under half a limit in 257 of 264
  changes, so a scale cannot move it. W2 K raised: the share tracks realized / law (share bins under 0.6, 0.6-0.75,
  0.75-0.85, over 0.85 → 0.41, 0.56, 0.67, 0.93) but gains only 104 vs 102; W2 K lowered: no relation (0.87, 1.14,
  0.92, 0.54). W3/W4: no pour ends at all (the level above stays over its target), W5 254 of 344; share 1 there.
  The same share on the L0 term: 216/156/61/77/275, worse. Not adopted; part g stays.
- **#6 deepest level, strict balance with the copies held bytes keep (2026-10-29, owner: "Go with 1";
  `clean/diag5/g6drop.py`, log `g6drop.log`, diagnostic only, no measured after-data):** RocksDB drops a stale copy only
  when a merge reads it, so bytes held higher up keep copies a merge would have dropped: the deepest level loses only
  the held bytes' surviving share. Part g's growth minus the law's changes above, L0's (#2 × bytes per flush) counted
  at (1 − d0), d0 = copies dropped per L0 byte merged in the before-stretch: W1/W2/W3/W4/W5 219/192/19/66/266 (law
  235/192/221/116/276). Every level's change counted at its byte's share reaching the deepest level (the product of
  the before-stretch's (x − o + trivial)/(s + trivial) down the path): 234/199/44/67/275; that share on L0 only:
  236/198/31/66/266. Measured (N − growth) / the law's L0 change: W1 0.55, W2 0.17, against d0 0.16 / 0.05 and the
  path share 0.91 / 0.22: W1 sits between the two ends, W2 near the path. None reaches W1's bar (237.6); busy trees
  lose (their deepest level takes almost nothing). Not adopted.

## Sources
- Single-change runs: `~/node_ops/diag/kcase/v2/out_r18/summary.md`. Accepted limitations: W1 hot key range (#5b), the
  read limitation (#11 W1/W2), #6 W3/W4/W5.
- Multi-change runs: `~/node_ops/diag/kcase/clean/r7/adopted_multi.log` (#2-#5b, #7, #8a, #8b) and `adopted_levels.log`
  (#6, #10: every level, size, bytes written, rewrite per byte pushed, flow out), from `run_adopted.sh` on worker 3.
  Scored with SCORE=own (no twin). Single-change runs under r7 (parts d, p, c, w, j): `adopted_single.log`. The logs
  before part j are `adopted_r_*`.

## Changes
- 2026-10-27: created. Round 18 for single-change runs, r5 for multi-change runs.
- 2026-10-28: no score change. #6's reason updated from the zero-prediction trace (`clean/trace0/`); the inflow trial
  (inf5) and the hold runs were diagnostics and moved nothing. The 16 same-machine replays (`clean/replay/`) left the
  D-33 hits unchanged and showed #6's W3/W4 misses are the law's own, not the machine gap. r6 was scored and not
  adopted (see "Trials not adopted"). #5b (W2) and #8a (W5) diagnosed (`clean/diag5b/`, `clean/diag8a/`); reasons
  updated, no score change.
- 2026-10-28 11:05 UTC: r7 adopted for multi-change runs (parts d, p, c). #2: W2 90 → 98 and W5 87 → 90 now pass, W3 94
  → 97, W4 84 → 86 (still FAIL), W1 97 → 100. Part c: calm trees compare with the run's own measured before-stretch.
  Part d: an L0 merge that starts while writes are slowed (20+ files or the 64 GiB brake) starts at the write cap
  (column_family.cc SetupDelay; 74-100% of such windows within 5% of it). Part p: the same inside the brake forecast.
  Side effects: #5b W3 63 → 64, W5 81 → 80; levels ±1 point; no verdict changed (multi, single, levels). Count stays
  2 of 10 (multi), 9 of 13 (single).
- 2026-10-28 12:10 UTC: multi-change runs rescored against the run's own next stretch, with no twin run (owner). Logs
  `clean/r7/own_multi.log`, `own_lv_compare.log` (SCORE=own). Count unchanged, 2 of 10. Cells moved: #2 W5 90 → 91;
  #3 W5 now FAILs (85); #4 W5 now FAILs (87); #5 W2 now passes (91); the deepest level drops to W1 34, W2 1 (growth
  between stretches is not in the law).
- 2026-10-28 12:12 UTC: r7 part o adopted for the multi-change runs (owner). After a change the run follows the new K
  only, so only the new-K forecast's 64 GiB brake cuts a stretch, and braked stretches predict the change from the
  run's own last stretch (no "K never changed" forecast; it was a twin inside the law). #2 now PASSES in every
  workload: W4 86 → 93 (250/269), W5 91 (314/344). #3 W3 92 → 94, W4 73 → 82, W5 85 → 86 (W4, W5 still FAIL); #5 W4
  55 → 56; #5b W3 69 → 68; levels ±1 point; no verdict broke. Count 2 → 3 of 10. Single-change runs keep parts d, p, c:
  their twin-differenced measure needs the old-K forecast (with part o, W4 #2 99 → 47), so their scores are
  unchanged. Logs `clean/r7/owno_*` and `adopted_*`.
- 2026-10-28 12:23 UTC: r7 parts w and s adopted for calm #4 (owner chose #4 calm next; nothing broke).
  - Part w: an L0 merge of n files writes n s_f + o_m − h·(o_m·n + s_f·n(n − 1)/2). RocksDB's merge keeps only a key's
    newest copy, so a bigger batch drops more L1 copies and more of its own older copies. s_f, o_m and h come from the
    run's own last stretch. Measured: the written-per-read share falls in a straight line with files per merge (corr
    −0.98 W1, −0.99 W2), while L1's bytes read per merge do not move (corr 0). Diagnosis `clean/diag4c/`.
  - Part s: calm #4 is compared with the run's own measured last stretch, not the old-K schedule (part c's form).
  - Multi: #4 W1 65 → 92 and W2 16 → 91 now pass. No other multi cell or level moved. Count stays 3 of 10 (#4 fails
    in W5, busy trees).
  - Single-change runs take part w only, in the twin form: #4 W2 first window 86 → 99 (FAIL → pass), W1 93 → 95,
    later windows W1 92 → 95, W2 94 → 98. No single verdict broke. Logs `clean/r7/ws_*`, `w_*`, `adopted_*`.
- 2026-10-28 12:39 UTC: r7 part r adopted for #4 (owner: "Try a principled version"; adopt only without a tuned span).
  W5's workload steps its write rate at fixed points in the run (e.g. 945 → 522 bytes flushed per op), and an L0
  merge's bytes per op follow the write rate (BK3). Where the rate is steady the law hit 95% in W5; 33 of its 44
  misses were rate steps. Part r: in the last stretch, the spacing of memtable switches (a switch comes exactly when the
  memtable is full) gives the write rate; the last step in it is found by a standard test (BIC: two rates kept only if
  n·ln(SSE_0/SSE_1) > 2·ln n); #4's after-state is scaled by the rate since that step over the stretch's rate. In a
  steady workload no step is found and nothing changes. #4 W5 300 → 317 (bar 310), W1 243 → 244, W2 222 → 221 (bar
  220.5), W3/W4 unchanged; no other cell or level moved; single runs do not use it (a write-rate step is in the twin
  too and cancels there). #4 now PASSES in every workload; count 3 → 4 of 10. Logs `clean/r7/rcpb_*`, `adopted_*`;
  diagnosis `clean/diag4c/` (busy4, zero4, phase4, recentq, steps_seen).
- 2026-10-28 13:09 UTC: r7 part j adopted for #8a (owner chose #8a W5 next; nothing broke). In W5 every one of the 38
  changes that came while an L0→L1 merge was already running missed (0%); the other 298 hit 92%. W1-W4 showed the same
  (0-11% on 8-18 such changes each). Cause, from RocksDB's code: one compaction runs at a time; L0's score counts only
  files no merge holds (ComputeCompactionScore skips being_compacted files); when the running merge finishes, its
  output lands in L1 and raises L1's score, so deeper merges run before L0's next one. Part j starts #8a/#8b from the
  tree as it will be when that merge finishes:
  - remaining ops = its start op + its bytes × the last stretch's own L0 merge speed (ops per byte) − X. The speed of
    all merges pooled was 3-10x too slow in W3/W4, where L0 merges run while writes are slowed;
  - L0 then holds n_X − n + (remaining ops)/t_F files, and L1 gains its output less the L1 bytes it read (part w's
    form for its own s, o and n);
  - the K-raise/K-lower transitions start from the same state, and #7 counts L0's files before that merge finishes
    (the write slowdown counts every L0 file, CalculateBaseBytes).
  #8a: W1 252 → 260/260, W2 198 → 206/207, W3 256 → 264/269, W4 247 → 263/269, **W5 275 → 310/336 (bar 302.4)**;
  gained 75, lost 0. #8b gained 16, lost 0 (W5 310 → 319). Side effects: #3 W4 +1, W5 +1/−1; #4 W4 250 → 249, W5
  317 → 316 (both still pass); #7 and #2 unchanged; levels unchanged. Single-change runs (forward check): the 14 with a
  merge running at the change go from 0% to 100% on #8a; #4 W5 window 1 56 → 55 (already FAIL); no verdict changed.
  #8a now PASSES in every workload; count 4 → 5 of 10. W5's 26 remaining misses: 20 are changes whose last stretch
  holds a W5 write-rate step (29 of 49 hit, unchanged by part j); changes with neither a step nor a running merge hit
  246 of 249. Logs `clean/r7/j3_*`, `adopted_*`; diagnosis `clean/diag8a/` (slot8a, rem_check, flip7, single8a).
- 2026-10-28 13:35 UTC: r7 part u adopted for calm #5b (owner: "start working on #5 and #5b"; nothing broke). In a calm
  tree L1 sits at its target between L0 merges; each merge leaves L1 b(n) − o_m above it (part w's bytes less the L1
  bytes it read), and the one worker pushes that down before L0's next merge. Part u sums those bursts over the next
  stretch's merges on the new-K schedule, at the last stretch's own L1 bytes written per byte pushed (plus the law's
  overlap change), scales them by part r's write-rate ratio (as #4), and compares with the run's own last stretch. It
  replaces the settled #5b and stor's term in calm trees. **#5b W2 135 → 222/245 (bar 220.5) now PASSES**; W1 209 →
  222 (84); W5 258 → 258 (+5/−5); W3/W4 unchanged (busy). Levels: L1 bytes written W2 55 → 91, W1 79 → 84; nothing
  else moved. Single-change runs do not take it (unchanged). Count stays 5 of 10 (#5b still fails in W1, W3-W5).
  Without the rate ratio W5 fell to 255 (logs `u_*`). Logs `clean/r7/u_*`, `u2_*`, `adopted_*` (before: `adopted_j_*`).
- 2026-10-28 13:41 UTC: part r extended to busy #5b (nothing broke). L1→L2 bytes written per op follow the write rate
  as L0→L1's do (BK3), so busy #5b's after-state is scaled by the rate since the last step, as #4. **#5b W5 258 →
  282/344** (+26/−2; bar 309.6, still FAIL); no other multi cell moved; levels: W5 L1 bytes written 75 → 82; single
  runs unchanged. Count stays 5 of 10. Logs `clean/r7/r5b_*`, `adopted_*` (before: `adopted_u_*`).
- 2026-10-28 14:25 UTC: r7 part e adopted for busy #5 and #5b (nothing broke). om5's #5 measured the change from
  "L2 if K had stayed", the twin's stand-in; the run's-own measure compares with the run's own last stretch, which is
  the law's base form om_b (L2'/L2)(L1/L1') − om_b. Part e drops om5's override, so #5 and #5b (which uses the same
  overlap) take the base form; multi-change runs only. **#5 W3 92 → 102, W4 150 → 169, W5 266 → 261** (7 of the 9
  lost sit at W5's phase boundaries); #5b W3 184 → 191, W4 174 → 186, W5 282 → 285; levels L1 rewrite per byte
  pushed W3 34 → 38, W4 56 → 63, W5 78 → 77, L1 bytes written W3 68 → 71, W4 65 → 69, W5 82 → 83; no other cell
  moved; single runs unchanged. Count stays 5 of 10. Logs `clean/r7/e_*`, `f_*`, `eid_*` (identity), `adopted_*`
  (before: `adopted_pre_e_*`).
- 2026-10-29 02:51 UTC: r7 part g adopted for the deepest level (owner: "Do it"; nothing broke). The deepest level
  pushes nowhere, so it grows only by the merges from the level above (x − o + trivial bytes); part g adds the run's
  own before-stretch intake per op over the shift between the stretches' midpoints; zero when no merge reaches it,
  calm or busy; multi-change runs only (under D-33 the twin grows too and it cancels). **#6 deepest level W1 34 → 86,
  W2 1 → 63, W3 83 → 82, W4 41 → 43, W5 79 → 78** (as `clean/diag5/g6grow.py`'s rule B); W5 L5 size 66 → 65 (one
  change whose after-stretch adds a level); no other cell moved (multi #2-#8b and single runs identical). #6 still
  fails everywhere; count stays 5 of 10. Logs `clean/r7/run_g.log`, `gid_*` (identity), `adopted_*` (before:
  `adopted_pre_g_*`), `g_levels.diff`.
- 2026-10-29 03:38 UTC: part g's rate timed on whole pours (owner: "replace part g's rate with Pw in the law"; nothing
  broke). A pour of the level above into the deepest level runs from its first merge after the level above went over
  its target to the one that leaves it under (`clean/r7/build_pour_r7.py`, level sizes rebuilt from the host log,
  rebuild error 0 in all 135 runs); the rate is the bytes of whole pours covering the last stretch over the ops between
  their ends (`law_r7.pour_rate`; R7_GRATE=stretch gives the 02:51 form). **#6 deepest level W1 227 → 235/264 (bar
  237.6), W2 155 → 192/245, W5 269 → 276/344**, W3 221 and W4 116 unchanged (as `clean/diag5/g6pour.py`'s Pw); no other
  cell moved (identity with R7_GRATE=stretch SAME; multi #2-#8b and single runs identical). #6 still fails everywhere;
  count stays 5 of 10. Logs `clean/r7/run_gp.log`, `gpid_*` (identity), `adopted_*` (before: `adopted_g1_*`),
  `gp_levels.diff`.
