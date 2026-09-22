#!/usr/bin/env python3
"""Marginal and conditional guard override rates from a shadow-logged arm.

E-1 scores the marginal rate: the fraction of guard_ready frames on which the
guard would have overridden something. Corollary E.2 asks for the conditional
rate instead -- how often the guard would actually CHANGE the policy's action --
on the grounds that a shield firing constantly in agreement with the policy has
no influence on the outcome. This stage reports both, from the guard's own
classification (safety_shadow.jsonl) joined to the policy's own decisions
(io.jsonl).

It deliberately does NOT reproduce the force condition. The 2026-09-14 replay
was retracted for exactly that: it reconstructed due age, score and pressure by
interpolating inside episodes, validated only on a cell where the unmodelled
debt term dominated, and was then wrong in both directions elsewhere. Every
quantity here is read directly from a log. The per-term breakdown reports which
terms were TRUE on an override frame, which is evidence about cause; it does not
claim to be the cause, and the residue column names the frames no readable term
explains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


# compaction_picker_rl.h:78. reason_mask is 1 << ActionReason.
REASON_BITS = {
    0: "policy", 1: "kBudget", 2: "maintenance", 3: "emergency",
    4: "fallback", 5: "drain", 6: "kSLO", 7: "kManifest",
    8: "kStaleStructure", 9: "kPosture",
}

# A level is due at score >= 1.0 (compaction_picker_rl.cc:1281), which is also
# the oracle's whole action rule, and force requires due.
DUE_SCORE = 1.0

# Widest offset considered when the two logs disagree on frame count.
MAX_ALIGN_OFFSET = 64

# interval_micros / 1e6 == dt_seconds to the microsecond, and tick jitter makes
# the sequence near-unique, so a correct offset matches ~every frame and a wrong
# one matches ~13%. Anything below this is not an alignment.
ALIGN_MATCH_FLOOR = 0.99


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_fingerprint(fingerprint: str) -> dict:
    """Pull what this stage needs out of the run fingerprint.

    Deliberately not parse_fingerprint_options from 06: that one is anchored
    with fullmatch and rejects unknown trailing fields, which is right for
    manifest pooling and wrong here, where an added segment must not stop an
    analysis from running.
    """
    parsed: dict = {"raw": fingerprint}
    fields = fingerprint.split(":")
    if fields:
        parsed["workload_profile"] = fields[0]
    for field in fields[1:]:
        if re.fullmatch(r"\d+M", field):
            parsed["size_label"] = field
        elif re.fullmatch(r"T\d+", field):
            parsed["size_ratio"] = int(field[1:])
        else:
            l0 = re.fullmatch(r"l0-(\d+)-(\d+)-(\d+)", field)
            if l0:
                parsed["l0_compaction_trigger"] = int(l0.group(1))
                parsed["l0_slowdown_trigger"] = int(l0.group(2))
                parsed["l0_stop_trigger"] = int(l0.group(3))
    return parsed


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    limits = {}
    for entry in manifest.get("level_limits", []):
        limits[int(entry["level"])] = {
            "due_age_limit_micros": float(entry["due_age_limit_micros"]),
            "pressure_limit_score_micros": float(
                entry["pressure_limit_score_micros"]),
            "score_limit": float(entry["score_limit"]),
            "calibrated": bool(entry.get("calibrated", False)),
        }
    return {
        "path": str(path),
        "sha256": sha256(path),
        "allowed_pending_debt_ratio": float(
            manifest.get("allowed_pending_debt_ratio", 0.0)),
        "level_limits": limits,
    }


def load_shadow(path: Path) -> list[dict]:
    frames = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                frames.append(json.loads(line))
    return frames


def load_io(path: Path) -> list[dict]:
    """Group io.jsonl's per-level rows into frames, in first-seen order.

    One row is written per level per decision, all sharing the frame's
    decision_id (rl_agent/server.py:79).
    """
    frames: dict[int, dict] = {}
    order: list[int] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            state = row["input"]
            decision = row.get("diagnostics", {}).get("decision_id")
            if decision is None:
                continue
            frame = frames.get(decision)
            if frame is None:
                frame = frames[decision] = {
                    "decision_id": decision,
                    "levels": [],
                    # Pairs with the shadow frame's interval_micros; this is
                    # what aligns the two logs.
                    "dt_seconds": row.get("diagnostics", {}).get("dt_seconds"),
                    # Global, identical on every row of the frame; this is the
                    # exact quantity DebtRatioBreach compares against the
                    # manifest limit (compaction_picker_rl.cc:1166).
                    "pending_debt_ratio": float(
                        row.get("reward_components", {})
                        .get("pending_debt_ratio", 0.0)),
                }
                order.append(decision)
            frame["levels"].append({
                "level": int(state["level"]),
                "files": float(state.get("files", 0.0)),
                "score": float(state.get("score", 0.0)),
                "due_age_micros": float(state.get("due_age_micros", 0.0)),
                "pressure_score_micros": float(
                    state.get("pressure_score_micros", 0.0)),
                "action": int(row["output"]["action"]),
            })
    return [frames[decision] for decision in order]


def align(shadow: list[dict], io_frames: list[dict]) -> dict:
    """Find the index offset between the two logs and score the fit.

    There is no shared frame id: safety_shadow.jsonl carries a steady_clock
    time and io.jsonl a Python wall clock, on different epochs. But the two are
    produced back to back on the same state in one worker iteration
    (compaction_picker_rl.cc:1913-1915), so they differ only by dropped frames:
    TraceSafetyShadow skips interval_micros == 0, and a native-fallback frame
    writes no io row.

    The shift is recovered from the frame duration, which both logs record --
    interval_micros here, dt_seconds there -- and which jitters around the
    50 ms tick enough to be near-unique per frame. An earlier revision keyed on
    observed_levels instead; that is constant at the level count for an entire
    run, so every offset scored a perfect 1.0 and the search returned whichever
    it happened to try first. A checksum that cannot discriminate must not
    report confidence.
    """
    durations = [frame.get("interval_micros") for frame in shadow]
    seconds = [frame.get("dt_seconds") for frame in io_frames]
    usable = sum(1 for value in seconds if value)
    best = None
    for offset in range(-MAX_ALIGN_OFFSET, MAX_ALIGN_OFFSET + 1):
        pairs = agree = 0
        for index, micros in enumerate(durations):
            other = index + offset
            if not 0 <= other < len(io_frames):
                continue
            if usable:
                if not seconds[other]:
                    continue
                pairs += 1
                agree += abs(micros / 1e6 - seconds[other]) < 1e-6
            else:
                pairs += 1
                agree += (shadow[index].get("observed_levels")
                          == len(io_frames[other]["levels"]))
        if pairs == 0:
            continue
        score = agree / pairs
        if best is None or score > best["match"]:
            best = {"offset": offset, "paired_frames": pairs, "match": score}
    if best is None:
        return {"offset": 0, "paired_frames": 0, "match": 0.0,
                "signal": "none", "trustworthy": False}
    best["signal"] = "interval_micros" if usable else "observed_levels"
    # observed_levels cannot discriminate, so a fit found that way is never
    # trustworthy however well it scores.
    best["trustworthy"] = bool(
        usable and best["match"] >= ALIGN_MATCH_FLOOR)
    return best


def override_events(flags: list[bool]) -> dict:
    """Maximal consecutive stretches of override frames.

    §14.8's substantive finding: how OFTEN the guard engages (one event per run,
    stable across seeds) and how LONG it stays engaged (~36%, swinging 18x under
    cross-validation) are different quantities, and E-1 thresholds the unstable
    one.
    """
    lengths = []
    run = 0
    for flag in flags:
        if flag:
            run += 1
        elif run:
            lengths.append(run)
            run = 0
    if run:
        lengths.append(run)
    return {
        "events": len(lengths),
        "longest_event_frames": max(lengths) if lengths else 0,
        "event_lengths": lengths[:32],
    }


def analyze_arm(result_dir: Path, manifest_root: Path | None) -> dict:
    shadow_path = result_dir / "safety_shadow.jsonl"
    io_path = result_dir / "io.jsonl"
    report: dict = {"result_dir": str(result_dir), "errors": []}
    if not shadow_path.exists():
        report["errors"].append("missing safety_shadow.jsonl")
        return report
    shadow = load_shadow(shadow_path)
    if not shadow:
        report["errors"].append("empty safety_shadow.jsonl")
        return report

    fingerprint = parse_fingerprint(shadow[0].get("experiment_fingerprint", ""))
    report["fingerprint"] = fingerprint
    report["enforcement_enabled"] = any(
        frame.get("enforcement_enabled") for frame in shadow)
    report["interventions_applied"] = sum(
        1 for frame in shadow if frame.get("intervention_applied"))

    ready = [frame for frame in shadow if frame.get("guard_ready")]
    pre_ready = [frame for frame in shadow if not frame.get("guard_ready")]
    ready_override = [
        frame for frame in ready if frame.get("would_override_frame")]
    report["frames"] = {
        "total": len(shadow),
        "ready": len(ready),
        "pre_ready": len(pre_ready),
        "pre_ready_fraction": len(pre_ready) / len(shadow),
        # Forcing is not gated on readiness -- due_age, pressure, score and debt
        # all evaluate before guard_ready -- so an enforcing arm has its policy
        # overridden here under no criterion at all. Reported, never scored.
        "pre_ready_override_fraction": (
            sum(1 for f in pre_ready if f.get("would_override_frame"))
            / len(pre_ready) if pre_ready else 0.0),
    }
    report["marginal_override_fraction"] = (
        len(ready_override) / len(ready) if ready else 0.0)
    report["events"] = override_events(
        [bool(f.get("would_override_frame")) for f in ready])

    reasons: dict[str, int] = {}
    for frame in ready_override:
        mask = int(frame.get("reason_mask", 0))
        names = tuple(sorted(
            name for bit, name in REASON_BITS.items() if mask >> bit & 1))
        key = "+".join(names) if names else "none"
        reasons[key] = reasons.get(key, 0) + 1
    report["ready_override_reasons"] = reasons

    if manifest_root is not None:
        manifest_path = (
            manifest_root / fingerprint.get("workload_profile", "")
            / fingerprint.get("size_label", "")
            / f"T{fingerprint.get('size_ratio', '')}" / "baseline_slo.json")
        if manifest_path.exists():
            manifest = load_manifest(manifest_path)
            recorded = shadow[0].get("baseline_slo_sha256")
            # The run records the hash of the manifest it actually loaded, so a
            # stale or wrong-cell manifest here is caught rather than silently
            # producing limits the run never used.
            manifest["matches_run"] = (recorded == manifest["sha256"])
            if not manifest["matches_run"]:
                report["errors"].append(
                    f"manifest sha256 {manifest['sha256']} does not match "
                    f"run's {recorded}")
            report["manifest"] = manifest
        else:
            report["errors"].append(f"no manifest at {manifest_path}")

    if not io_path.exists():
        report["errors"].append(
            "missing io.jsonl; conditional rate and term breakdown skipped")
        return report

    io_frames = load_io(io_path)
    report["io_frames"] = len(io_frames)
    report["alignment"] = align(shadow, io_frames)

    # Join-free, so it survives any alignment doubt: the force branch can only
    # change an action where the policy deferred a level that was due, and
    # revoke_optional can only bite where it compacted a level that was not.
    due = due_deferred = optional = optional_compacted = 0
    for frame in io_frames:
        for level in frame["levels"]:
            if level["score"] >= DUE_SCORE:
                due += 1
                due_deferred += level["action"] == 0
            elif level["files"] > 0:
                optional += 1
                optional_compacted += level["action"] == 1
    report["policy_actions"] = {
        "due_level_decisions": due,
        "due_deferred": due_deferred,
        "due_deferred_fraction": due_deferred / due if due else 0.0,
        "non_due_level_decisions": optional,
        "non_due_compacted": optional_compacted,
        "non_due_compacted_fraction": (
            optional_compacted / optional if optional else 0.0),
    }

    offset = report["alignment"]["offset"]
    limits = report.get("manifest", {}).get("level_limits", {})
    debt_limit = report.get("manifest", {}).get("allowed_pending_debt_ratio")
    slowdown = fingerprint.get("l0_slowdown_trigger")

    conditional = 0
    scored = 0
    terms: dict[str, int] = {}
    unexplained = 0
    for index, frame in enumerate(shadow):
        if not frame.get("guard_ready"):
            continue
        other = index + offset
        if not 0 <= other < len(io_frames):
            continue
        scored += 1
        if not frame.get("would_override_frame"):
            continue
        io_frame = io_frames[other]
        # Corollary E.2's statistic. Force requires due, so the only frames on
        # which it can change anything are those where the policy deferred a due
        # level. The revoke_optional direction cannot be computed here --
        # prohibit_optional needs slo.write, which is internal C++ hysteresis
        # state with no per-frame record -- so it is bounded separately below by
        # the kSLO frame count, which is how a bare revoke is labelled
        # (compaction_picker_rl.cc:1346).
        if any(level["score"] >= DUE_SCORE and level["action"] == 0
               for level in io_frame["levels"]):
            conditional += 1

        if not limits:
            continue
        fired = set()
        if debt_limit and io_frame["pending_debt_ratio"] >= debt_limit:
            fired.add("global_debt")
        for level in io_frame["levels"]:
            if level["score"] < DUE_SCORE:
                continue
            limit = limits.get(level["level"])
            if limit is None:
                continue
            if level["due_age_micros"] >= limit["due_age_limit_micros"]:
                fired.add("due_age")
            if (level["pressure_score_micros"]
                    >= limit["pressure_limit_score_micros"]):
                fired.add("pressure")
            if level["score"] >= limit["score_limit"]:
                fired.add("score")
        if slowdown is not None:
            l0 = next((lv for lv in io_frame["levels"] if lv["level"] == 0),
                      None)
            if l0 is not None and l0["files"] >= slowdown:
                fired.add("l0_slowdown")
        key = "+".join(sorted(fired)) if fired else "none"
        terms[key] = terms.get(key, 0) + 1
        if not fired:
            unexplained += 1

    kslo_frames = sum(
        1 for frame in ready_override
        if int(frame.get("reason_mask", 0)) >> 6 & 1)
    report["conditional"] = {
        "scored_ready_frames": scored,
        "force_would_change_action": conditional,
        "force_conditional_fraction": conditional / scored if scored else 0.0,
        # Upper bound: every bare revoke_optional is labelled kSLO, but so is an
        # SLO-driven force, so this counts both.
        "revoke_upper_bound_frames": kslo_frames,
        "influence_upper_bound_fraction": (
            (conditional + kslo_frames) / scored if scored else 0.0),
    }
    # Terms true on an override frame. Evidence about cause, not a reproduction
    # of the guard's decision: slo_force_due has no per-frame record and the
    # retention branch is stateful, so "none" is the residue those explain.
    report["override_terms_true"] = terms
    report["override_frames_no_readable_term"] = unexplained
    return report


def summarize(report: dict) -> str:
    if report.get("errors") and "frames" not in report:
        return f"{report['result_dir']}: {'; '.join(report['errors'])}"
    fingerprint = report.get("fingerprint", {})
    cell = (f"{fingerprint.get('size_label', '?')}"
            f"/T{fingerprint.get('size_ratio', '?')}")
    lines = [
        f"{cell}  {report['result_dir']}",
        f"  marginal override   {report['marginal_override_fraction']:.4f}"
        f"  ({report['frames']['ready']} ready frames)",
        f"  events              {report['events']['events']}"
        f"  longest {report['events']['longest_event_frames']} frames",
        f"  pre-ready window    {report['frames']['pre_ready_fraction']:.3f}"
        f" of run, override {report['frames']['pre_ready_override_fraction']:.3f}",
    ]
    conditional = report.get("conditional")
    if conditional:
        lines.append(
            f"  CONDITIONAL         "
            f"{conditional['force_conditional_fraction']:.4f} force-changes"
            f"  (<= {conditional['influence_upper_bound_fraction']:.4f} with"
            f" revoke bound)")
    actions = report.get("policy_actions")
    if actions:
        lines.append(
            f"  policy              due deferred "
            f"{actions['due_deferred']}/{actions['due_level_decisions']}"
            f"  non-due compacted {actions['non_due_compacted']}"
            f"/{actions['non_due_level_decisions']}")
    alignment = report.get("alignment")
    if alignment and not alignment["trustworthy"]:
        lines.append(
            f"  ! alignment offset {alignment['offset']} via"
            f" {alignment['signal']}, match {alignment['match']:.4f}"
            f" -- conditional rate is NOT trustworthy")
    if report.get("override_terms_true"):
        top = sorted(report["override_terms_true"].items(),
                     key=lambda item: -item[1])[:4]
        lines.append("  terms true          "
                     + ", ".join(f"{k}={v}" for k, v in top))
    if report.get("errors"):
        lines.append(f"  ! {'; '.join(report['errors'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Marginal and conditional guard override rates (E-1, E-5).")
    parser.add_argument("result_dirs", nargs="+", type=Path,
                        help="arm result directories holding "
                             "safety_shadow.jsonl and io.jsonl")
    parser.add_argument("--manifest-root", type=Path,
                        help="BASELINE_SLO_DIR; enables the per-term breakdown")
    parser.add_argument("--output", type=Path,
                        help="write the full JSON report here")
    args = parser.parse_args()

    reports = [analyze_arm(directory, args.manifest_root)
               for directory in args.result_dirs]
    for report in reports:
        print(summarize(report))
        print()
    if args.output:
        atomic_json(args.output, {"schema_version": 1, "arms": reports})
    return 1 if any(report.get("errors") for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
