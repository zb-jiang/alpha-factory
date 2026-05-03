from __future__ import annotations

from pathlib import Path
from typing import Any
import warnings

import numpy as np
import pandas as pd
import shutil

from common import (
    OUTPUT_DIR,
    analysis_observation_dates,
    env_config,
    estimate_label_forward_days,
    label_config,
    load_raw_data,
    read_json,
    write_json,
    write_table,
)

try:
    import alphalens as al
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError(
        "缺少依赖 alphalens，请先安装兼容版本: python -m pip install alphalens-reloaded"
    ) from exc

try:
    from scipy.stats import ConstantInputWarning
except Exception:  # pragma: no cover
    ConstantInputWarning = RuntimeWarning  # type: ignore[assignment]


def _configure_runtime_warning_filters() -> None:
    # 仅屏蔽第三方库已知兼容性 warning，保留业务异常和关键日志。
    warnings.filterwarnings(
        "ignore",
        message=r".*default fill_method='pad'.*pct_change.*",
        category=FutureWarning,
        module=r"alphalens\..*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*default of observed=False is deprecated.*",
        category=FutureWarning,
        module=r"alphalens\..*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*DataFrameGroupBy\.apply operated on the grouping columns.*",
        category=FutureWarning,
        module=r"alphalens\..*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*correlation coefficient is not defined.*",
        category=ConstantInputWarning,
        module=r"alphalens\..*",
    )


def _load_top3_factor_names() -> list[str]:
    payload = read_json(OUTPUT_DIR / "backtest" / "top3_factors.json")
    top3 = payload.get("top3", []) if isinstance(payload, dict) else []
    names: list[str] = []
    for item in top3:
        factor_name = str(item.get("factor_name", "")).strip()
        if factor_name and factor_name not in names:
            names.append(factor_name)
    return names


def _load_factor_values() -> pd.DataFrame:
    path = OUTPUT_DIR / "backtest" / "factor_values.parquet"
    if not path.exists():
        raise FileNotFoundError(f"缺少文件: {path}")
    factor_values = pd.read_parquet(path)
    index_columns = ["factor_name", "instrument", "datetime"]
    if set(index_columns).issubset(factor_values.columns):
        factor_values = factor_values.copy()
        factor_values["datetime"] = pd.to_datetime(factor_values["datetime"])
        return factor_values.set_index(index_columns).sort_index()
    return factor_values.reset_index().set_index(index_columns).sort_index()


def _extract_factor_series(factor_values: pd.DataFrame, factor_name: str, cfg: dict[str, Any]) -> pd.Series:
    factor_frame = factor_values.xs(factor_name, level="factor_name")
    score_column = "score" if "score" in factor_frame.columns else "raw_score"
    if score_column not in factor_frame.columns:
        raise KeyError(f"{factor_name} 不存在可用列 score/raw_score")
    series = pd.to_numeric(factor_frame[score_column], errors="coerce")
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    observation_dates = pd.Index(
        pd.to_datetime(analysis_observation_dates(series.index.get_level_values("datetime"), cfg))
    )
    series = series.loc[series.index.get_level_values("datetime").isin(observation_dates)]
    series = series.reorder_levels(["datetime", "instrument"]).sort_index()
    series.index = series.index.set_names(["date", "asset"])
    return series.rename("factor")


def _build_prices_and_groups(
    cfg: dict[str, Any],
    factor_series: pd.Series,
    horizon_days: int,
) -> tuple[pd.DataFrame, pd.Series]:
    price_field = str(label_config(cfg).get("price_field", "close")).strip()
    raw_fields = [price_field, "industry"]
    raw_frame = load_raw_data(cfg, raw_fields=raw_fields, forward_trading_days=horizon_days)

    prices = (
        raw_frame[price_field]
        .reset_index()
        .pivot(index="datetime", columns="instrument", values=price_field)
        .sort_index()
    )
    prices.index = pd.to_datetime(prices.index)

    factor_min_date = pd.Timestamp(factor_series.index.get_level_values("date").min()).normalize()
    prices = prices.loc[prices.index >= factor_min_date]

    industry = raw_frame["industry"].copy()
    align_index = pd.MultiIndex.from_arrays(
        [
            factor_series.index.get_level_values("asset"),
            factor_series.index.get_level_values("date"),
        ],
        names=["instrument", "datetime"],
    )
    aligned = industry.reindex(align_index)
    groups = pd.Series(aligned.values, index=factor_series.index, name="group")
    groups = groups.fillna("UNKNOWN").astype(str)
    return prices, groups


def _period_column(factor_data: pd.DataFrame) -> str:
    candidates = [str(col) for col in factor_data.columns if str(col) not in {"factor", "group", "factor_quantile"}]
    if not candidates:
        raise RuntimeError("Alphalens 输出中缺少 forward return 列")
    return candidates[0]


def _run_single_factor(
    factor_name: str,
    factor_values: pd.DataFrame,
    cfg: dict[str, Any],
    horizon_days: int,
    quantiles: int,
    output_dir: Path,
    step11_metric_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    factor_series = _extract_factor_series(factor_values, factor_name, cfg)
    if factor_series.empty:
        raise RuntimeError("因子观测值为空")
    prices, groups = _build_prices_and_groups(cfg, factor_series, horizon_days)
    factor_data = al.utils.get_clean_factor_and_forward_returns(
        factor=factor_series,
        prices=prices,
        groupby=groups,
        quantiles=quantiles,
        periods=[horizon_days],
        max_loss=0.99,
    )
    period_col = _period_column(factor_data)

    ic_series = al.performance.factor_information_coefficient(factor_data)
    mean_ret_by_q, _ = al.performance.mean_return_by_quantile(
        factor_data,
        by_date=False,
        by_group=False,
    )
    mean_ret_by_q_group, _ = al.performance.mean_return_by_quantile(
        factor_data,
        by_date=False,
        by_group=True,
    )
    mean_ic_by_group = al.performance.mean_information_coefficient(factor_data, by_group=True)
    max_q = int(factor_data["factor_quantile"].max())
    min_q = int(factor_data["factor_quantile"].min())
    q_spread = float(mean_ret_by_q.loc[max_q, period_col] - mean_ret_by_q.loc[min_q, period_col])
    q_turnover_high = al.performance.quantile_turnover(factor_data["factor_quantile"], quantile=max_q, period=1)
    q_turnover_low = al.performance.quantile_turnover(factor_data["factor_quantile"], quantile=min_q, period=1)

    factor_output = output_dir / factor_name
    factor_output.mkdir(parents=True, exist_ok=True)
    clean_export = factor_data.reset_index().rename(columns={"date": "datetime", "asset": "instrument"})
    write_table(factor_output / "clean_factor_data.parquet", clean_export)
    write_table(factor_output / "ic_series.csv", ic_series)
    write_table(factor_output / "mean_return_by_quantile.csv", mean_ret_by_q)
    write_table(factor_output / "mean_return_by_quantile_by_group.csv", mean_ret_by_q_group)
    write_table(factor_output / "mean_ic_by_group.csv", mean_ic_by_group)

    alphalens_observation_count = int(factor_data.index.get_level_values("date").nunique())
    # Alphalens IC 为秩相关（RankIC）口径
    alphalens_rank_ic = float(ic_series[period_col].mean())
    alphalens_rank_ic_std = float(ic_series[period_col].std(ddof=0) or 0.0)
    alphalens_rank_ic_ir = float(alphalens_rank_ic / alphalens_rank_ic_std) if alphalens_rank_ic_std else 0.0
    step11_observation_count = (
        int(step11_metric_row.get("observation_count", 0)) if step11_metric_row else None
    )
    step11_rank_ic = (
        float(step11_metric_row.get("mean_rank_ic", 0.0)) if step11_metric_row else None
    )
    step11_rank_ic_ir = (
        float(step11_metric_row.get("rank_ic_ir", 0.0)) if step11_metric_row else None
    )

    summary = {
        "factor_name": factor_name,
        "horizon_trading_days": int(horizon_days),
        "period_column": period_col,
        # 与 step11/step07 口径保持一致：看板核心指标优先展示 factor_metrics.csv
        "observation_count": step11_observation_count
        if step11_observation_count is not None
        else alphalens_observation_count,
        "sample_count": int(len(factor_data)),
        # 核心 IC 指标统一使用 RankIC 口径
        "rank_ic": step11_rank_ic if step11_rank_ic is not None else alphalens_rank_ic,
        "rank_ic_ir": step11_rank_ic_ir if step11_rank_ic_ir is not None else alphalens_rank_ic_ir,
        "observation_count_step11": step11_observation_count,
        "rank_ic_step11": step11_rank_ic,
        "rank_ic_ir_step11": step11_rank_ic_ir,
        "observation_count_alphalens": alphalens_observation_count,
        "rank_ic_alphalens": alphalens_rank_ic,
        "rank_ic_ir_alphalens": alphalens_rank_ic_ir,
        # 兼容旧字段：保留 mean_ic，并映射到 RankIC
        "mean_ic": step11_rank_ic if step11_rank_ic is not None else alphalens_rank_ic,
        "mean_ic_step11": step11_rank_ic,
        "mean_ic_alphalens": alphalens_rank_ic,
        "ic_std": float(ic_series[period_col].std(ddof=0)),
        "mean_return_qmax": float(mean_ret_by_q.loc[max_q, period_col]),
        "mean_return_qmin": float(mean_ret_by_q.loc[min_q, period_col]),
        "mean_return_spread_qmax_qmin": q_spread,
        "mean_turnover_qmax": float(q_turnover_high.mean()),
        "mean_turnover_qmin": float(q_turnover_low.mean()),
        "group_dimension": "industry",
    }
    write_json(factor_output / "summary.json", summary)
    return summary


def run() -> None:
    _configure_runtime_warning_filters()
    cfg = env_config()
    run_mode = str(cfg.get("run_mode", "train")).strip().lower()
    if run_mode != "test":
        raise ValueError("step12_alphalens_report 仅支持 run_mode=test")

    factor_names = _load_top3_factor_names()
    if not factor_names:
        raise RuntimeError("top3_factors.json 为空，请先执行 step11_oos_test.py")

    factor_values = _load_factor_values()
    factor_metrics_path = OUTPUT_DIR / "backtest" / "factor_metrics.csv"
    step11_metrics_by_factor: dict[str, dict[str, Any]] = {}
    if factor_metrics_path.exists():
        fm = pd.read_csv(factor_metrics_path)
        if "factor_name" in fm.columns:
            step11_metrics_by_factor = {
                str(row["factor_name"]): row.to_dict()
                for _, row in fm.iterrows()
            }
    horizon_days = max(int(estimate_label_forward_days(cfg) or 1), 1)
    quantiles = 5
    output_dir = OUTPUT_DIR / "alphalens"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for factor_name in factor_names:
        try:
            print(f"[step12] 开始分析: {factor_name}")
            summary = _run_single_factor(
                factor_name=factor_name,
                factor_values=factor_values,
                cfg=cfg,
                horizon_days=horizon_days,
                quantiles=quantiles,
                output_dir=output_dir,
                step11_metric_row=step11_metrics_by_factor.get(factor_name),
            )
            summaries.append(summary)
            print(f"[step12] 分析完成: {factor_name}")
        except Exception as exc:
            skipped.append({"factor_name": factor_name, "reason": str(exc)})
            print(f"[step12] 跳过: {factor_name}, reason={exc}")

    if not summaries:
        raise RuntimeError("所有 Top3 因子都未成功生成 Alphalens 报告")

    summary_df = pd.DataFrame(summaries).set_index("factor_name")
    write_table(output_dir / "summary.csv", summary_df)
    write_json(
        output_dir / "summary.json",
        {
            "scope": "step11_oos_top3_only",
            "window": "test_only",
            "grouped_analysis": "industry_only",
            "horizon_trading_days": int(horizon_days),
            "factors_analyzed": summaries,
            "skipped_factors": skipped,
        },
    )
    print(f"[step12] 报告完成: analyzed={len(summaries)}, skipped={len(skipped)}, output={output_dir}")


if __name__ == "__main__":
    run()
