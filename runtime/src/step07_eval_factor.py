from __future__ import annotations

import pandas as pd

from common import (
    OUTPUT_DIR,
    build_feature_frame,
    env_config,
    evaluate_formula,
    factor_metrics_from_series,
    feature_pool_config,
    load_raw_data,
    read_json,
    write_table,
)


def run() -> None:
    config = env_config()
    feature_cfg = feature_pool_config()
    raw_frame = load_raw_data(config, list(feature_cfg.get("raw_fields", [])))
    feature_frame = build_feature_frame(raw_frame, config, feature_cfg)
    validated = read_json(OUTPUT_DIR / "llm" / "factors_validated.json").get("factors", [])
    label_name = str(config.get("target_label", "future_5d_return"))
    factor_rows: list[pd.DataFrame] = []
    metric_rows: list[dict[str, float]] = []
    factor_input = feature_frame[[item["name"] for item in feature_cfg.get("base_features", [])]]
    label_series = feature_frame[label_name]
    for item in validated:
        factor_name = str(item["factor_name"])
        raw_score = evaluate_formula(str(item["formula"]), factor_input)
        metrics = factor_metrics_from_series(factor_name, raw_score, label_series)
        metrics["llm_direction"] = str(item["llm_direction"])
        metrics["empirical_direction"] = "higher_better" if metrics["mean_rank_ic"] >= 0 else "lower_better"
        factor_rows.append(
            pd.DataFrame(
                {
                    "factor_name": factor_name,
                    "raw_score": raw_score,
                    "score": raw_score,
                }
            )
        )
        metric_rows.append(metrics)
    if not factor_rows:
        raise RuntimeError("没有可评估的合法因子")
    factor_values = pd.concat(factor_rows).reset_index()
    factor_values = factor_values.set_index(["factor_name", "instrument", "datetime"]).sort_index()
    metrics = pd.DataFrame(metric_rows).set_index("factor_name").sort_index()
    write_table(OUTPUT_DIR / "backtest" / "factor_values.parquet", factor_values)
    write_table(OUTPUT_DIR / "backtest" / "factor_metrics.csv", metrics)
    print(f"factor evaluation ok, factors={len(metrics)}")


if __name__ == "__main__":
    run()
