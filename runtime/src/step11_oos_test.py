from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import OUTPUT_DIR, SRC_DIR, clear_runtime_context, ensure_runtime_dirs, env_config, log_step_end, log_step_start, write_json
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


def collect_alpha191_factors(formulas_path: Path, factor_names: list[str] | None = None) -> list[dict]:
    """加载 alpha191 固定因子库

    Args:
        formulas_path: alpha191_formulas.json 文件路径
        factor_names: 指定因子名列表（如 ['alpha1', 'alpha2']），为 None 时加载全部
    """
    if not formulas_path.exists():
        raise FileNotFoundError(f"未找到 alpha191 公式文件: {formulas_path}")

    with formulas_path.open("r", encoding="utf-8") as f:
        formulas = json.load(f)

    selected_set = set(factor_names) if factor_names else None

    factors: list[dict] = []
    for item in formulas:
        name = item["name"]
        if selected_set is not None and name not in selected_set:
            continue
        direction = int(item.get("direction", 1))
        llm_direction = "higher_better" if direction == 1 else "lower_better"
        factors.append(
            {
                "factor_name": name,
                "formula": item["formula"],
                "llm_direction": llm_direction,
                "empirical_direction": llm_direction,
                "reason": "国泰君安 alpha191 固定因子库",
                "risk": "固定因子库 OOS 测试",
            }
        )

    if selected_set is not None:
        found_names = {f["factor_name"] for f in factors}
        missing = selected_set - found_names
        if missing:
            print(f"  警告: 以下因子在公式文件中未找到: {sorted(missing)}")

    return factors


def run(alpha191_path: Path | None = None, factor_names: list[str] | None = None) -> None:
    ensure_runtime_dirs()
    clear_runtime_context()
    config = env_config()

    if config.get("run_mode") != "test":
        print("================================================================")
        print("警告: 当前 analysis_rule.yaml 中的 run_mode 不是 'test'！")
        print("请先将 analysis_rule.yaml 中的 run_mode 修改为 'test'，然后再运行此脚本进行样本外盲测。")
        print("================================================================")
        return

    is_alpha191 = alpha191_path is not None

    if is_alpha191:
        log_step_start("11", "Alpha191 因子库 OOS 测试")
    else:
        log_step_start("11", "样本外盲测 (OOS Test)")

    run_step01()

    if is_alpha191:
        if factor_names:
            print(f"  加载 alpha191 因子库 (指定 {len(factor_names)} 个因子): {factor_names}")
        else:
            print("  加载 alpha191 固定因子库 (全部)...")
        factors = collect_alpha191_factors(alpha191_path, factor_names)
    else:
        print("  收集样本内训练出的优秀因子...")
        factors = collect_train_top_factors()

    if not factors:
        if is_alpha191:
            print("  未找到匹配的 alpha191 因子。")
        else:
            print("  未找到任何训练生成的优秀因子。请先在 run_mode: train 下运行 step10_iterate.py。")
        log_step_end("11", "盲测中止 (无因子)")
        return

    print(f"  共收集到 {len(factors)} 个独立的优秀因子")

    (OUTPUT_DIR / "llm").mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_DIR / "llm" / "factors_validated.json", {"factors": factors})

    run_step07()
    run_step08()
    run_step09()

    log_step_end("11", "OOS 测试完成", details=[f"测试因子: {len(factors)} 个"])


def parse_factor_selection(selection: str) -> list[str]:
    """解析因子选择字符串，支持:
    - 枚举: alpha1,alpha2,alpha3
    - 范围: alpha1-alpha10
    - 混合: alpha1,alpha3-alpha5,alpha8
    """
    import re

    names: list[str] = []
    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue
        range_match = re.match(r"^(.+?)(\d+)-\1(\d+)$", part)
        if range_match:
            prefix, start, end = range_match.group(1), int(range_match.group(2)), int(range_match.group(3))
            if start > end:
                start, end = end, start
            names.extend(f"{prefix}{i}" for i in range(start, end + 1))
        else:
            names.append(part)
    return names


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="step11 样本外盲测")
    parser.add_argument(
        "--staging",
        type=str,
        default=None,
        help="staging 目录路径 (也可通过 STAGING_DIR 环境变量指定)",
    )
    parser.add_argument(
        "--alpha191",
        type=Path,
        default=None,
        help="使用 alpha191 固定因子库，需指定公式文件路径 (如: runtime/alpha191/alpha191_formulas.json)",
    )
    parser.add_argument(
        "--factors",
        type=str,
        default=None,
        help="指定 alpha191 因子，支持枚举(alpha1,alpha2)或范围(alpha1-alpha10)，可混合",
    )
    args = parser.parse_args()

    factor_names = None
    if args.factors:
        factor_names = parse_factor_selection(args.factors)

    run(alpha191_path=args.alpha191, factor_names=factor_names)
