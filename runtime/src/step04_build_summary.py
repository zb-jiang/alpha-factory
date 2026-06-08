from __future__ import annotations

import pandas as pd

from common import OUTPUT_DIR, build_summary_payload, env_config, label_name, log_step_end, log_step_start, read_json, write_json


def run() -> None:
    log_step_start("04", "构建 LLM 摘要")
    config = env_config()
    stats = pd.read_csv(OUTPUT_DIR / "health" / "feature_stats.csv")
    corr = pd.read_csv(OUTPUT_DIR / "health" / "feature_corr.csv", index_col=0)
    label_column = label_name(config)
    summary = build_summary_payload(stats, corr, label_column)
    fundamental_health_path = OUTPUT_DIR / "health" / "fundamental_feature_health.json"
    if fundamental_health_path.exists():
        summary["fundamental_feature_health"] = read_json(fundamental_health_path)
    previous_top_path = OUTPUT_DIR / "backtest" / "top3_factors.json"
    if previous_top_path.exists():
        summary["previous_round_top_factors"] = read_json(previous_top_path)
    write_json(OUTPUT_DIR / "health" / "llm_summary.json", summary)
    log_step_end("04", "LLM 摘要构建完成")


if __name__ == "__main__":
    run()
