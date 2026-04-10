from __future__ import annotations

import json
from pathlib import Path

from common import OUTPUT_DIR, feature_pool_config, write_json


def run() -> None:
    feature_cfg = feature_pool_config()
    
    # 构建特征池清单
    manifest = {
        "raw_fields": feature_cfg.get("raw_fields", []),
        "base_features": feature_cfg.get("base_features", []),
        "allowed_operators": feature_cfg.get("allowed_operators", []),
        "metadata": {
            "description": "Qlib 因子挖掘系统特征池配置",
            "version": "1.0",
            "total_base_features": len(feature_cfg.get("base_features", [])),
            "total_operators": len(feature_cfg.get("allowed_operators", []))
        }
    }
    
    write_json(OUTPUT_DIR / "health" / "feature_pool_manifest.json", manifest)
    print(f"feature pool built: {len(manifest['base_features'])} features, {len(manifest['allowed_operators'])} operators")


if __name__ == "__main__":
    run()
