#!/usr/bin/env python3
"""Telemetry of a learned2 batch (36_rl_batch.sh; owner, 2026-11-05):

  1  loss_<W>_<mode>.png   per T and level: the training loss of each round and
                           the prequential error (each window predicted before
                           it is learned from) against the noise floor
  2  cost_<W>_<mode>.png   per T: J per operation over the run, learner vs the
                           native run it mirrors (same tree, same binary, same
                           window pricing, the mode's weights)
  3  outputs_<W>_<mode>.png per T: K and m_1..m_3 over the run, every move the
                           agents issued (confident, explore) and every gated one
  4  amplification.csv/.md raw WA, RA (point: SST probes per Get; scan: entries
                           visited per entry returned) and SA, native vs read
                           priority vs write priority, from 04's summaries
  5  throughput_<W>_<mode>.png, decisions.csv, loss_trend.png/.csv, report.md

  35_rl_telemetry.py --rl-root DIR --native-root DIR --prices V5.json --out DIR
      [--q-bar 50000] [--beta-star 2]

--rl-root holds the batch's rl2-<mode>-<W>/T<T>-l1<MiB>-k<K>/ folders (any depth);
--native-root the grid's suite-<W>/T<T>-l1<MiB>-k<K>/ folders (any depth)."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

PIPE = Path(__file__).resolve().parent
ROOT = PIPE.parents[1]
sys.path.insert(0, str(PIPE))
sys.path.insert(0, str(ROOT / "rl_agent"))

MODES = {"read": "read", "write": "write"}
LEVELS = (0, 1, 2, 3)
AMP = (("WA", "write_amplification"), ("RA_point", "point_read_amplification"),
       ("RA_scan", "scan_amplification"), ("SA", "space_amplification"))


def read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return out


def walk(root: Path, leaf: str):
    """Every directory named `leaf` under root, following links."""
    for dirpath, dirnames, _ in os.walk(root, followlinks=True):
        if os.path.basename(dirpath) == leaf and Path(dirpath) != Path(root):
            yield Path(dirpath)
            dirnames[:] = []


ACT3_ONLY = set()   # runs drawn although 28 failed them on ACT-3 alone (data complete)


def act3_only(d: Path) -> bool:
    try:
        r = json.loads((d / "plugin_check.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return (d / "FAILED_PLUGIN_CHECK").exists() and r.get("failed_checks") == ["act3"]


def find_rl(root: Path, tag: str = "rl2", setting: str | None = None) -> dict:
    """Completed runs of the batch tag (36's RL_TAG) and, if given, of the
    start setting only; a run 28 failed on ACT-3 alone counts only where no
    completed run of the same (W, T, mode) exists."""
    runs, fallback = {}, {}
    for d in walk(root, "learned2"):
        m = re.search(re.escape(tag) + r"-(\w+)-(W\d)/T(\d+)-l1(\d+)-k(\d+)/", str(d) + "/")
        if not m:
            continue
        if setting is not None and f"l1{m.group(4)}-k{m.group(5)}" != setting:
            continue
        key = (m.group(2), int(m.group(3)), m.group(1))
        if (d / "COMPLETED").exists():
            runs[key] = d
        elif act3_only(d):
            fallback[key] = d
    for key, d in fallback.items():
        if key not in runs:
            runs[key] = d
            ACT3_ONLY.add(key)
    return runs


def find_native(root: Path, setting: str) -> dict:
    runs = {}
    for d in walk(root, "native"):
        m = re.search(r"suite-(W\d)/T(\d+)-" + re.escape(setting) + r"/", str(d) + "/")
        if m and (d / "COMPLETED").exists():
            runs[(m.group(1), int(m.group(2)))] = d
    return runs


_COLLECT = None


def summary_row(run: Path) -> dict:
    """04's row for the run: from the summary.csv of its results folder, or,
    when 04 has not scored it yet, from 04's own collect_arm on the run."""
    global _COLLECT
    for parent in run.parents:
        f = parent / "graphs" / "summary.csv"
        if f.exists():
            tail = "/".join(run.parts[-4:])
            for row in csv.DictReader(open(f)):
                if row.get("result_directory", "").rstrip("/").endswith(tail):
                    return row
            break
    if not (run / "COMPLETED").exists():
        return {}
    if _COLLECT is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gen04", PIPE / "04_generate_graphs.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _COLLECT = mod
    try:
        row = _COLLECT.collect_arm(run) or {}
    except Exception as error:  # an arm 04 would refuse: reported, not scored
        print(f"[35] 04 refuses {run}: {error}", file=sys.stderr)
        return {}
    return {k: v for k, v in row.items()}


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def rolling(values: list, n: int) -> list:
    out, acc = [], []
    for v in values:
        acc.append(v)
        if len(acc) > n:
            acc.pop(0)
        good = [a for a in acc if math.isfinite(a)]
        out.append(sum(good) / len(good) if good else math.nan)
    return out


def native_windows(run: Path, pricing) -> list:
    """C_W, C_R, C_S, ops and seconds of every 250k-op window of a native
    run, priced as trainer2 prices the learner's (learned2.HostLogPricer)."""
    from learner import learned2 as L2
    pr = L2.HostLogPricer(pricing)
    for r in read_jsonl(run / "host_log.jsonl"):
        pr.add(r)
    out = []
    op0 = 0
    while op0 < 40_000_000:
        b = pr.bounds(op0, op0 + 250_000)
        if b is None:
            break
        p = pr.price(*b)
        p["op1"] = op0 + 250_000
        out.append(p)
        op0 += 250_000
    return out


def mode_weights(mode: str, beta: float) -> tuple:
    return (beta, 1.0, 1.0) if mode == "write" else (1.0, beta, 1.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rl-root", type=Path, required=True)
    ap.add_argument("--native-root", type=Path, required=True)
    ap.add_argument("--prices", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--q-bar", type=float, default=50000.0)
    ap.add_argument("--beta-star", type=float, default=2.0)
    ap.add_argument("--setting", default="l1128-k4",
                    help="the start setting: learner runs and natives of it only")
    ap.add_argument("--tag", default="rl2", help="36's RL_TAG of the batch")
    args = ap.parse_args(argv)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import cost_model_v2 as cm
    import research_objective as ro
    from learner import prices as PM

    args.out.mkdir(parents=True, exist_ok=True)
    record = json.loads(args.prices.read_text())
    contract, _ = ro.load_contract()
    v2 = cm.load_prices(record)
    pricing = PM.Pricing(v2=v2, c_w=v2.c_w,
                         c_s=ro.checked_prices(record, contract, provisional_ok=True)["c_s"],
                         q_bar=args.q_bar, beta_w=1.0, beta_r=1.0, beta_s=1.0)
    rl = find_rl(args.rl_root, args.tag, args.setting)
    nat = find_native(args.native_root, args.setting)
    W_all = sorted({w for w, _, _ in rl} | {w for w, _ in nat})
    T_all = sorted({t for _, t, _ in rl} | {t for _, t in nat})
    print(f"[35] learner runs {len(rl)}, native runs {len(nat)}", flush=True)
    nat_win = {}
    for key, run in sorted(nat.items()):
        if any((key[0], key[1], m) in rl for m in MODES):
            nat_win[key] = native_windows(run, pricing)

    # ---- per run: logs
    data = {}
    for key, run in rl.items():
        dec = read_jsonl(run / "decisions.jsonl")
        tr = read_jsonl(run / "trainer2.jsonl")
        data[key] = {
            "windows": [r for r in dec if r.get("type") == "window"],
            "f2": [r for r in dec if r.get("type") == "f2"],
            "trial": [r for r in dec if r.get("type") == "trial"],
            "twin": [r for r in tr if r.get("type") == "window"],
            "pred": [r for r in tr if r.get("type") == "pred"],
            "train": [r for r in tr if r.get("type") == "train"],
            "summary": summary_row(run)}

    from matplotlib.lines import Line2D

    def legend(fig, items, ncol=None):
        """A figure-level legend under the title: (label, colour, style, marker)."""
        handles = [Line2D([], [], color=c, ls=ls, marker=mk, lw=1.4 if ls != "none" else 0, ms=7)
                   for _, c, ls, mk in items]
        fig.legend(handles, [lab for lab, *_ in items], loc="upper center", bbox_to_anchor=(0.5, 0.965),
                   ncol=ncol or len(items), frameon=False, fontsize=10)

    def grid(title):
        fig, axes = plt.subplots(3, 3, figsize=(15, 11), sharex=True)
        fig.suptitle(title)
        return fig, axes.flatten()

    figures = []
    trend_rows = []
    j_rows = []
    for w in W_all:
        for mode in MODES:
            keys = [(w, t, mode) for t in T_all if (w, t, mode) in data]
            if not keys:
                continue
            # 1: loss per T x level
            fig, axes = plt.subplots(len(T_all), len(LEVELS), figsize=(16, 2.2 * len(T_all)),
                                     squeeze=False)
            fig.suptitle(f"{w}, {mode} priority (beta* {args.beta_star:g}): training loss and prediction error "
                         "per agent (rows: T; columns: level)", fontsize=12, y=0.995)
            legend(fig, [("Training loss (Huber, each training round)", "tab:blue", "-", None),
                         ("Prediction error on windows not yet trained on (RMS of log J, last 20 windows)", "tab:orange", "-", None),
                         ("Noise floor (window-to-window spread at fixed settings)", "grey", "--", None)], ncol=1)
            for i, t in enumerate(T_all):
                d = data.get((w, t, mode))
                for j, level in enumerate(LEVELS):
                    ax = axes[i][j]
                    ax.set_title(f"T={t} L{level}", fontsize=8)
                    if not d:
                        ax.axis("off")
                        continue
                    tr = [r for r in d["train"] if r["level"] == level]
                    pr = [r for r in d["pred"] if r["level"] == level]
                    if not tr:
                        ax.text(0.5, 0.5, "no training\n(too few settled windows)",
                                ha="center", va="center", fontsize=7, transform=ax.transAxes)
                        continue
                    ax.plot([r["op"] / 1e6 for r in tr], [r["train_loss"] for r in tr],
                            lw=0.8, color="tab:blue")
                    if pr:
                        err = [abs(r["err"]) for r in pr]
                        rm = [math.sqrt(v) for v in rolling([e * e for e in err], 20)]
                        ax.plot([r["op1"] / 1e6 for r in pr], rm, lw=1.0, color="tab:orange")
                    nf = [r["noise_floor"] for r in tr if math.isfinite(num(r.get("noise_floor")))]
                    if nf:
                        ax.axhline(nf[-1], color="grey", lw=0.7, ls="--")
                    ax.set_yscale("log")
                    ax.tick_params(labelsize=6)
                    if i == len(T_all) - 1:
                        ax.set_xlabel("operations (millions)", fontsize=7)
                    if j == 0:
                        ax.set_ylabel("loss / error (log scale)", fontsize=7)
                    # trend: first vs last quarter of the prequential errors
                    if len(pr) >= 8:
                        q = len(pr) // 4
                        e1 = math.sqrt(sum(r["err"] ** 2 for r in pr[:q]) / q)
                        e2 = math.sqrt(sum(r["err"] ** 2 for r in pr[-q:]) / q)
                        trend_rows.append({"W": w, "T": t, "mode": mode, "level": level,
                                           "preq_first_q": e1, "preq_last_q": e2,
                                           "ratio": e2 / e1 if e1 > 0 else math.nan,
                                           "train_first": tr[0]["train_loss"],
                                           "train_last": tr[-1]["train_loss"],
                                           "noise_floor": nf[-1] if nf else math.nan,
                                           "preds": len(pr), "rounds": len(tr)})
            fig.tight_layout(rect=(0, 0, 1, 0.94))
            name = f"loss_{w}_{mode}.png"
            fig.savefig(args.out / name, dpi=80)
            plt.close(fig)
            figures.append(name)

            # 2: J over the run; 5: throughput
            wts = mode_weights(mode, args.beta_star)
            fig, axes = grid(f"{w}, {mode} priority: priced cost J per operation over the run (rolling mean of 1M ops)")
            legend(fig, [("learned2 (this batch)", "tab:red", "-", None),
                         ("Native RocksDB, K = 4, same tree (2026-11-04 grid)", "black", "-", None)])
            fig2, axes2 = grid(f"{w}, {mode} priority: throughput over the run (rolling mean of 1M ops)")
            legend(fig2, [("learned2 (this batch)", "tab:red", "-", None),
                          ("Native RocksDB, K = 4, same tree (2026-11-04 grid)", "black", "-", None)])
            for i, t in enumerate(T_all[:9]):
                ax, ax2 = axes[i], axes2[i]
                ax.set_title(f"T={t}", fontsize=9)
                ax2.set_title(f"T={t}", fontsize=9)
                d = data.get((w, t, mode))
                nw0 = nat_win.get((w, t))
                if d and d["twin"] and nw0:
                    rj = sum(num(r["J"]) for r in d["twin"] if num(r["J"]) is not None)
                    ro = sum(r["ops"] for r in d["twin"])
                    nj = sum(wts[0] * p["C_W"] + wts[1] * p["C_R"] + wts[2] * p["C_S"] for p in nw0)
                    no = sum(p["ops"] for p in nw0)
                    j_rows.append({"W": w, "T": t, "mode": mode, "J_learner": rj / ro if ro else math.nan,
                                   "J_native": nj / no if no else math.nan,
                                   "ratio": (rj / ro) / (nj / no) if ro and no and nj else math.nan,
                                   "act3_only": (w, t, mode) in ACT3_ONLY})
                if d and d["twin"]:
                    xs = [r["op1"] / 1e6 for r in d["twin"]]
                    ax.plot(xs, rolling([num(r["J_per_op"]) for r in d["twin"]], 4), color="tab:red", lw=1)
                    ax2.plot(xs, rolling([r["ops"] / r["secs"] if r["secs"] else math.nan
                                          for r in d["twin"]], 4), color="tab:red", lw=1)
                nw = nat_win.get((w, t))
                if nw:
                    js = [(wts[0] * p["C_W"] + wts[1] * p["C_R"] + wts[2] * p["C_S"]) / p["ops"]
                          for p in nw]
                    ax.plot([p["op1"] / 1e6 for p in nw], rolling(js, 4), color="black", lw=1)
                    ax2.plot([p["op1"] / 1e6 for p in nw],
                             rolling([p["ops"] / p["secs"] if p["secs"] else math.nan for p in nw], 4),
                             color="black", lw=1)
                ax.tick_params(labelsize=7)
                ax2.tick_params(labelsize=7)
                if i % 3 == 0:
                    ax.set_ylabel("J per operation (money units)", fontsize=8)
                    ax2.set_ylabel("operations per second", fontsize=8)
                if i >= 6:
                    ax.set_xlabel("operations (millions)", fontsize=8)
                    ax2.set_xlabel("operations (millions)", fontsize=8)
            for f, nm in ((fig, f"cost_{w}_{mode}.png"), (fig2, f"throughput_{w}_{mode}.png")):
                f.tight_layout(rect=(0, 0, 1, 0.93))
                f.savefig(args.out / nm, dpi=80)
                plt.close(f)
                figures.append(nm)

            # 3: outputs
            fig, axes = grid(f"{w}, {mode} priority: the settings each agent chose over the run")
            legend(fig, [("K, the L0 trigger (left axis)", "black", "-", None),
                         ("log2 m_1 (right axis)", "tab:blue", "-", None),
                         ("log2 m_2 (right axis)", "tab:green", "-", None),
                         ("log2 m_3 (right axis)", "tab:purple", "-", None),
                         ("confident move (9 of 10 networks agreed)", "grey", "none", "o"),
                         ("exploration move (random)", "grey", "none", "x"),
                         ("gated: wanted to move, not confident", "grey", "none", "|"),
                         ("K trial switch (planned alternation)", "grey", "none", "."),
                         ("K trial adopted the neighbour", "red", "none", "*"),
                         ("K trial rejected / inconclusive", "tab:orange", "none", "s")], ncol=4)
            for i, t in enumerate(T_all[:9]):
                ax = axes[i]
                ax.set_title(f"T={t}", fontsize=9)
                d = data.get((w, t, mode))
                if not d or not d["windows"]:
                    continue
                xs = [r["op1"] / 1e6 for r in d["windows"]]
                ax.step(xs, [r["k"] for r in d["windows"]], where="post", color="black", lw=1)
                ax.set_ylabel("K", fontsize=7)
                ax.set_ylim(0, 10)
                axr = ax.twinx()
                for lv, col in ((1, "tab:blue"), (2, "tab:green"), (3, "tab:purple")):
                    ys = [math.log2(r["m"][lv]) if len(r["m"]) > lv and r["m"][lv] > 0 else math.nan
                          for r in d["windows"]]
                    axr.step(xs, ys, where="post", color=col, lw=0.8)
                axr.set_ylim(-1.2, 1.2)
                for r in d["f2"]:
                    x = r["op"] / 1e6
                    y = r["new"] if r["level"] == 0 else None
                    col = "black" if r["level"] == 0 else {1: "tab:blue", 2: "tab:green", 3: "tab:purple"}.get(r["level"], "grey")
                    tgt = ax if r["level"] == 0 else axr
                    yy = y if r["level"] == 0 else (math.log2(r["new"]) if r["new"] > 0 else 0)
                    if r["reason"] == "confident":
                        tgt.plot([x], [yy], "o", color=col, ms=4)
                    elif r["reason"] == "explore":
                        tgt.plot([x], [yy], "x", color=col, ms=4)
                    elif r["reason"] == "gate":
                        tgt.plot([x], [yy], "|", color=col, ms=6)
                    elif r["reason"] in ("trial", "back"):
                        tgt.plot([x], [yy], ".", color=col, ms=3)
                    elif r["reason"] == "adopt":
                        tgt.plot([x], [yy], "*", color="red", ms=9)
                    elif r["reason"] in ("reject", "inconclusive"):
                        tgt.plot([x], [yy], "s", color="tab:orange", ms=4, mfc="none")
                ax.tick_params(labelsize=7)
                axr.tick_params(labelsize=7)
                axr.set_ylabel("log2 m (0 = RocksDB default)", fontsize=7)
                if i >= 6:
                    ax.set_xlabel("operations (millions)", fontsize=8)
            fig.tight_layout(rect=(0, 0, 1, 0.91))
            name = f"outputs_{w}_{mode}.png"
            fig.savefig(args.out / name, dpi=80)
            plt.close(fig)
            figures.append(name)

    # 4: raw amplification table
    amp_rows = []
    for w in W_all:
        for t in T_all:
            row = {"W": w, "T": t}
            nrow = summary_row(nat[(w, t)]) if (w, t) in nat else {}
            for label, col in AMP:
                row[f"native_{label}"] = num(nrow.get(col))
            row["native_ops_per_s"] = num(nrow.get("throughput_ops_per_second"))
            for mode in MODES:
                srow = data.get((w, t, mode), {}).get("summary", {})
                for label, col in AMP:
                    row[f"{mode}_{label}"] = num(srow.get(col))
                row[f"{mode}_ops_per_s"] = num(srow.get("throughput_ops_per_second"))
            amp_rows.append(row)
    if amp_rows:
        with open(args.out / "amplification.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(amp_rows[0]))
            wr.writeheader()
            wr.writerows(amp_rows)

    # decisions per run and level
    dec_rows = []
    for (w, t, mode), d in sorted(data.items()):
        by = defaultdict(lambda: defaultdict(int))
        for r in d["f2"]:
            by[r["level"]][r["reason"]] += 1
            by[r["level"]]["moves"] += r["move"] != "hold"
        last = d["windows"][-1] if d["windows"] else {}
        for level in sorted(by):
            dec_rows.append({"W": w, "T": t, "mode": mode, "level": level,
                             **{k: by[level].get(k, 0) for k in
                                ("cold", "hold", "gate", "confident", "explore", "small",
                                 "frozen", "trial", "back", "adopt", "reject", "inconclusive",
                                 "moves")},
                             "final_K": last.get("k"),
                             "final_m": (last.get("m") or [None] * (level + 1))[level]
                             if level and last.get("m") and len(last["m"]) > level else None})
    if dec_rows:
        with open(args.out / "decisions.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(dec_rows[0]))
            wr.writeheader()
            wr.writerows(dec_rows)
    # The L0 agent's K trials (the noise fix): per run, its verdicts.
    trial_rows = []
    for (w, t, mode), d in sorted(data.items()):
        if not d["trial"]:
            continue
        ends = [r for r in d["trial"] if r.get("reason") in ("adopt", "reject", "inconclusive", "abandon")]
        first = d["windows"][0] if d["windows"] else {}
        last = d["windows"][-1] if d["windows"] else {}
        cyc = [r for r in d["windows"] if r.get("cut") == "cycle"]
        trial_rows.append({
            "W": w, "T": t, "mode": mode, "start_K": first.get("k"), "final_K": last.get("k"),
            "windows": len(d["windows"]), "cycle_windows": len(cyc),
            "trials": max((r.get("number", 0) for r in d["trial"]), default=0),
            "switches": sum(1 for r in d["trial"] if r.get("reason") in ("trial", "back")),
            "adopt": sum(1 for r in ends if r["reason"] == "adopt"),
            "reject": sum(1 for r in ends if r["reason"] == "reject"),
            "inconclusive": sum(1 for r in ends if r["reason"] == "inconclusive"),
            "verdicts": "; ".join(
                f"{r['reason']} {r['k_inc']}->{r['k_cand']} at {r['op'] / 1e6:.1f}M"
                + (f" eff {r['effect']:+.3f} ({r['cheaper']}/{r['members']} cheaper)"
                   if r.get("effect") is not None else " (no model)")
                for r in ends)})
    if trial_rows:
        with open(args.out / "trials.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(trial_rows[0]))
            wr.writeheader()
            wr.writerows(trial_rows)
    if j_rows:
        with open(args.out / "j_summary.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(j_rows[0]))
            wr.writeheader()
            wr.writerows(j_rows)
    if trend_rows:
        with open(args.out / "loss_trend.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(trend_rows[0]))
            wr.writeheader()
            wr.writerows(trend_rows)
        fig, ax = plt.subplots(figsize=(8, 4))
        for level in LEVELS:
            ys = [r["ratio"] for r in trend_rows if r["level"] == level and math.isfinite(r["ratio"])]
            ax.scatter([level + (i % 9 - 4) * 0.03 for i in range(len(ys))], ys, s=10,
                       label=f"L{level}: one dot per run ({len(ys)})")
        ax.axhline(1, color="grey", ls="--", lw=0.8, label="no change (ratio 1)")
        ax.legend(fontsize=8, frameon=False, loc="best")
        ax.set_xticks(list(LEVELS), [f"L{lv}" for lv in LEVELS])
        ax.set_yscale("log")
        ax.set_ylabel("prequential error, last quarter / first quarter")
        ax.set_title("Does the error go down? (below 1 = yes), every run")
        fig.tight_layout()
        fig.savefig(args.out / "loss_trend.png", dpi=90)
        plt.close(fig)
        figures.append("loss_trend.png")

    # report
    L = ["# learned2 batch telemetry", "",
         f"Learner runs: {len(rl)}; native runs matched: {len(nat_win)}. beta* {args.beta_star:g}, "
         f"q-bar {args.q_bar:g} (stand-in), prices {args.prices.name}.", ""]
    if ACT3_ONLY:
        L += ["Runs drawn although the plugin check failed them on ACT-3 alone (a logged repair changed "
              "a requested value; data complete, no 04 scoring so no WA/RA/SA): "
              + ", ".join(f"{w} T{t} {m}" for w, t, m in sorted(ACT3_ONLY)), ""]
    if amp_rows:
        L += ["## Raw WA / RA / SA (native | read priority | write priority)", "",
              "| W | T | native WA | RA pt | RA scan | SA | read WA | RA pt | RA scan | SA | write WA | RA pt | RA scan | SA |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        f3 = lambda x: f"{x:.3f}" if math.isfinite(x) else "–"
        for r in amp_rows:
            L.append(f"| {r['W']} | {r['T']} | " + " | ".join(
                f3(r[f"{p}_{a}"]) for p in ("native", "read", "write") for a, _ in AMP) + " |")
        L.append("")
    if trend_rows:
        L += ["## Prequential error, last quarter / first quarter (median over T)", "",
              "| W | mode | " + " | ".join(f"L{lv}" for lv in LEVELS) + " |", "|---|---|" + "---|" * len(LEVELS)]
        for w in W_all:
            for mode in MODES:
                cells = []
                for level in LEVELS:
                    v = [r["ratio"] for r in trend_rows if r["W"] == w and r["mode"] == mode
                         and r["level"] == level and math.isfinite(r["ratio"])]
                    cells.append(f"{st.median(v):.2f} (n={len(v)})" if v else "–")
                L.append(f"| {w} | {mode} | " + " | ".join(cells) + " |")
        L.append("")
    L += ["## Figures", ""] + [f"- {f}" for f in figures]
    (args.out / "report.md").write_text("\n".join(L) + "\n")
    print(f"[35] wrote {len(figures)} figures to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
