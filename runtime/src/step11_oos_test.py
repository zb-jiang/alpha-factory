from __future__ import annotations

import json

from common import OUTPUT_DIR, clear_runtime_context, ensure_runtime_dirs, env_config, log_step_end, log_step_start, write_json
from step00_clean import run as run_step00
from step01_precache_tushare import run as run_step01
from step07_eval_factor import run as run_step07
from step08_backtrader import run_backtest_batch_export as run_step08
from step09_score import run as run_step09


def collect_train_top_factors() -> list[dict]:
    """优先收集跨窗口汇总因子，其次回退到各窗口 validation 的 Top3 因子"""
    collected_factors: list[dict] = []
    seen_formulas: set[str] = set()

    summary_paths = [OUTPUT_DIR / "backtest" / "cross_window_top3_factors.json"]
    summary_paths.extend(sorted(OUTPUT_DIR.glob("train_windows/*/validation/backtest/top3_factors.json")))

    for top3_path in summary_paths:
        if not top3_path.exists():
            continue

        with top3_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            top3 = data.get("top3", [])

        for factor in top3:
            formula = factor.get("formula")
            if not formula or formula in seen_formulas:
                continue

            seen_formulas.add(formula)
            collected_factors.append(
                {
                    "factor_name": factor["factor_name"] + "_OOS",
                    "formula": formula,
                    "llm_direction": factor.get("llm_direction", ""),
                    "empirical_direction": factor.get("empirical_direction", factor.get("llm_direction", "")),
                    "reason": factor.get("reason", "来自训练集的优秀因子"),
                    "risk": factor.get("risk", "样本外盲测阶段"),
                }
            )
    return collected_factors


def run() -> None:
    ensure_runtime_dirs()
    clear_runtime_context()
    config = env_config()
    
    if config.get("run_mode") != "test":
        print("================================================================")
        print("警告: 当前 analysis_rule.yaml 中的 run_mode 不是 'test'！")
        print("请先将 analysis_rule.yaml 中的 run_mode 修改为 'test'，然后再运行此脚本进行样本外盲测。")
        print("================================================================")
        return

    log_step_start("11", "样本外盲测 (OOS Test)")

    run_step01()

    print("  收集样本内训练出的优秀因子...")
    factors = collect_train_top_factors()
    if not factors:
        print("  未找到任何训练生成的优秀因子。请先在 run_mode: train 下运行 step10_iterate.py。")
        log_step_end("11", "盲测中止 (无因子)")
        return
        
    print(f"  共收集到 {len(factors)} 个独立的优秀因子")
    
    (OUTPUT_DIR / "llm").mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_DIR / "llm" / "factors_validated.json", {"factors": factors})
    
    run_step07()
    run_step08()
    run_step09()
    
    log_step_end("11", "样本外盲测完成", details=[f"测试因子: {len(factors)} 个"])


if __name__ == "__main__":
    run()
