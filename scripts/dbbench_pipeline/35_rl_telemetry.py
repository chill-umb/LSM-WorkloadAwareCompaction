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
        if os.path.basename(dirpath) == leaf:
            yield Path(dirpath)
            dirnames[:] = []


def find_rl(root: Path) -> dict:
    runs = {}
    for d in walk(root, "learned2"):
        m = re.search(r"rl2-(\w+)-(W\d)/T(\d+)-l1(\d+)-k(\d+)/", str(d) + "/")
        if m and (d / "COMPLETED").exists():
            runs[(m.group(2), int(m.group(3)), m.group(1))] = d
    return runs


def find_native(root: Path, setting: str) -> dict:
    runs = {}
    for d in walk(root, "native"):
        m = re.search(r"suite-(W\d)/T(\d+)-" + re.escape(setting) + r"/", str(d) + "/")
        if m and (d / "COMPLETED").exists():
            runs[(m.group(1), int(m.group(2)))] = d
    return runs


def summary_row(run: Path) -> dict:
    """04's row for the run, from the summary.csv of its results folder."""
    for parent in run.parents:
        f = parent / "graphs" / "summary.csv"
        if f.exists():
            tail = "/".join(run.parts[-4:])
            for row in csv.DictReader(open(f)):
                if row.get("result_directory", "").rstrip("/").endswith(tail):
                    return row
    return {}


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
    ap.add_argument("--setting", default="l1128-k4")
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
    rl = find_rl(args.rl_root)
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
            "twin": [r for r in tr if r.get("type") == "window"],
            "pred": [r for r in tr if r.get("type") == "pred"],
            "train": [r for r in tr if r.get("type") == "train"],
            "summary": summary_row(run)}

    def grid(title):
        fig, axes = plt.subplots(3, 3, figsize=(15, 11), sharex=True)
        fig.suptitle(title)
        return fig, axes.flatten()

    figures = []
    trend_rows = []
    for w in W_all:
        for mode in MODES:
            keys = [(w, t, mode) for t in T_all if (w, t, mode) in data]
            if not keys:
                continue
            # 1: loss per T x level
            fig, axes = plt.subplots(len(T_all), len(LEVELS), figsize=(16, 2.2 * len(T_all)),
                                     squeeze=False)
            fig.suptitle(f"{w}, {mode} priority (beta* {args.beta_star:g}): training loss "
                         "(blue, each round) and prequential error (orange, rolling 20 windows; "
                         "grey = noise floor)", fontsize=10)
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
            fig.tight_layout(rect=(0, 0, 1, 0.97))
            name = f"loss_{w}_{mode}.png"
            fig.savefig(args.out / name, dpi=80)
            plt.close(fig)
            figures.append(name)

            # 2: J over the run; 5: throughput
            wts = mode_weights(mode, args.beta_star)
            fig, axes = grid(f"{w}, {mode} priority: J per operation (rolling 1M ops), "
                             "learner (red) vs native (black)")
            fig2, axes2 = grid(f"{w}, {mode} priority: throughput (ops/s, rolling 1M ops), "
                               "learner (red) vs native (black)")
            for i, t in enumerate(T_all[:9]):
                ax, ax2 = axes[i], axes2[i]
                ax.set_title(f"T={t}", fontsize=9)
                ax2.set_title(f"T={t}", fontsize=9)
                d = data.get((w, t, mode))
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
            for f, nm in ((fig, f"cost_{w}_{mode}.png"), (fig2, f"throughput_{w}_{mode}.png")):
                f.tight_layout(rect=(0, 0, 1, 0.96))
                f.savefig(args.out / nm, dpi=80)
                plt.close(f)
                figures.append(nm)

            # 3: outputs
            fig, axes = grid(f"{w}, {mode} priority: K (black, left) and log2 m_1..m_3 "
                             "(right); o confident move, x explore move, | gated")
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
                ax.tick_params(labelsize=7)
                axr.tick_params(labelsize=7)
            fig.tight_layout(rect=(0, 0, 1, 0.96))
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
                                ("cold", "hold", "gate", "confident", "explore", "moves")},
                             "final_K": last.get("k"),
                             "final_m": (last.get("m") or [None] * (level + 1))[level]
                             if level and last.get("m") and len(last["m"]) > level else None})
    if dec_rows:
        with open(args.out / "decisions.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(dec_rows[0]))
            wr.writeheader()
            wr.writerows(dec_rows)
    if trend_rows:
        with open(args.out / "loss_trend.csv", "w", newline="") as fo:
            wr = csv.DictWriter(fo, fieldnames=list(trend_rows[0]))
            wr.writeheader()
            wr.writerows(trend_rows)
        fig, ax = plt.subplots(figsize=(8, 4))
        for level in LEVELS:
            ys = [r["ratio"] for r in trend_rows if r["level"] == level and math.isfinite(r["ratio"])]
            ax.scatter([level + (i % 9 - 4) * 0.03 for i in range(len(ys))], ys, s=10)
        ax.axhline(1, color="grey", ls="--", lw=0.8)
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
