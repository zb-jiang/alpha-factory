from __future__ import annotations

from pathlib import Path

import pandas as pd

from common import (
    OUTPUT_DIR,
    analysis_profile,
    apply_factor_preprocess,
    build_label_series,
    build_feature_frame,
    estimate_label_forward_days,
    estimate_required_warmup,
    env_config,
    evaluate_formula,
    factor_metrics_from_series,
    feature_pool_config,
    label_signature,
    load_raw_data,
    preprocess_signature,
    read_json,
    write_table,
)


def _build_factor_value_chunk(
    factor_name: str,
    raw_score: pd.Series,
    score: pd.Series,
) -> pd.DataFrame:
    frame = pd.concat([raw_score.rename("raw_score"), score.rename("score")], axis=1).reset_index()
    frame.insert(0, "factor_name", factor_name)
    frame["raw_score"] = pd.to_numeric(frame["raw_score"], errors="coerce").astype("float32")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce").astype("float32")
    return frame


class FactorValueParquetWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()

    def write(self, chunk: pd.DataFrame) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(chunk, preserve_index=False)
        if self._writer is None:
            self._writer = pq.ParquetWriter(str(self.path), table.schema, compression="snappy")
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()

def run() -> None:
    config = env_config()
    feature_cfg = feature_pool_config()
    validated = read_json(OUTPUT_DIR / "llm" / "factors_validated.json").get("factors", [])
    formulas = [str(item.get("formula", "")) for item in validated if item.get("formula")]
    warmup_days = estimate_required_warmup(feature_cfg, formulas)
    forward_days = estimate_label_forward_days(config)
    raw_frame = load_raw_data(
        config,
        list(feature_cfg.get("raw_fields", [])),
        warmup_trading_days=warmup_days,
        forward_trading_days=forward_days,
    )
    feature_frame = build_feature_frame(raw_frame, config, feature_cfg)
    metric_rows: list[dict[str, object]] = []
    factor_input = feature_frame[[item["name"] for item in feature_cfg.get("base_features", [])]]
    label_series = build_label_series(raw_frame, config)
    active_analysis_profile = analysis_profile(config)
    active_label_mode = label_signature(config)
    active_preprocess = preprocess_signature(config)
    factor_writer = FactorValueParquetWriter(OUTPUT_DIR / "backtest" / "factor_values.parquet")
    wrote_factor_values = False
    total = len(validated)
    try:
        for i, item in enumerate(validated, start=1):
            factor_name = str(item["factor_name"])
            print(f"  [{i}/{total}] {factor_name}: evaluating...", flush=True)
            raw_score = evaluate_formula(str(item["formula"]), factor_input)
            score = apply_factor_preprocess(raw_score, raw_frame, config)
            print(f"  [{i}/{total}] {factor_name}: computing IC/IR metrics...", flush=True)
            metrics = factor_metrics_from_series(factor_name, score, label_series, config)
            metrics["llm_direction"] = str(item["llm_direction"])
            metrics["empirical_direction"] = "higher_better" if metrics["mean_rank_ic"] >= 0 else "lower_better"
            metrics["analysis_profile"] = active_analysis_profile
            metrics["label_mode"] = active_label_mode
            metrics["preprocess_signature"] = active_preprocess
            factor_writer.write(_build_factor_value_chunk(factor_name, raw_score, score))
            wrote_factor_values = True
            metric_rows.append(metrics)
            rank_ic = metrics.get("mean_rank_ic", 0.0)
            rank_ir = metrics.get("rank_ic_ir", 0.0)
            print(f"  [{i}/{total}] {factor_name}: rank_ic={rank_ic:.4f}, rank_ir={rank_ir:.4f}", flush=True)
    finally:
        factor_writer.close()
    if not wrote_factor_values:
        raise RuntimeError("没有可评估的合法因子")
    metrics = pd.DataFrame(metric_rows).set_index("factor_name").sort_index()
    write_table(OUTPUT_DIR / "backtest" / "factor_metrics.csv", metrics)
    print(f"factor evaluation ok, factors={len(metrics)}")


if __name__ == "__main__":
    run()
