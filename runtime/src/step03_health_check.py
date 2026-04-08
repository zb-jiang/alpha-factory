from __future__ import annotations

from common import (
    OUTPUT_DIR,
    build_feature_frame,
    build_summary_payload,
    compute_feature_corr,
    compute_feature_stats,
    env_config,
    feature_pool_config,
    load_raw_data,
    write_json,
    write_table,
)


def run() -> None:
    config = env_config()
    feature_cfg = feature_pool_config()
    raw_frame = load_raw_data(config, list(feature_cfg.get("raw_fields", [])))
    feature_frame = build_feature_frame(raw_frame, config, feature_cfg)
    label_name = str(config.get("target_label", "future_5d_return"))
    stats = compute_feature_stats(feature_frame, label_name)
    corr = compute_feature_corr(feature_frame, label_name)
    summary = build_summary_payload(stats, corr, label_name)
    write_table(OUTPUT_DIR / "health" / "feature_stats.csv", stats)
    write_table(OUTPUT_DIR / "health" / "feature_corr.csv", corr)
    write_json(OUTPUT_DIR / "health" / "health_summary.json", summary)
    print(f"health check ok, rows={len(feature_frame)}, features={len(stats)}")


if __name__ == "__main__":
    run()
