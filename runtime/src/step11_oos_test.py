from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from common import OUTPUT_DIR, ensure_runtime_dirs, env_config, write_json
from step01_init_qlib import run as run_step01
from step02_build_feature_pool import run as run_step02
from step07_eval_factor import run as run_step07
from step08_backtest import run as run_step08
from step09_score import run as run_step09


def collect_train_top_factors() -> list[dict]:
    """从之前的各个 iteration 中收集 Top3 因子"""
    collected_factors = []
    seen_formulas = set()
    
    # 遍历 iter_01, iter_02, ...
    for iter_dir in OUTPUT_DIR.glob("iter_*"):
        if not iter_dir.is_dir():
            continue
            
        top3_path = iter_dir / "backtest" / "top3_factors.json"
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
            # 转换成 step07 能识别的格式 (类似 step06 的输出)
            collected_factors.append({
                "factor_name": factor["factor_name"] + "_OOS", # 加上后缀方便区分
                "formula": formula,
                "direction": factor.get("direction", "higher_better"),
                "reason": factor.get("reason", "来自训练集的优秀因子"),
                "risk": factor.get("risk", "样本外盲测阶段"),
            })
            
    return collected_factors


def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    
    if config.get("run_mode") != "test":
        print("================================================================")
        print("警告: 当前 env.yaml 中的 run_mode 不是 'test'！")
        print("请先将 env.yaml 中的 run_mode 修改为 'test'，然后再运行此脚本进行样本外盲测。")
        print("================================================================")
        return

    print("开始收集样本内 (In-Sample) 训练出的优秀因子...")
    factors = collect_train_top_factors()
    if not factors:
        print("未找到任何训练生成的优秀因子。请先在 run_mode: train 下运行 step10_iterate.py。")
        return
        
    print(f"共收集到 {len(factors)} 个独立的优秀因子。")
    
    # 模拟大模型的输出，将收集到的好因子写入 factors_validated.json，供后续步骤读取
    (OUTPUT_DIR / "llm").mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_DIR / "llm" / "factors_validated.json", {"factors": factors})
    
    print("开始进行样本外 (Out-of-Sample) 数据处理与回测...")
    print("------------------------------------------------")
    run_step01()
    run_step02()
    # 跳过 step03 (健康检查), step04 (摘要), step05 (大模型), step06 (验证)
    # 直接评估、回测和打分
    run_step07()
    run_step08()
    run_step09()
    print("------------------------------------------------")
    
    print("\n样本外盲测完成！")
    print("盲测结果保存在 D:/test/test/Qlib/runtime/outputs/backtest/ 中。")
    print("你可以查看 outputs/backtest/top3_factors.json 和 strategy_metrics.csv 来评估这些因子是否真正有效。")


if __name__ == "__main__":
    run()
