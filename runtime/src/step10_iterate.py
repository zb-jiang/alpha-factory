from __future__ import annotations

from pathlib import Path

import pandas as pd

from common import (
    OUTPUT_DIR,
    analysis_rule_config,
    archive_iteration_outputs,
    archive_outputs_bundle,
    build_training_windows,
    clear_runtime_context,
    ensure_runtime_dirs,
    env_config,
    log_phase,
    log_step_end,
    log_step_start,
    read_json,
    set_runtime_context,
    write_json,
)
from step00_clean import clean_outputs, run as run_step00
from step01_precache_tushare import run as run_step01
from step02_health_check import run as run_step02
from step03_market_context import run as run_step03
from step04_build_summary import run as run_step04
from step05_call_llm import run as run_step05
from step06_validate_factor import run as run_step06
from step07_eval_factor import run as run_step07
from step08_backtrader import run_backtest_batch_export as run_step08
from step09_score import run as run_step09


def _stage_context(window: dict[str, str], stage: str, iteration: int | None = None) -> dict:
    start_key = f"{stage}_start_date"
    end_key = f"{stage}_end_date"
    return {
        "run_mode": "train",
        "train_start_date": window[start_key],
        "train_end_date": window[end_key],
        "workflow_state": {
            "window_id": window["window_id"],
            "stage": stage,
            "iteration": iteration,
            "window_config": window,
        },
    }


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _metric_value(row: dict, *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value is None or pd.isna(value):
            continue
        return float(value)
    return 0.0


def _run_discovery_iteration(iteration: int, total_iterations: int, window: dict[str, str], scope: str) -> None:
    log_phase(f"{window['window_id']} - discovery - iteration_{iteration}/{total_iterations}")
    run_step04()
    run_step05()
    run_step06()
    run_step07()
    run_step08()
    run_step09()
    write_json(
        OUTPUT_DIR / "backtest" / "iteration_context.json",
        {
            "iteration": iteration,
            "window_id": window["window_id"],
            "stage": "discovery",
            "status": "completed",
            "date_range": {
                "start": window["discovery_start_date"],
                "end": window["discovery_end_date"],
            },
        },
    )
    archive_iteration_outputs(iteration, scope=scope)


def _collect_discovery_passed_factors(scope: str) -> list[dict]:
    scope_root = OUTPUT_DIR / scope
    candidates_by_formula: dict[str, dict] = {}
    for iter_dir in sorted(scope_root.glob("iter_*")):
        final_score = _safe_read_csv(iter_dir / "backtest" / "final_score.csv")
        if final_score.empty or "factor_name" not in final_score.columns:
            continue
        validated = read_json(iter_dir / "llm" / "factors_validated.json").get("factors", [])
        validated_by_name = {str(item["factor_name"]): item for item in validated}
        for row in final_score.to_dict(orient="records"):
            factor_name = str(row.get("factor_name", ""))
            detail = validated_by_name.get(factor_name)
            if not detail:
                continue
            formula = str(detail.get("formula", "")).strip()
            if not formula:
                continue
            candidate = dict(detail)
            candidate["discovery_total_score"] = float(row.get("total_score", 0.0) or 0.0)
            candidate["discovery_iteration"] = iter_dir.name
            existing = candidates_by_formula.get(formula)
            if existing is None or candidate["discovery_total_score"] > existing["discovery_total_score"]:
                candidates_by_formula[formula] = candidate
    candidates = sorted(
        candidates_by_formula.values(),
        key=lambda item: float(item.get("discovery_total_score", 0.0)),
        reverse=True,
    )
    if not candidates:
        print(f"  警告: discovery 阶段没有任何通过回测筛选的候选因子，跳过该窗口的 validation")
    return candidates


def _run_validation(window: dict[str, str], candidates: list[dict]) -> list[dict]:
    log_phase(f"{window['window_id']} - validation")
    set_runtime_context(_stage_context(window, "validation"))
    write_json(
        OUTPUT_DIR / "llm" / "factors_validated.json",
        {
            "factors": candidates,
            "source": {
                "stage": "discovery",
                "window_id": window["window_id"],
                "candidate_count": len(candidates),
            },
        },
    )
    run_step07()
    run_step08()
    run_step09()
    archive_outputs_bundle(OUTPUT_DIR / "train_windows" / window["window_id"] / "validation")

    final_score = _safe_read_csv(OUTPUT_DIR / "backtest" / "final_score.csv")
    factor_metrics = _safe_read_csv(OUTPUT_DIR / "backtest" / "factor_metrics.csv")
    strategy_metrics = _safe_read_csv(OUTPUT_DIR / "backtest" / "strategy_metrics.csv")
    if final_score.empty or "factor_name" not in final_score.columns:
        return []

    metrics = final_score.copy()
    if not factor_metrics.empty and "factor_name" in factor_metrics.columns:
        metrics = metrics.merge(
            factor_metrics[["factor_name", "mean_rank_ic", "rank_ic_ir", "positive_ic_ratio"]],
            on="factor_name",
            how="left",
        )
    if not strategy_metrics.empty and "factor_name" in strategy_metrics.columns:
        metrics = metrics.merge(
            strategy_metrics[["factor_name", "annualized_return", "max_drawdown", "sharpe", "turnover"]],
            on="factor_name",
            how="left",
        )
    candidate_by_name = {str(item["factor_name"]): item for item in candidates}
    rows: list[dict] = []
    for row in metrics.to_dict(orient="records"):
        factor_name = str(row.get("factor_name", ""))
        candidate = candidate_by_name.get(factor_name)
        if not candidate:
            continue
        rows.append(
            {
                "window_id": window["window_id"],
                "factor_name": factor_name,
                "formula": candidate.get("formula", ""),
                "llm_direction": candidate.get("llm_direction", ""),
                "reason": candidate.get("reason", ""),
                "risk": candidate.get("risk", ""),
                "discovery_total_score": float(candidate.get("discovery_total_score", 0.0) or 0.0),
                "validation_total_score": _metric_value(row, "total_score"),
                "annualized_return": _metric_value(row, "annualized_return", "annualized_return_x", "annualized_return_y"),
                "mean_rank_ic": _metric_value(row, "mean_rank_ic", "mean_rank_ic_x", "mean_rank_ic_y"),
                "rank_ic_ir": _metric_value(row, "rank_ic_ir", "rank_ic_ir_x", "rank_ic_ir_y"),
                "positive_ic_ratio": _metric_value(row, "positive_ic_ratio", "positive_ic_ratio_x", "positive_ic_ratio_y"),
                "max_drawdown": _metric_value(row, "max_drawdown", "max_drawdown_x", "max_drawdown_y"),
                "sharpe": _metric_value(row, "sharpe", "sharpe_x", "sharpe_y"),
                "turnover": _metric_value(row, "turnover", "turnover_x", "turnover_y"),
            }
        )
    return rows


def _aggregate_cross_window_results(validation_rows: list[dict], total_windows: int) -> pd.DataFrame:
    if not validation_rows:
        return pd.DataFrame()
    frame = pd.DataFrame(validation_rows)
    aggregated = (
        frame.groupby("formula", as_index=False)
        .agg(
            factor_name=("factor_name", "first"),
            llm_direction=("llm_direction", "first"),
            reason=("reason", "first"),
            risk=("risk", "first"),
            windows_passed=("window_id", "nunique"),
            mean_discovery_total_score=("discovery_total_score", "mean"),
            mean_validation_total_score=("validation_total_score", "mean"),
            min_validation_total_score=("validation_total_score", "min"),
            mean_annualized_return=("annualized_return", "mean"),
            mean_rank_ic=("mean_rank_ic", "mean"),
            mean_rank_ic_ir=("rank_ic_ir", "mean"),
            mean_positive_ic_ratio=("positive_ic_ratio", "mean"),
            mean_max_drawdown=("max_drawdown", "mean"),
            mean_sharpe=("sharpe", "mean"),
            mean_turnover=("turnover", "mean"),
        )
    )
    aggregated["window_pass_ratio"] = aggregated["windows_passed"] / max(total_windows, 1)
    aggregated = aggregated.sort_values(
        ["window_pass_ratio", "mean_validation_total_score", "mean_annualized_return"],
        ascending=[False, False, False],
    )
    return aggregated


def _write_cross_window_outputs(validation_rows: list[dict], windows: list[dict[str, str]]) -> None:
    detail_frame = pd.DataFrame(validation_rows)
    detail_frame.to_csv(OUTPUT_DIR / "backtest" / "cross_window_validation_details.csv", index=False)
    aggregated = _aggregate_cross_window_results(validation_rows, len(windows))
    aggregated.to_csv(OUTPUT_DIR / "backtest" / "cross_window_factor_ranking.csv", index=False)
    top3 = aggregated.head(3).to_dict(orient="records") if not aggregated.empty else []
    write_json(
        OUTPUT_DIR / "backtest" / "cross_window_summary.json",
        {
            "window_count": len(windows),
            "windows": windows,
            "validation_row_count": len(validation_rows),
            "aggregated_factor_count": int(len(aggregated)),
            "top3": top3,
        },
    )
    write_json(OUTPUT_DIR / "backtest" / "cross_window_top3_factors.json", {"top3": top3})


def run() -> None:
    ensure_runtime_dirs()
    clear_runtime_context()
    config = env_config()
    analysis_config = analysis_rule_config()
    if analysis_config.get("run_mode") != "train":
        print("================================================================")
        print("警告: 当前 analysis_rule.yaml 中的 run_mode 不是 'train'！")
        print("请先将 analysis_rule.yaml 中的 run_mode 修改为 'train'，然后再运行此脚本。")
        print("================================================================")
        return

    log_step_start("10", "因子挖掘迭代流程 (train 模式)")

    run_step00()
    run_step01()
    windows = build_training_windows(analysis_config)
    iterations = int(analysis_config.get("iteration_count", 3))
    write_json(OUTPUT_DIR / "backtest" / "training_windows.json", {"windows": windows})
    print(f"  训练窗口: {len(windows)} 个, 每窗口迭代: {iterations} 次")

    validation_rows: list[dict] = []
    try:
        for window in windows:
            log_phase(
                f"{window['window_id']} - discovery window "
                f"({window['discovery_start_date']} ~ {window['discovery_end_date']})"
            )
            # 在新的训练窗口开始前，做一次全面的环境清理（因为上个窗口的验证阶段会产生新数据）
            clean_outputs(dry_run=False)
            discovery_scope = f"train_windows/{window['window_id']}/discovery"
            
            # 在一个窗口的 discovery 阶段开始时，准备好一次性的上下文和基础数据。
            # Step02/03 属于窗口级动作，不属于某一轮 iteration。
            set_runtime_context(_stage_context(window, "discovery"))
            run_step02()
            run_step03()
            
            for iteration in range(1, iterations + 1):
                # 每次迭代前，只清理会被本轮重建的临时文件。
                # 保留 health 目录（窗口级一次性产物）以及上一轮回测反馈文件，
                # 这样 Step04/05 仍能读取 previous_round_top_factors / skipped_factors。
                clean_outputs(
                    dry_run=False,
                    preserve_train_windows=True,
                    preserve_health=True,
                    preserve_backtest_feedback=True,
                )
                
                # 更新当前迭代轮数的上下文
                set_runtime_context(_stage_context(window, "discovery", iteration))
                
                # 开始执行核心迭代流程（04~09）
                _run_discovery_iteration(iteration, iterations, window, discovery_scope)
            candidates = _collect_discovery_passed_factors(discovery_scope)
            if not candidates:
                print(f"  {window['window_id']} 跳过 validation（无候选因子）")
                write_json(
                    OUTPUT_DIR / "train_windows" / window["window_id"] / "window_summary.json",
                    {
                        "window": window,
                        "discovery_candidate_count": 0,
                        "validation_passed_count": 0,
                        "skipped_reason": "discovery 阶段无任何因子通过预筛",
                    },
                )
                continue
            validation_result = _run_validation(window, candidates)
            validation_rows.extend(validation_result)
            write_json(
                OUTPUT_DIR / "train_windows" / window["window_id"] / "window_summary.json",
                {
                    "window": window,
                    "discovery_candidate_count": len(candidates),
                    "validation_passed_count": len(validation_result),
                },
            )
            print(
                f"  {window['window_id']} validation ok, "
                f"candidates={len(candidates)}, passed={len(validation_result)}"
            )
    finally:
        clear_runtime_context()
    _write_cross_window_outputs(validation_rows, windows)
    aggregated = _aggregate_cross_window_results(validation_rows, len(windows))
    log_step_end(
        "10",
        "因子挖掘迭代完成",
        details=[
            f"训练窗口: {len(windows)} 个",
            f"通过验证因子: {len(validation_rows)} 个",
            f"聚合因子: {len(aggregated)} 个",
        ],
    )

if __name__ == "__main__":
    run()
