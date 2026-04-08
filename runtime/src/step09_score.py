from __future__ import annotations

import pandas as pd

from common import OUTPUT_DIR, write_json, write_table


def normalize(series: pd.Series) -> pd.Series:
    minimum = float(series.min() or 0.0)
    maximum = float(series.max() or 0.0)
    if minimum == maximum:
        return pd.Series(0.0, index=series.index)
    return (series - minimum) / (maximum - minimum)


def run() -> None:
    factor_metrics = pd.read_csv(OUTPUT_DIR / "backtest" / "factor_metrics.csv", index_col=0)
    strategy_metrics = pd.read_csv(OUTPUT_DIR / "backtest" / "strategy_metrics.csv", index_col=0)
    merged = factor_metrics.join(strategy_metrics, how="inner")
    merged["ic_stability_score"] = normalize(merged["rank_ic_ir"])
    merged["annual_return_score"] = normalize(merged["annualized_return"])
    merged["drawdown_penalty"] = normalize(merged["max_drawdown"].abs())
    merged["turnover_penalty"] = normalize(merged["turnover"])
    merged["instability_penalty"] = normalize(1 - merged["positive_ic_ratio"])
    
    # 调整权重：提高收益率的权重，降低单纯IC稳定性的权重
    merged["total_score"] = (
        0.20 * merged["ic_stability_score"]
        + 0.45 * merged["annual_return_score"]
        - 0.15 * merged["drawdown_penalty"]
        - 0.10 * merged["turnover_penalty"]
        - 0.10 * merged["instability_penalty"]
    )
    
    # 对负收益进行严厉惩罚，避免负收益因子被选为Top
    negative_return_mask = merged["annualized_return"] < 0
    merged.loc[negative_return_mask, "total_score"] -= 0.5
    
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
                item["direction"] = detail.get("direction", "")
                item["reason"] = detail.get("reason", "")
                item["risk"] = detail.get("risk", "")
    except Exception as e:
        print(f"Warning: Failed to enrich top3 factors with formula details: {e}")

    write_json(OUTPUT_DIR / "backtest" / "top3_factors.json", {"top3": top3})
    print("score ok")


if __name__ == "__main__":
    run()
