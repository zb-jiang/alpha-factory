from __future__ import annotations

from copy import deepcopy

import pandas as pd

from common import (
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
        codes = list_instruments(mode_cfg)
        print(f"{mode} 模式股票池: {len(codes)} 只")
        code_set.update(codes)
    result = sorted(code_set)
    print(f"训练+测试并集股票池: {len(result)} 只")
    return result


def run() -> None:
    cfg = env_config()
    data_source = str(cfg.get("data_source", "qlib")).strip().lower()
    if data_source != "tushare":
        print(f"当前 data_source={data_source}，非 tushare，跳过预缓存。")
        return

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
