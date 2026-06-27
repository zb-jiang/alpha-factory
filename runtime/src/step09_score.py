from __future__ import annotations

import pandas as pd

from common import OUTPUT_DIR, log_step_end, log_step_start, score_config, write_json, write_table


def normalize(series: pd.Series) -> pd.Series:
    minimum = float(series.min() or 0.0)
    maximum = float(series.max() or 0.0)
    if minimum == maximum:
        return pd.Series(0.0, index=series.index)
    return (series - minimum) / (maximum - minimum)


def run() -> None:
    log_step_start("09", "因子评分排序")
    factor_metrics = pd.read_csv(OUTPUT_DIR / "backtest" / "factor_metrics.csv", index_col=0)
    strategy_metrics = pd.read_csv(OUTPUT_DIR / "backtest" / "strategy_metrics.csv", index_col=0)

    if strategy_metrics.empty:
        print("  警告: 没有因子进入回测，无法进行评分")
        write_table(OUTPUT_DIR / "backtest" / "final_score.csv", pd.DataFrame())
        write_json(OUTPUT_DIR / "backtest" / "top3_factors.json", {"top3": []})
        log_step_end("09", "评分完成 (无因子)")
        return

    merged = factor_metrics.join(strategy_metrics, how="inner")
    merged["ic_stability_score"] = normalize(merged["rank_ic_ir"])
    merged["annual_return_score"] = normalize(merged["annualized_return"])
    merged["drawdown_penalty"] = normalize(merged["max_drawdown"].abs())
    merged["turnover_penalty"] = normalize(merged["turnover"])
    merged["instability_penalty"] = normalize(1 - merged["positive_ic_ratio"])

    scfg = score_config()
    weights = scfg.get("weights", {})
    regime_cfg = dict(scfg.get("regime_analysis", {}))
    neutral_regime_score = float(regime_cfg.get("neutral_score", 0.5))
    if "regime_consistency_score" not in merged.columns:
        merged["regime_consistency_score"] = neutral_regime_score
    merged["regime_consistency_component"] = (
        pd.to_numeric(merged["regime_consistency_score"], errors="coerce")
        .fillna(neutral_regime_score)
        .clip(lower=0.0, upper=1.0)
    )

    w_ic = float(weights.get("ic_stability", 0.35))
    w_ret = float(weights.get("annual_return", 0.50))
    w_dd = float(weights.get("drawdown", 0.05))
    w_to = float(weights.get("turnover", 0.05))
    w_inst = float(weights.get("instability", 0.05))
    neg_penalty = float(scfg.get("negative_return_penalty", 0.5))

    merged["total_score"] = (
        w_ic * merged["ic_stability_score"]
        + w_ret * merged["annual_return_score"]
        - w_dd * merged["drawdown_penalty"]
        - w_to * merged["turnover_penalty"]
        - w_inst * merged["instability_penalty"]
    )

    negative_return_mask = merged["annualized_return"] < 0
    merged.loc[negative_return_mask, "total_score"] -= neg_penalty

    merged = merged.sort_values("total_score", ascending=False)
    write_table(OUTPUT_DIR / "backtest" / "final_score.csv", merged)

    # 丰富 top3_factors.json，补充公式和逻辑说明
    top3 = merged.head(3).reset_index().rename(columns={"index": "factor_name"}).to_dict(orient="records")

    try:
        from common import read_json
        validated_factors = read_json(OUTPUT_DIR / "llm" / "factors_validated.json").get("factors", [])
        factor_details = {f["factor_name"]: f for f in validated_factors}

        for item in top3:
            fname = item["factor_name"]
            if fname in factor_details:
                detail = factor_details[fname]
                item["formula"] = detail.get("formula", "")
                item["llm_direction"] = detail.get("llm_direction", "")
                item["reason"] = detail.get("reason", "")
                item["risk"] = detail.get("risk", "")
                item["expected_failure_regime"] = detail.get("expected_failure_regime", "")
    except Exception as e:
        print(f"  Warning: Failed to enrich top3 factors with formula details: {e}")

    write_json(OUTPUT_DIR / "backtest" / "top3_factors.json", {"top3": top3})
    top3_names = [item.get("factor_name", "?") for item in top3]
    log_step_end("09", "评分完成", details=[f"Top3: {', '.join(top3_names)}" if top3_names else "无因子通过评分"])


if __name__ == "__main__":
    run()
