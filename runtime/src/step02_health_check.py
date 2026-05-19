from __future__ import annotations

from common import (
    OUTPUT_DIR,
    build_feature_frame,
    build_summary_payload,
    compute_feature_corr,
    compute_feature_stats,
    env_config,
    feature_pool_config,
    label_name,
    load_raw_data,
    log_step_end,
    log_step_start,
    write_json,
    write_table,
)


def run() -> None:
    log_step_start("02", "特征健康检查")
    config = env_config()
    feature_cfg = feature_pool_config()
    raw_frame = load_raw_data(config, list(feature_cfg.get("raw_fields", [])))
    feature_frame = build_feature_frame(raw_frame, config, feature_cfg)
    label_column = label_name(config)
    stats = compute_feature_stats(feature_frame, label_column)
    corr = compute_feature_corr(feature_frame, label_column)
    summary = build_summary_payload(stats, corr, label_column)
    write_table(OUTPUT_DIR / "health" / "feature_stats.csv", stats)
    write_table(OUTPUT_DIR / "health" / "feature_corr.csv", corr)
    write_json(OUTPUT_DIR / "health" / "health_summary.json", summary)
    log_step_end(
        "02",
        "健康检查完成",
        details=[
            f"数据行数: {len(feature_frame):,} (股票×交易日)",
            f"特征数: {len(stats)} (含基础特征+衍生特征)",
        ],
    )


if __name__ == "__main__":
    run()
