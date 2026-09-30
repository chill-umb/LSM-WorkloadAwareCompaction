"""summary.csv rows for the frontier and paired tests, in 04's column names
and as csv.DictReader returns them (strings)."""
import research_objective

CONTRACT, CONTRACT_SHA = research_objective.load_contract()


def row(arm, config, seed, costs, workload="assoc-v1", ratio=2, **extra):
    fields = {
        "arm": arm, "workload_profile": workload, "size_ratio": ratio,
        "size_millions": 10, "dbbench_seed": seed,
        "experiment_fingerprint": f"{workload}:10M:T{ratio}:{config}:mix1-0-0",
        "objective_status": "priced", "session_id": "s1",
        "dbbench_sha256": "a" * 64, "prices_sha256": "p" * 64,
        "reference_rate": "100", "research_objective_sha256": CONTRACT_SHA,
        "settle_hold_seconds": "10",
        "get_operations": 90, "put_operations": 10, "scan_operations": 0,
        "stall_fraction": 0.01, "throughput_ops_per_second": 1000.0,
        "result_directory": f"/r/{workload}/{arm}/{config}/{seed}",
        **dict(zip(("C_W", "C_R", "C_S"), costs)),
        **research_objective.objective_columns(CONTRACT, costs),
    }
    fields.update(extra)
    return {key: str(value) for key, value in fields.items()}
