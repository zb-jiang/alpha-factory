"""
Step 01: 预缓存 Tushare 核心数据
"""

from __future__ import annotations

from copy import deepcopy

import pandas as pd

from common import (
    _dynamic_index_components_by_observation_date,
    _dynamic_index_pool_enabled,
    _observation_dates_from_run_window,
    env_config,
    estimate_label_forward_days,
    estimate_required_warmup,
    feature_pool_config,
    get_data_provider,
    list_instruments,
)


def _collect_time_span(cfg: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    windows: list[tuple[pd.Timestamp, pd.Timestamp, str]] = []
    for scope in ("train", "test"):
        start_key = f"{scope}_start_date"
        end_key = f"{scope}_end_date"
        if cfg.get(start_key) and cfg.get(end_key):
            start = pd.Timestamp(str(cfg[start_key])).normalize()
            end = pd.Timestamp(str(cfg[end_key])).normalize()
            windows.append((start, end, scope))
    if not windows:
        raise ValueError("analysis_rule.yaml 未配置 train/test 时间区间，无法预缓存")
    start = min(item[0] for item in windows)
    end = max(item[1] for item in windows)
    return start, end


def _collect_union_instruments(cfg: dict) -> list[str]:
    code_set: set[str] = set()
    for mode in ("train", "test"):
        start_key = f"{mode}_start_date"
        end_key = f"{mode}_end_date"
        if not cfg.get(start_key) or not cfg.get(end_key):
            continue
        mode_cfg = deepcopy(cfg)
        mode_cfg["run_mode"] = mode
        if _dynamic_index_pool_enabled(mode_cfg):
            observation_dates = _observation_dates_from_run_window(mode_cfg)
            component_map = _dynamic_index_components_by_observation_date(mode_cfg, observation_dates)
            codes = sorted({code for items in component_map.values() for code in items})
            print(
                f"{mode} 模式动态股票池: 观测日={len(observation_dates)}，"
                f"并集股票数={len(codes)}"
            )
        else:
            codes = list_instruments(mode_cfg)
        print(f"{mode} 模式股票池: {len(codes)} 只")
        code_set.update(codes)
    result = sorted(code_set)
    print(f"训练+测试并集股票池: {len(result)} 只")
    return result


def _audit_remaining_gaps(
    provider,
    instruments: list[str],
    start_ymd: str,
    end_ymd: str,
) -> None:
    # 先统一同步一次交易日历，避免在每只股票审计时重复回源。
    if hasattr(provider, "_sync_trade_cal"):
        provider._sync_trade_cal(start_ymd, end_ymd)  # type: ignore[attr-defined]

    api_names = ("daily", "adj_factor", "daily_basic")
    summary: list[str] = []
    for api_name in api_names:
        missing_code_count = 0
        missing_range_count = 0
        total = len(instruments)
        for i, qlib_code in enumerate(instruments, 1):
            ts_code = provider._convert_to_ts_code(qlib_code)  # type: ignore[attr-defined]
            ranges = provider._get_unfetched_ranges(api_name, ts_code, start_ymd, end_ymd)  # type: ignore[attr-defined]
            if not ranges:
                if i % 100 == 0 or i == total:
                    print(f"  审计 {api_name} 进度: {i}/{total}")
                continue
            missing_code_count += 1
            missing_range_count += len(ranges)
            if i % 100 == 0 or i == total:
                print(f"  审计 {api_name} 进度: {i}/{total}")
        summary.append(
            f"{api_name}: missing_codes={missing_code_count}, missing_ranges={missing_range_count}"
        )
    print("预缓存缺口审计:")
    for line in summary:
        print(f"  {line}")


def run() -> None:
    cfg = env_config()
    data_source = str(cfg.get("data_source", "tushare")).strip().lower()
    if data_source != "tushare":
        raise ValueError(f"当前仅支持 data_source=tushare，收到: {data_source}")

    feature_cfg = feature_pool_config()
    warmup_days = estimate_required_warmup(feature_cfg, formulas=[])
    forward_days = estimate_label_forward_days(cfg)

    start, end = _collect_time_span(cfg)
    buffered_start = (start - pd.offsets.BDay(warmup_days + 5)).normalize()
    buffered_end = (end + pd.offsets.BDay(forward_days + 5)).normalize()

    instruments = _collect_union_instruments(cfg)
    if not instruments:
        raise RuntimeError("股票池为空，无法执行 Tushare 预缓存")

    provider = get_data_provider(cfg)
    provider.initialize()

    start_ymd = buffered_start.strftime("%Y%m%d")
    end_ymd = buffered_end.strftime("%Y%m%d")

    print("开始预缓存 Tushare 核心日频数据...")
    print(
        f"  时间范围(含缓冲): {buffered_start.strftime('%Y-%m-%d')} ~ {buffered_end.strftime('%Y-%m-%d')}, "
        f"warmup={warmup_days}, forward={forward_days}"
    )
    print(f"  股票数量: {len(instruments)}")

    for api_name in ("daily", "adj_factor", "daily_basic"):
        print(f"预缓存 {api_name} ...")
        provider._ensure_data_cached(api_name, instruments, start_ymd, end_ymd)

    _audit_remaining_gaps(provider, instruments, start_ymd, end_ymd)

    # 触发静态字段缓存，避免后续步骤第一次运行时再等待。
    provider.get_features(
        instruments=instruments,
        fields=["$industry"],
        start_date=buffered_start.strftime("%Y-%m-%d"),
        end_date=buffered_end.strftime("%Y-%m-%d"),
        freq=str(cfg.get("freq", "day")),
    )

    print("Tushare 预缓存完成。现在可直接运行 step10_iterate.py / step11_oos_test.py。")


if __name__ == "__main__":
    run()
