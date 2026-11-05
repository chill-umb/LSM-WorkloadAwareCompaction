# Cases 1-2 propagation law: scores

Updated 2026-11-04 ~20:55 UTC. Kept current by the operator: every time a score changes, this file changes with it.

- **Single-change runs:** 675 runs, one K change from a settled K = 4 tree. Law round 18 (`v2/score_k_v2r18.py`), scored
  in settled windows. Pass = at least 90% in every workload. Count: **9 of 13**.
- **Multi-change runs:** 135 runs, 1,430 changes, 7-16 per run. Law r7 (`clean/r7`, parts d, p, c, o, w, s, r, j, u, e, g, y, z, zr, zc, zd, wr, nc, cm; =
  r5 + the write rate at L0 merge start, the calm before-stretch, the brake forecast on the new K only, an L0 merge's
  dropped copies, the workload's write rate since its last step, the L0 merge already running at the change, and each
  L0 merge's L1→L2 burst in calm trees, busy #5/#5b against the run's own last stretch, with no old-K stand-in, and the
  deepest level's own intake from its merges, timed on whole pours, files per L0 merge in closed form, #2 and #4 from formulas with no loop over merges, and #8's first merge from level sizes with no per-file snapshot, and the slowed write rate from the last stretch's own slowed time, else RocksDB's write-delay cap, and calm trees' levels from a threshold cascade, one burst down the tree per L0 merge, and busy trees' L1 and deeper levels from a per-L0-cycle map with the five cause-group fixes of 2026-11-04, and its
  round-2 fixes: the ride's bytes counted once, a rising pause that serves only levels above RocksDB's pick line, L1's
  inflow from flushed bytes, and the trivial-move share of a level that starts pushing; and its round-3 fixes: the
  served levels' drain over one L0 cycle after the first L0 merge, L1's drop share from its pick depth and a frozen
  receiver's filling, L1's hold against its own rest with L2+'s ride on the fast flushes' budget, and the least-overlap
  pick's discount with the zero-overlap trivial share; and the served group's water under the write stalls, held where
  RocksDB's pending-bytes estimate sits at its 64 GiB limit, and after a write-rate step the settled water's deep
  levels joining at the pick water). At
  each K change
  the law predicts every level from the run's own last stretch; the prediction is checked against the run's own next
  stretch (no twin run). Tolerance: the noise band from the original repeats; where that band is 0 on bytes written
  or flow out (the repeats never moved that level), one file's bytes per op of the stretch. Pass = at least 90% hits in every
  workload. Count: **6 of the 10 scored**.

| # | What it checks | Single-change runs (round 18) | Multi-change runs (r7) |
|---|---|---|---|
| 1 | K above the cap acts as the cap | FAIL (W4) | not scored |
| 2 | Mean L0 file count | PASS | **PASS** (92-100%; W5 92, W4 93) |
| 3 | Files per L0→L1 merge | PASS | **PASS** (91-100%; W4 91 (245/269, bar 242.1), W5 96) |
| 4 | L0→L1 bytes written per op | PASS | **PASS** (90-93%; W2 221/245 is half a hit above the bar) |
| 5 | L1→L2 overlap | FAIL | FAIL in 4 (W1 79, W3 38, W4 63, W5 77; W2 91 passes) |
| 5b | L1→L2 bytes written per op | PASS (W1 accepted as a limitation) | FAIL in 4 (W1 84, W3 71, W4 69, W5 83; **W2 91 passes**) |
| 6 | L2 and deeper | FAIL | FAIL in 4. **W1 passes every row** (98% pooled); W2 fails one row, bytes written at L6 (23/26, bar 23.4); W3/W4/W5 (busy) miss at most levels (80.8/76.0/89.2% pooled; rows below 90%: W3 17 of 28, W4 26 of 29, W5 15 of 29; busy trees 80.8/76.0/86.6, case 1 70/68/85 (K raised with writes slowed 66/58/85), case 2 91/84/88; deepest level W1 94, W2 96, W3 89, W4 64, W5 88) |
| 7 | Writes slowed or not | PASS | **PASS** (94-100%) |
| 8a | When the first L0 merge after the change starts | PASS | **PASS** (93-100%; W5 93) |
| 8b | Files in that first merge | PASS | **PASS** (93-100%) |
| 9 | L0 settles within one cycle | PASS | not scored |
| 10 | Flows out of L1 and deeper | FAIL (W4) | FAIL in 3. **W1 and W2 pass every row** (97%, 97%); W3/W4/W5 (busy) 90.1/89.3/90.7% pooled (L1 97/94/94, L2 87/91/91); rows below 90%: W3 L2 87, L3 85, L6 84; W4 L3 85, L4 87, L5 89, L6 81, L7 81; W5 L3 86, L7 86, L8 73 |
| 11 | Probes per Get | PASS (W1/W2 accepted as a limitation) | not scored |

## Why the multi-change runs fail

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
- **#10:** since part nc (calm cascade) W1 and W2 pass. Since part cm (2026-11-03 22:00) busy L1 and L2 flows hit
  W3 91/83, W4 88/89, W5 90/90%; L3 and deeper still miss (W4 L3-L6 74-78%), with the deep levels' sizes under #6.
- **Busy trees (#6, #10), 2026-11-03:** RocksDB's pick rule (highest score at job begin) reproduces every measured
  merge, so the outcome is set by the scores. The busy trials' parts cancel each other's errors (serving-line trial,
  test 2's measured L0 rhythm), so the busy forecast is being rebuilt piece by piece, each piece checked against the
  replay before they are combined (owner: "Go with piece by piece rebuild for busy trees").
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
- **L3 size without the lag rule's spill (2026-10-30, owner: "Run it"; `clean/diagL3/`, `l3_report.md`, `l3_twin.md`,
  diagnostic only, r7's files untouched; base rows equal the adopted rows 1430/1430):** the rule lets the level below
  the pushing levels go over its new cap and spills the excess whole into the next one. K raised, L3 size, W3 and W4:
  210 misses of 250 changes, 61 of them with the spill, and only 2 would hit without it while 7 hits are lost. Re-run
  with the spill dropped (the bytes go nowhere): L3 W3 K raised 20 → 16, W4 20 → 19. Kept in the level above: W3 20 → 16,
  W4 20 → 20, and #5 W3 102 → 86, W4 169 → 152, #5b W3 191 → 174, W4 186 → 177. Why: L3 really does rise in those
  changes (median W3 +349 MiB, W4 +2245; the law's spill +1314, +2095), and the no-change twin rises as much (+350,
  +2348), so the K effect is small (+102, +221) and the spill is wrong as a K effect but stands in for L3's own rise.
  Where L3 catches and pushes nothing (92 changes K raised, 88 misses) the law says +108, +101 and the run gives
  +504, +2269 (twin +421, +2343). The run's own last move does not forecast it (1/45, 5/47), and even the twin's move,
  known after seeing, hits only 33/122 and 36/128 K raised (law 20, 20). This corrects the earlier reading that L3 gets
  bytes that never arrive: those figures measured the K effect alone. Not adopted.
- **#3 from each merge's walk (2026-10-31, owner: "start working on #3"; `clean/diag3/`, `clean/sub3_*`, `clean/sub3_combo/`, reports
  `~/node_ops/reports/2026-10-31-sub-3*.md`; awaiting the owner's ruling):** the law's first merge is nearly always right; what follows is
  wrong. Five trials, each alone, W1/W2/W3/W4/W5 (r7 248/187/253/221/290; re-scored by the operator, same as the agents' figures):
  x, the mean built as the measure builds it, 262/202/254/222/295; t, the per-merge walk for all busy trees, 248/187/261/251/313;
  v, the walk at the rate since a step, 248/187/253/221/313; n, the walk below 20 files, 248/187/257/226/309; h, the hold rule counting
  the levels that will push at the new water, 248/187/257/243/292 (#5b −4, −4, −5). Merged x + t + v + the score-1 floor + no next-stretch
  gate: **262/202/261/251/328, all pass (bars 235.8/181.8/242.1/242.1/303.3)**; #2 W5 314 → 318; levels moved in 5 changes, x1 and f1 only;
  identity with the parts off exact (multi, levels, single). With everything after X removed from the inputs, #3 is unchanged in all 1,430
  changes. #2 and #5b differ in the 63 changes with a merge running at X (an old read of that merge's own end, 0 hits flip). Without t's
  snapshot overlap W4 is 241, and that overlap reads about 2x the measured one (1.44 vs 0.77). Single runs, own measure, not used to choose
  the form (t only): W3 first window 127 → 135/135, later 517 → 626/675; W4 114 → 135/135, 303 → 654/675; W5 92 → 101/135, 89 → 75/675.
  **Rejected by the owner, 2026-10-31: "I don't want a simulator for the propagation law"; t, v and n loop over merges and flushes.**

- **No per-file snapshot (2026-10-31, owner: "Go with it"; `clean/sub8/`, law as adopted, `DRAIN=flat`, `R18S=1`; not adopted):** #8's first merge from each level's mean overlap
  instead of RocksDB's least-overlap-first order on the snapshot at X. A proof run in which reading a snapshot raises gives the same rows. W1/W2/W3/W4/W5 against the bars: **#8a** 260/206/259/**238**/**300** (W4
  bar 242.1, W5 bar 302.4; adopted 263, 310); **#8b** 260/207/**222**/**220**/311 (W3, W4 bar 242.1; adopted 251, 251); **#3** 262/201/256/**236**/321 (W4 bar 242.1; adopted 244); #2 W4 252 → 248, W5
  317 → 316; #7 W4 254 → 253; #4 and levels unchanged. **The count would fall from 6 to 3 of 10.** Single runs, first window only: #3 W3 130 → 128, W4 117 → 111, W5 100 → 97; #2 W5 124 → 122; #4 W3
  123 → 124. `R18S=1` alone changes 210 predictions and flips no hit, so #8 is the only part of the law that needs the snapshot. `DRAIN=off` did not finish within its 600 s limit, twice, and was not diagnosed.

- **#8 without reading every file (2026-10-31, owner: five Opus agents, one worker each; `clean/sub8_{calib,fit,curve,order,stale}/`, reports `~/node_ops/reports/2026-10-31-sub-8*.md`; `order` adopted 2026-11-02 as part zd, see Changes; the others not adopted):** all with `R18S=1`. Operator's re-score on worker 3, W1/W2/W3/W4/W5 (fit not re-scored): `order` (`DRAIN=flat DRAIN_FAM=uni DF_ARR=mix`, overlap of the cheapest m bytes = next level's bytes × q(1 + q)/2, q = m / level's bytes, level bytes only): #8a 260/206/264/262/311, #8b 260/207/251/252/319, #3 262/201/261/244/323. `curve T` and `calib quad` (overlap rate start, rising to the next level's bytes): #8a 260/206/265/263/308, #8b 260/207/251/251/318, #3 262/201/261/243/324. `curve B2` (2 points per level from all files): 260/206/264/263/313, 260/207/251/250/313, 262/201/261/247/324. `stale p32` (32 files per level at X): 260/206/264/264/311, 260/207/251/255/318, 262/201/262/244/323. All six rows above their bars in all five; no-snapshot proof holds for order and T; order's off switch exact and #8a/#8b unchanged with the future removed. Failed: rolling correction (drains in a run do not correlate), sorted past merges, older snapshots (#3 W4 241-242). Against: forms chosen after seeing; W1 has 0 busy solves, W2 1; starting costs 0.3-0.7 all pass, so the bars do not pin the shape; agents report single-run #8b W3 118-119/135 (adopted 131), W5 123-124, #3 W4 first window 117 → 111-113; sequential workloads untested; live reads from code reading only.
- **Pick rule and the serving line (2026-11-03, owner: "focus on #6 and #10", then "Yes to both"; diagnosis w3
  `clean/sub6_pick/`, trial w2 `clean/sub6_line/`):** a MANIFEST + host-log replay rebuilt the tree at every job's begin.
  RocksDB's rule, the highest score at job begin (L0 = max(files / K, L0 bytes / base), ties to the lower level), picks
  1,458,779 of the 1,458,784 measured-phase merges in all five workloads. Because the picker compares only when a job
  starts, levels the slot serves rest below L0's score at its pick: L2 by 0.2/2.1/3.0%, L3+ by 1.4/4.3/4.0% (W3/W4/W5
  medians). Put into the best busy trial (cm + ride + L1 ride + kappa identity, WRATE=Bc) as each level's cap at the
  water, it makes busy #6 worse: W3/W4/W5 71/64/83 → 69/55/78 with the next stretch's measured values, 69/56/77 with
  the last stretch's; #10 76/74/84 → 75/67/83. It also makes busy #6 worse with the measured ride (69/56/78). The trial's
  W4 L2 is already low (median −0.4 limits), and the too-high cap was hiding that. Not adopted.
- **Water ceiling and round-2 issue agents (2026-11-04; reports `~/node_ops/reports/2026-11-04-0905-water-ceiling.md`,
  `2026-11-04-i{1..5}-*.md`, `2026-11-04-1045-comb-plus-i2i3.md`; I1 and I4 adopted, see Changes):** a perfect L0-pick
  water plus a perfect resting line is worth only +2.3/−0.4/+1.3 busy #6 (W3/W4/W5), so no live water fix was built.
  I2 (case 1 receivers: survive share = 1 − d·ω, `I2_KAPD`): no mode beats the law; on top of I1 + I4 mode 1 nets
  #6 −6/0/−10 and mode 3 −1/0/−3, and both pull #10 W5 below 90 (90.2 → 89.6/89.9) and fail flows L5 W5; the idle
  level's d would need a per-file key sketch in the fork. I3 (L2 size in busy trees: fast flushes' own budget, L1's
  cheapest-first profile, live overlaps for the ride): about zero alone; on top of I1 + I4 −1/−5/+2 (fast only) and
  −12/−15/+1 (all three), size L6 W5 fails. I5's case-2 pause rule (`I5_PAUSE`) is the same rule as I1's RISE.
  Multi #2-#8b, single and calm rows unchanged in every variant. Not adopted.
- **The served group's score path, problem 1 (2026-11-04 16:00-18:20 UTC; reports `~/node_ops/reports/2026-11-04-1630-p1-score-path.md`,
  `2026-11-04-1650-p1-slope-hindcast.md`, `2026-11-04-p1g-brake-water.md`; w3 `clean/p1_path`, `clean/p1g`):**
  - A live slope from K's direct effect on L0's L1 read plus the last stretch's balance (hindcast): worse than flat in
    W4 case 2 and W5 (path error W4 c2 6.8% flat vs 8.4-8.9%).
  - The L0-L1 cycle map for stretches with no level ≥ 2 served (`P1M`; L0 reaches 20 files during each long L0 merge,
    L1 pushes its budget per fast flush between merges): it reproduces the climbs, but those stretches carry almost no
    #6 value (deep levels are frozen receivers there). Busy #6 with the live selection 76.8/71.3/85.6, with the measured
    class 78.3/73.0/86.0, against 78.5/73.0/86.0.
  - Agent P1G's other candidates: a join wave timed by the slot's budget (hindcast 9-10% off vs the law's 1.5-6%); a
    self-calibrated case-1 drain (`P1G_SLF`: busy #6 W3 −6, W4 −41, W5 −3 rows; #10 W4 85.9 → 83.9); the slot time freed
    by a write-rate step (case 1 worse, 10.1% → 22.5%); an L0 cap during the climb (W4 c2 free-L0 9.2% → 33.3%).
  - W5: no write stall binds in 171 of its 186 free group stretches, so the brake water does not act there. Part y's pair
    form froze the deep levels' scores within a regime piece; the fix (`P1G_PAIR=2`, after a write-rate step only) is
    adopted (see Changes). Not adopted (agent P1G part 2, report `~/node_ops/reports/2026-11-04-p1g-w5-pair.md`; busy #6
    rows vs the brake-water law): the fix everywhere (`P1G_PAIR=1`: W3 −3, W5 within-phase worse at the picks 4.9% → 8.1%);
    the slot budget from the recent window (`P1G_BREC=1`: W4 −3; with PAIR=1 W3/W4 churn ±13/±37 for +2/+1); the measured
    throughput as budget (`BREC=2`: W3 −351, W4 −240); the budget times the busy share (W3 loses); the pair form in case 1
    (`P1G_PFORM`: W4 −11..−21); survive shares inside part y (`P1G_KAPY`: W5 −18, W3 +9); carrying the last stretch's error
    ratio (errors anti-correlate, corr −0.20); the L0-L1 map on top of the brake water (`P1M=s`: W4 +16, W5 −9).
  - W5's remaining gap (busy 86.6 vs the flat measured mean's 91.3): within a phase the water is set by the slot's spare
    time, 10-30% of its reads, so a 7-9% error in the slot budget becomes a ±4-5% water error, while the #6 size limits
    need about 1-2%. No live window predicts the next stretch's budget closer than 6-9% (whole last stretch 12-22%, its
    recent half 7-9%, the last 0.25M ops 6-9%, a trend 10-11%: the client's op rate drifts within a phase). With the next
    stretch's measured budget (oracle) W5 case 2 within-phase rises 91.0 → 95.8%. The misses left are near misses of
    mixed sign (1-1.7 limits): no bias to correct.
- **K raised while writes are slowed, W3/W4 (2026-11-04 19:22-20:06 UTC; agent C1S, w3 `clean/c1s`, report
  `~/node_ops/reports/2026-11-04-c1s-pinned-raise.md`; work continuing):** writes are slowed by L0's 20 files here (W3
  1,137 of 1,158 rows, W4 all 880), not the 64 GiB brake. With the measured water and survive shares (oracles) the cell
  where K's effective trigger moved still caps at 73% (W3) and 61% (W4); measured sizes and flows (form) 90/87%. Not
  adopted: the corrected balance inside part y itself (`C1S_BAL=2`: #3 W4 91 → 89%, fails); the water relaxing
  geometrically from the first merge at part y's own ρ (`C1S_GEO`: busy #6 W3 −7, W4 −15 rows; #6 f2 W4 fails); the fast
  budget from the last stretch's fast flush intervals (`C1S_BF=1`: W4 +19, W5 −62 rows; `=2` only after a 20+-file first
  merge: W5 −18). Diagnosed, not fixed: after a near-stop first merge (28-34 files; SetupDelay's ×0.6 cut) the count
  relaxes over several cycles (34, 31, 29, 27, 26, 25, 24) and part y's first step is 0.85 file low in W4; part y's
  budgets do not carry from the last stretch (B_s 5-10% high, +22% after a free stretch; B_f off 20-60%); an idle L2's
  survive share is +0.27 high in W3 (needs fork change F, the per-SST key sketch; worth +8 points in W3's moved cells).
  Part 2 (2026-11-04 20:37-20:50 UTC, report `~/node_ops/reports/2026-11-04-c1s-part2.md`), on top of C1S_BAL: worth of
  measured inputs in this cell (W3/W4, oracles): exact settled count 68.6/62.7%, exact rhythm mean 69.1/65.7, exact path
  68.8/66.7, survive shares 69.7/57.7, path + survive shares + two-piece pause 76.8/68.3, sizes + flows 90.4/86.8 (what
  stays missed there is the x and omega forms). With every input of the pinned balance measured from the next stretch the
  settled count is within 2% in only 36-47% of W4's moved changes (the limits need about 2%); perfect budgets are worth
  W3 −2, W4 +18, W5 −30 rows. Not adopted: the settled count in relative form for every pinned last stretch (`C1S_REL=1`:
  W3 +20, W4 −1, #6 f2 W4 fails; `=3` ratio form worse piece-wise); the late-service term per transient cycle
  (`C1S_TD`: W3 −2, W4 +5; right for L2, wrong for L3 in W4); geometric relaxation on the new base (`C1S_GEO`: W4 −29);
  the slowed budget from the last stretch's slowed intervals (`C1S_BS`: W4 −3, W5 −8). The pick gap at slowed-flush
  landings is small (mean 0.1-0.34 file) and part y's relaxation rate is right with C1S_BAL (ρ 0.45 vs 0.50 measured).

## Sources
- Single-change runs: `~/node_ops/diag/kcase/v2/out_r18/summary.md`. Accepted limitations: W1 hot key range (#5b), the
  read limitation (#11 W1/W2), #6 W3/W4/W5.
- Multi-change runs: `~/node_ops/diag/kcase/clean/r7/adopted_multi.log` (#2-#5b, #7, #8a, #8b) and `adopted_levels.log`
  (#6, #10: every level, size, bytes written, rewrite per byte pushed, flow out), from `run_adopted.sh`; since
  2026-11-04 the same law and byte-identical rows on all five workers (w1-w5 `clean/r7`, kept read-only).
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
- 2026-10-31 11:50 UTC: part y, #3 in closed form, multi only (owner: "try the closed-form #3", "go, fix the lowered-trigger rise", "Yes, adopt this";
  `clean/r7/closed3.py`, `rate_step.py`, `stepq.py`, trial `clean/sub3_closed/`, run with `R7_PC_NOMF=1`, adopted rows before it `adopted_pre_y_*`). **#3 passes: count 5 → 6 of 10.** K raised and calm trees: x's mean and
  one cycle's balance. K lowered, busy trees: the two-number form. An L0 merge reads all of L1, so its length follows L1's score, not K. Files
  flushed during it, and L1's score after it, are linear in the pick's files and L1 score, with separate branches for L0 past 20 files, and the
  gap to the next pick is linear level by level. So the path over merges has a closed form in a 2x2 matrix's eigenvalues; it explains the slow rise
  and the saw-tooth after a large first merge. W1/W2/W3/W4/W5: **262/201/261/244/323, all pass**; K lowered 121/121, 99/99, 135/136, 125/134,
  156/164; K raised 141/141, 102/103, 126/133, 119/135, 167/173. First attempt, one geometric rise: 262/201/216/204/316. #2 W5 314 → 318
  (from the no-next-stretch gate); levels moved in the same 5 changes, x1 and f1 only (one cell: W5 L1 73 → 74, no verdict changes); single-run rows unchanged; identity with y off exact (multi, levels, single); the evaluator's self-check against r5 passes in all 1,430 changes (its off state now also turns `R7_PC_NOMF` off). With
  everything after X removed from the inputs, #3 is unchanged in all changes; #2 and #5b differ in the same 63 changes as above. Not a pure
  formula: up to 6 single-merge evaluations (one per regime; the merges between them closed, 63% of merges with no cap on pieces), a root-find for
  each regime's length, and the first merge still from #8's `drain_order` (a per-file sort and level search, not a merge-by-merge walk). 4 pieces gives W4 242 (fails by 0.1), 6 or more 244; form chosen
  after seeing these runs; W4 passes by 2 hits. The same form on K raised is worse (W3 115/133, W4 113/135, W5 158/173), so it applies to K lowered
  only. Single runs, own measure, not used to build the form: W3 first window 127 → 132/135, later 517 → 593/675 (K lowered 212 → 204/270, worse
  than the law); W4 114 → 133/135, 303 → 643/675 (K lowered 135 → 245/270); W5 92 → 108/135, 89 → 72/675. D-33's measure worsens (W4 later
  586 → 290/675, as with the walk), so y stays out of D-33 scoring.
- 2026-10-31 12:00 UTC: parts z, zr, zc, multi and single (owner: "replacing the per-merge calculation in #2 and #4 with the two number formula"; `clean/r7/forward_r7.py`,
  `closed3.py`, `l0_schedule_r7.py`, trial `clean/sub24/`, adopted rows before them `adopted_pre_z_*`). z, K lowered, busy: the two-number path's sums (N, u, N², N u, N a) in closed
  form replace `fill`; past the brake's crossing, its level at three points of the stretch. zr, K raised, busy: N_k − N* = ρ^(k−1) (N_1 − N*) with geometric sums and x linear in N
  from the second merge; #2's excess within 0.27 files of the loop, #3's exact. zc, calm: an arithmetic flush line and merge pieces (median 2, at most 3) replace the flush-by-flush
  schedule for #2 and #4; equal to the loop to within rounding in all 573 and 566 changes. **#2 W4 250 → 252, W5 318 → 317; #4 W4 249 → 250, W5 316 → 318; nothing else moves** (multi
  #3-#8b and levels unchanged; count stays 6 of 10). Single runs (D-33), first window only: #2 W3 132 → 130, W4 131 → 128; #3 W3 128 → 130, W4 118 → 117, W5 98 → 100; #4 W3
  126 → 123; later windows' hits unchanged; no pass/fail at 90% changes. Identity with the three parts off exact (multi, levels, single); self-checks against r5 pass. With everything
  after X removed, #2 and #5b differ in the same 63 changes as before (`c03_parts`), #3 and #4 in none. Up to 6 single-merge evaluations and a root-find per regime as in part y.
  Still not closed form: #8's `drain_order`, a per-file sort and level search over the snapshot at X (no merge loop; the first merge, an input of #2, #3 and #4); the brake forecast's stepped inflow table and level search (#2, #3, #4 on braked
  stretches); the calm schedule's per-merge sums for #5b's bursts and #5b/#10's L1 excess.
- 2026-11-02 05:27 UTC: part zd, multi only (owner: "adopt order"; `clean/r7/drain_curve_r7.py`, `forward_r7.py`, trial `clean/sub8_order/`, adopted rows before it `adopted_pre_zd_*`). #8's
  first-merge wait reads each level's bytes at the change, not the per-file snapshot: the overlap of a level's cheapest m bytes = next level's bytes × q(1 + q)/2, q = m / level's bytes, with arrivals as two
  pools (new files, the old rest), and `R18S=1` for part zr. **#2 W3 262 → 263, W4 252 → 251, W5 317 → 316; #4 W3 248 → 247; #8a W4 263 → 262, W5 310 → 311; #8b W4 251 → 252; #3 unchanged in
  total (W4 +2/−2); #7 unchanged. Count stays 6 of 10.** Thin margins remain at #3 W4 (244, bar 242.1) and #4 W2 (221, bar 220.5). Checks on worker 3 (`r7/zd_check.out`, last line `ZD_EXIT=0`): old parts on the edited code byte-identical to `adopted_pre_zd_multi_rows`; rows
  byte-identical to the trial's re-score; single rows byte-identical; S1 1430/1430 twice; with snapshot reads forced to raise, rows byte-identical; with everything after X removed, 0 #8a or #8b predictions differ and the total stays the 126 known (#2 and #5b, 63 each). Level values move, and no hit line in the levels log moves.
  **Weak spots:** the shape between the floor and the mean was chosen after seeing scores, and floors from 0.3 to 0.7 all pass, so the bars do not pin it. W1 has 0 busy changes and W2 has 1, so only W3-W5 test it. Sequential-style writes are untested. The live reader of level sizes comes from reading the code, was not run, and can lag the change by a job.
  **Single-change runs keep `drain_order` and its snapshot, so the law now has two forms of #8.** With this form, single #8b W3 would fall from 131 to about 119 of 135 and #3 W4's first window from 117 to about 113 (the agent's figures, not re-scored).
  Still not closed form on multi runs: the brake forecast's stepped inflow table and level search; the calm schedule's per-merge sums for #5b's bursts and #5b/#10's L1 excess. Still a fixed-step solve in zd: 3 solves of 60 bisection steps, each with an inner 40-step bisection, time and memory independent of the number of files.
- 2026-11-03: live-input check, no score change. The law's input tables rebuilt from the host log's flush and merge
  records alone (what the plugin receives; `clean/iv_live/`, `clean/multi_live/`, root `~/clean/multi_live`) equal the
  MANIFEST-built ones in every field the law reads, on all 675 runs; the adopted rows and the cascade trial's rows are
  byte-identical on that root. Only the tree at open still comes from the MANIFEST (stands in for the first live snapshot).
- 2026-11-03 15:55 UTC: part wr, multi only (owner: "Adopt Bc"; `clean/r7/forward_r7.py` `wr_rs`, `eval_r7_multi.py`,
  `eval_levels_r7.py`, table `~/clean/multi/wrate/` from `build_wrate.py`, trial `clean/sub6_wrate/` WRATE=Bc, adopted
  rows before it `adopted_pre_wr_*`). The hold rule's slowed write rate: where the last stretch had more than 1 s of
  slowed writes, its own rate as before; where it had none, RocksDB's cap (option delayed_write_rate, 8 MiB/s) / bytes
  per write (1040), in place of the pool over every stretch since the run's start (earlier K values; owner 2026-11-03:
  no old stretches). While writes are slowed, keys/s = the delay rate in force / bytes per write (LOG rate / measured
  1041-1046 B in W3-W5). The pool was used in busy W3 9, W4 43, W5 147 of 269/269/279 changes. **#3 W4 244 → 245;
  nothing else moves (levels log unchanged); count stays 6 of 10.** Checks (`r7_wr_stage/after_wr.log`, last line
  `AFTER_WR_EXIT=0`): multi and level rows byte-identical to the trial's; single rows unchanged; with wr off the trial
  copy gave the adopted rows byte for byte. Other forms tried: last stretch only, else none (multi −13/+1); RocksDB's
  held rate at the change (fork change 3) as the fallback, the same hits as the cap; the held rate always (multi
  −18/+6). Live sources: the stretch's slowed keys and seconds (host log, L0 count); bytes per write from RLOpCounts
  (here the run's end stamp, constant in these runs); the cap needs fork change 1 or the plugin config.
- 2026-11-03 17:00 UTC: part nc, calm trees, multi levels rows only (owner: "Adopt C for calm trees"; the threshold
  cascade of agent "cascade", w4 `clean/sub6_cascade`, ported onto the adopted law + wr in w2 `clean/sub6_ncwr/`;
  installed by `clean/r7_nc_stage/install_nc.sh`, scored by `after_nc.sh`, `AFTER_NC_EXIT=0`; rows before it
  `adopted_pre_nc_*`). Every level of a calm tree is a threshold element: over its target it is pushed down at once,
  under it it gets no merges; each L0 merge sets off one burst down the tree (b(n) − o_m into L1, less the copies each
  merge drops below). Sizes L2.., the deepest level, flows L1.. and bytes written L2.. come from the cascade (nc_r7.py;
  NC_PREV=0: a level that pushed nothing in the last stretch takes r = 1, no earlier stretch). **#6 W1 92 → 98 (passes
  every row), W2 84 → 97 (all rows but bytes written L6, 23/26), W5 73 → 73 (+12/−1 calm); #10 W1 78 → 97, W2 56 → 97
  (both pass every row).** Busy trees untouched; multi #2-#8b and single rows byte-identical. Level rows = the
  trial's byte for byte; with nc off the trial copy gave the adopted rows byte for byte. Live: on the host-log-built
  root (`clean/sub6_ncwr_live/`, `~/clean/multi_live` vs `~/clean/multi_nosnap`) all 21,099 forecasts identical.
  Count stays 6 of 10 (#6 and #10 must pass in every workload).
- 2026-11-03 ~22:00 UTC: **part cm adopted** (owner: "Adopt the combined fixes"), multi levels rows only, busy trees.
  It is trial B's per-L0-cycle map (`cycle_cm.py`) with B's ride, L1 ride and survive-share fixes, plus the 11-04
  cause-group fixes G2 (ride by water-filling), G3 (L1 gains in steps; drops at the new L1 size), G4 (survive share
  from the receiving level's fullness, idle levels' overlap read, recent pick discount, bytes written per byte read)
  and G5 (L1 rows from the map; L1's kept share at the new K). Reports `~/node_ops/reports/2026-11-04-g*.md`; trial
  w2 `clean/g6comb` config cB; stage `clean/r7_g6_stage` (old parts = adopted rows byte for byte; new busy rows = cB,
  other rows = adopted, multi and single unchanged); install `install_g6.sh`, `*.pre_g6`, `adopted_pre_g6_*`.
  **#6 W3 51 → 74, W4 43 → 68, W5 73 → 88 (+691/−67, +855/−114, +637/−87); deepest level W3 87, W4 54, W5 89.
  #10 W3 57 → 87, W4 51 → 83, W5 72 → 90.** W1/W2 unchanged. Rows newly passing: flows L1 (W3, W5), L2, L4 (W5),
  L5 (W3, W5), L7 (W4), size L8 (W5); none lost. Count stays 6 of 10 (#6 and #10 still fail in W3, W4, W5).
- 2026-11-04 ~11:30 UTC: **part cm's round-2 fixes I1 + I4 adopted** (owner: "Yes, adopt I1+I4 as the new law"),
  multi levels rows only, busy trees. Switches `I1_RCONS=1 I1_RISE=1 I1_QF=1 I4_XIDLE=1` in `run_adopted.sh`.
  - I1 RCONS: the ride's bytes were counted twice (added to a level at the water after the cascade had already passed
    them down); they now come off the first level below that is not at the water, times its survive share.
  - I1 RISE (case 2): during the pause before L0's first merge, RocksDB serves a level only if its score beats L0's
    (the pick rule), so the cap is max(old water, new pick line) × target, not the old water alone.
  - I1 QF: L1's inflow = flushed bytes per op × the kept share (the last stretch's L0 net gain also counted L0's own
    stock change).
  - I4 XIDLE: a level that pushed nothing in the last stretch and pushes in the next takes its trivial-move share from
    its bytes with no overlap below at X (SortFileByOverlappingRatio puts them first; each such pick is a trivial
    move). The 135 runs read it from the MANIFEST replay (`i4/zero_tab.json`); new runs read the fork's host-log `zo`
    (fork 9445c093f, root 8411f99; tests green; all nine workers rebuilt and preflighted 2026-11-04, identity
    7de7f999). Reader (owner: "Write the law code"): `i4/build_zero_live.py` tables Z_i at X = `zo` of the last h
    record with op <= X into `i4/zero_live.json`; the law reads it first and falls back to `zero_tab.json` (patch
    `i4/patch_i4b.py`). On the fork's smoke run every one of 265 h records' `zo` equals the MANIFEST replay at its
    version (`i4/check_zero_live.py`).
  Trials w3 `clean/i1_w4deep`, w5 `clean/i4_omx`; combine w3 `clean/comb_i1i4` (report
  `~/node_ops/reports/2026-11-04-1105-comb-i1i4.md`); install `~/node_ops/workers/2026-11-04-adopt-i1i4/`
  (`install_law.sh`, `run_law.sh`; old code in `code_pre_i1i4.tgz`, old rows `adopted_pre_i1i4_*`). Checks: with the
  switches off the rows are the old adopted rows byte for byte; each fix alone = its agent's rows; on all five
  workers the adopted rows = the combine's (lv 4b20323b, multi 6f569488, single 49a57bb9). **#6 W3 74 → 78, W4 68 →
  71, W5 87.5 → 87.6 (+111/−18, +115/−32, +22/−20); busy case 1 W3/W4/W5 66/64/83, case 2 89/78/86; deepest level W3
  89, W4 59, W5 89. #10 W3 86.6 → 89.6, W4 83.5 → 85.1, W5 89.7 → 90.2.** Rows newly passing: flows L1 W4, flows L4
  W3, size L6 W5, bytes written L8 W4; newly failing: flows L2 W5 (309/344, one hit short), flows L7 W4 (21 changes).
  W1/W2, calm trees, multi #2-#8b and single rows unchanged. Count stays 6 of 10 (#6 and #10 still fail in W3, W4, W5).
- 2026-11-04 ~14:40 UTC (owner: "Give these rows a minimum limit, which is one file's worth of bytes"): scorer
  change, law unchanged. A bytes-written or flow-out row whose limit is 0 (the original repeats never moved that level)
  now gets one target file (4 MiB) per op of the scored stretch. Installed on w1-w5 (`eval_levels_r7.py`); rows = the
  offline rescore byte for byte; multi #2-#8b and single rows unchanged. #6 +6 hits (W1 3108 → 3111, W3 2069 → 2070,
  W5 3411 → 3413), #10 none; no row flips, no cell moves at whole percent. The zero-limit misses left are real errors
  (median W3 124 files, W4 511 files off). Report `~/node_ops/reports/2026-11-04-1425-lim0-floor.md`.
- 2026-11-04 ~15:52 UTC: **part cm's round-3 fixes J1-J4 adopted** (owner: "Adopt J1-J4 as the new law"; switches
  J1_HOLD=g, J2_DEPTH=1 J2_FILL=2, J3_PHI=own J3_FAST=1 J3_TD=m, J4_OMR=1 J4_XZ=1; agents' reports
  `~/node_ops/reports/2026-11-04-j{1-path,2-kappa,3-top,4-omx,5-idle}.md`, combine `2026-11-04-1650-comb-j1234.md`;
  install `~/node_ops/workers/2026-11-04-adopt-j1234/`, old code `code_pre_j1234.tgz`, old rows `adopted_pre_j1234_*`).
  On all five workers the new levels rows = the combine's (c72d953e); multi #2-#8b and single rows unchanged. **#6 W3
  2070 → 2093, W4 2098 → 2164, W5 3413 → 3460 (+79/−56, +131/−65, +76/−29); busy 77.7/70.7/84.5 → 78.5/73.0/86.0.
  #10 W4 871 → 879, W5 1173 → 1176, W3 900.** Rows: #10 L2 W4 now passes, L2 W3 86 → 84, L7 W4 86 → 81 (21 changes);
  #6 rows below 90% W3 22 → 18, W4 27 → 26, W5 19 → 15. W1/W2 unchanged. Count stays 6 of 10. With the measured
  served-group path (diagnosis only; J1 off) J2-J4 reach busy 83.4/81.4/92.6.
- 2026-11-04 ~18:24 UTC: **the brake water adopted (P1G_BRK=4)** (owner, 17:45 UTC: "hunt down this issue, and fix it ...
  If the recommended next step produces a good result, go with it"; agent P1G, w3 `clean/p1g`, report
  `~/node_ops/reports/2026-11-04-p1g-brake-water.md`; operator's fresh-copy check w3 `clean/vfy_p1g` and trial install
  `clean/r7_p1g_try`; install `~/node_ops/workers/2026-11-04-adopt-p1g/`, old code `code_pre_p1g.tgz`, old rows
  `adopted_pre_p1g_*`). When the last stretch was stalled (64 GiB brake or 20 L0 files), the served group's water follows
  the root of RocksDB's pending-bytes estimate at its soft limit, so the group drains as the level below fills and joins it
  (piece-wise W4 water error 1.1-1.4% → 0.13-0.16%). On all five workers the new levels rows = the trial's (585e659f);
  multi #2-#8b and single rows unchanged. **#6 W3 2093 → 2125, W4 2164 → 2248, W5 3460 → 3459 (+34/−2, +119/−35, +1/−2);
  busy 78.5/73.0/86.0 → 79.7/75.8/86.0. #10 W3 900 → 906, W4 879 → 912, W5 1176 → 1178.** Rows: #6 size L8 W3 now passes
  (rows below 90% W3 18 → 17); #10 W4 L3 79 → 84, L4 76 → 86, L5 83 → 89, L6 77 → 81. W1/W2 unchanged. Count stays 6 of 10.
- 2026-11-04 ~18:53 UTC: **part y's pair form after a write-rate step adopted (P1G_PAIR=2)** (same standing instruction;
  agent P1G part 2, w3 `clean/p1g`, report `~/node_ops/reports/2026-11-04-p1g-w5-pair.md`; operator's fresh-copy check w3
  `clean/vfy_p1g2` and trial install `clean/r7_p1g2_try`; install `~/node_ops/workers/2026-11-04-adopt-p1g2/`, old code
  `code_pre_p1g2.tgz`, old rows `adopted_pre_p1g2_*`). The pair form now lets a deep level at the water join at the pick
  water s = N/K̂ (a state variable), where it used to freeze the deep scores within a regime piece; used only where part y's
  own write-rate step test fired, and only as the cascade's rhythm water (#2/#3/#4 unchanged). On all five workers the new
  levels rows = the trial's (8ee34929); multi #2-#8b and single rows unchanged. **#6 W5 3459 → 3477 (+23/−6 vs the
  pre-brake law's rows), W3 and W4 unchanged; busy W5 86.0 → 86.6 (case 2 86.6 → 87.9; post-step case 2 67.7 → 73.7%).
  #10 W5 1178 → 1181.** Rows: #6 W5 size L7 now misses (fails at 90 rounded), x L5 W5 now passes; #10 L1 W5 93 → 94, L2 W5
  90 → 91. Count stays 6 of 10. W5's remaining gap: see "Trials not adopted" (problem 1).
- 2026-11-04 ~20:12 UTC: **the pinned balance with the L0 merge counted while writes are slowed adopted (C1S_BAL=1)**
  (owner ~19:20 UTC: an Opus agent on #6 W3/W4 "K raised, writes slowed", "same orders as previous"; agent C1S, w3
  `clean/c1s`, report `~/node_ops/reports/2026-11-04-c1s-pinned-raise.md`; operator's fresh-copy check w3 `clean/vfy_c1s`
  and trial install `clean/r7_c1s_try`; kit `~/node_ops/workers/2026-11-04-adopt-c1s/`, old code `code_pre_c1s.tgz`, old
  rows `adopted_pre_c1s_*`). RocksDB's write-stall L0 count is every L0 file, including those being merged
  (`version_set.cc` `CalculateBaseBytes`: `files_[0].size()` → `l0_delay_trigger_count`), so an L0 merge of 20 or more
  files runs while writes are slowed and only displaces the fast flushes it overlaps: part y's slowed budget loses
  L0c (1 − B_f/B_s) in the settled count N* and in ρ. Used only for the cascade's rhythm water after a raise (part y's
  one-form, a second part y call); #2/#3/#4 unchanged. Piece-wise, files per L0 merge after a raise with writes slowed:
  median error +2.7/+0.8/+3.8% → −0.3/−1.6/+0.7% (W3/W4/W5); where K is above its cap (nothing changes), the settled
  count is within 2% in 71/73/60% of changes (was 12/9/0%). **Installed on w3 only (levels rows d3863b9e; multi #2-#8b and
  single rows unchanged); see the next entry.** **#6 W3 2125 → 2139, W4 2248 → 2251, W5 3477 → 3476
  (+65/−51, +47/−44, +9/−10); busy 79.7/75.8/86.6 → 80.3/75.9/86.6; K raised with writes slowed 64.0/57.3/84.8 →
  65.2/57.7/85.1 (above the cap 67.9 → 79.4 W3, 76.9 → 87.5 W4). #10 W4 912 → 914, W5 1181 → 1180, W3 906.** No row
  flips. Count stays 6 of 10. The rest of the block: see "Trials not adopted" (K raised with writes slowed).
- 2026-11-04 ~20:54 UTC: **the two-piece pause and the unchanged-trigger settled count adopted (C1S_PAUSE=1, C1S_REL=2)**
  (same order; agent C1S part 2, w3 `clean/c1s`, report `~/node_ops/reports/2026-11-04-c1s-part2.md`; operator's
  fresh-copy check w3 `clean/vfy_c1s2` (script `clean/vfy_base.sh`, base B2) and trial install `clean/r7_c1s2_try`; kit
  `~/node_ops/workers/2026-11-04-adopt-c1s2/`, old code `code_pre_c1s2.tgz`, old rows `adopted_pre_c1s2_*`). PAUSE: before
  the first L0 merge after a raise, all L0 files count towards the 20-file slowdown, and the one slot does B_f of work per
  fast flush and B_s per slowed one, so over the (20 − n_x) fast flushes the water falls only by (τ0 − τ1)·W_f/(W_f + W_s),
  W_f = (20 − n_x)B_f, W_s = (n1 − 20)B_s, then the rest (falling pause with n_x < 20 ≤ n1 only; rows whose pause part is
  more than one limit off: W3 L2 57 → 21%, L3 48 → 17%, W4 L2 22 → 6%; W4 L4 26 → 42%, 19 rows, worse). REL=2: where
  L0's effective trigger K̂ = min(K, B/F0) is the same on both sides, L0's score max(n/K, bytes/B) is unchanged, so the
  settled pinned count is the last stretch's own (mean of its last 3 L0 merges, all ≥ 20 files and within one file; live:
  host-log job records; settled count within 2% in those cells W3 60 → 75%, W4 88 → 94%, W5 38 → 62%). **Installed on w3
  (levels rows fd80387a; multi #2-#8b and single rows unchanged); not installed on w1/w2/w4/w5: the owner closed the
  propagation-law work at ~21:00 UTC, so those workers keep the P1G law.** **#6 W3 2139 → 2152, W4 2251 → 2254, W5 3476 → 3475 (+27/−14, +9/−6, +6/−7); busy 80.3/75.9/86.6 →
  80.8/76.0/86.6; K raised with writes slowed 65.2/57.7/85.1 → 66.3/57.8/85.3. #10 W3 906 → 905.** No row flips. Count
  stays 6 of 10.
