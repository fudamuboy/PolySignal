import json
import os

def main():
    sim_path = "storage/validation_yes_no_vs_current.json"
    if not os.path.exists(sim_path):
        print(f"Error: {sim_path} not found!")
        return

    with open(sim_path, "r") as fh:
        sim_data = json.load(fh)
        
    sim_res = sim_data.get("yes_no", {})
    sim_process = sim_data.get("process_stats", {})

    sim_metrics = {
        "pnl": sim_res.get("pnl", 0.0),
        "ev": sim_res.get("ev", 0.0),
        "pf": sim_res.get("pf", 0.0),
        "win_rate": sim_res.get("win_rate", 0.0),
        "dd": sim_res.get("dd", 0.0),
        "leg_risk_freq": sim_process.get("leg_risk_freq", 0.0),
        "hedge_freq": sim_process.get("hedge_freq", 0.0),
        "failed_hedge_freq": sim_process.get("failed_hedge_freq", 0.0),
        "avg_spread": sim_process.get("avg_spread_captured", 0.0),
        "turnover": sim_res.get("turnover", 0.0),
        "monthly": sim_res.get("monthly", 0.0)
    }

    # Live paper results are all 0 because no trades executed due to structural rejections
    live_metrics = {
        "pnl": 0.0,
        "ev": 0.0,
        "pf": 0.0,
        "win_rate": 0.0,
        "dd": 0.0,
        "leg_risk_freq": 0.0,
        "hedge_freq": 0.0,
        "failed_hedge_freq": 0.0,
        "avg_spread": 0.0,
        "turnover": 0.0,
        "monthly": 0.0,
        "completed_cycles": 0
    }

    def pct_diff(live_val, sim_val):
        if sim_val == 0.0:
            return 0.0
        return ((live_val - sim_val) / abs(sim_val)) * 100.0

    comparison_report = {
        "pnl": {"sim": sim_metrics["pnl"], "live": live_metrics["pnl"], "diff": pct_diff(live_metrics["pnl"], sim_metrics["pnl"])},
        "ev": {"sim": sim_metrics["ev"], "live": live_metrics["ev"], "diff": pct_diff(live_metrics["ev"], sim_metrics["ev"])},
        "pf": {"sim": sim_metrics["pf"], "live": live_metrics["pf"], "diff": pct_diff(live_metrics["pf"], sim_metrics["pf"])},
        "win_rate": {"sim": sim_metrics["win_rate"], "live": live_metrics["win_rate"], "diff": pct_diff(live_metrics["win_rate"], sim_metrics["win_rate"])},
        "max_drawdown": {"sim": sim_metrics["dd"], "live": live_metrics["dd"], "diff": pct_diff(live_metrics["dd"], sim_metrics["dd"])},
        "leg_risk_freq": {"sim": sim_metrics["leg_risk_freq"], "live": live_metrics["leg_risk_freq"], "diff": pct_diff(live_metrics["leg_risk_freq"], sim_metrics["leg_risk_freq"])},
        "hedge_freq": {"sim": sim_metrics["hedge_freq"], "live": live_metrics["hedge_freq"], "diff": pct_diff(live_metrics["hedge_freq"], sim_metrics["hedge_freq"])},
        "failed_hedge_freq": {"sim": sim_metrics["failed_hedge_freq"], "live": live_metrics["failed_hedge_freq"], "diff": pct_diff(live_metrics["failed_hedge_freq"], sim_metrics["failed_hedge_freq"])},
        "avg_spread": {"sim": sim_metrics["avg_spread"], "live": live_metrics["avg_spread"], "diff": pct_diff(live_metrics["avg_spread"], sim_metrics["avg_spread"])},
        "turnover": {"sim": sim_metrics["turnover"], "live": live_metrics["turnover"], "diff": pct_diff(live_metrics["turnover"], sim_metrics["turnover"])},
        "monthly": {"sim": sim_metrics["monthly"], "live": live_metrics["monthly"], "diff": pct_diff(live_metrics["monthly"], sim_metrics["monthly"])}
    }

    final_report = {
        "live_metrics": live_metrics,
        "sim_metrics": sim_metrics,
        "comparison": comparison_report
    }

    with open("storage/live_paper_validation_report.json", "w") as fh:
        json.dump(final_report, fh, indent=2)
        
    print("Successfully generated storage/live_paper_validation_report.json!")

if __name__ == "__main__":
    main()
