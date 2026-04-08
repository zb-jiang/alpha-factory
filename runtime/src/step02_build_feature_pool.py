from __future__ import annotations

from common import OUTPUT_DIR, feature_pool_config, write_json


def run() -> None:
    feature_cfg = feature_pool_config()
    payload = {
        "raw_fields": feature_cfg.get("raw_fields", []),
        "base_features": feature_cfg.get("base_features", []),
        "allowed_operators": feature_cfg.get("allowed_operators", []),
        "base_feature_names": [item["name"] for item in feature_cfg.get("base_features", [])],
    }
    write_json(OUTPUT_DIR / "health" / "feature_pool_manifest.json", payload)
    print(f"feature pool ready, features={len(payload['base_feature_names'])}")


if __name__ == "__main__":
    run()
