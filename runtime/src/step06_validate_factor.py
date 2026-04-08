from __future__ import annotations

from typing import Any, List, Dict

from pydantic import BaseModel, Field, ValidationError

from common import (
    OUTPUT_DIR,
    backtest_rule_config,
    feature_pool_config,
    parse_json_text,
    read_json,
    validate_formula,
    write_json,
)


class FactorCandidate(BaseModel):
    factor_name: str
    formula: str
    fields: List[str] = Field(default_factory=list)
    direction: str
    reason: str
    risk: str
    backtest_rule: Dict[str, Any]


class FactorPayload(BaseModel):
    factors: List[FactorCandidate]


def run() -> None:
    raw = read_json(OUTPUT_DIR / "llm" / "raw_response.json")
    payload = parse_json_text(str(raw.get("content", "")))
    factor_payload = FactorPayload.model_validate(payload)
    feature_cfg = feature_pool_config()
    allowed_features = {item["name"] for item in feature_cfg.get("base_features", [])}
    allowed_operators = set(feature_cfg.get("allowed_operators", []))
    fixed_rule = backtest_rule_config()
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in factor_payload.factors:
        try:
            if candidate.direction not in {"higher_better", "lower_better"}:
                raise ValueError("direction 只能是 higher_better 或 lower_better")
            if set(candidate.fields) - allowed_features:
                raise ValueError("fields 中包含未授权特征")
            ok, reason = validate_formula(candidate.formula, allowed_features, allowed_operators)
            if not ok:
                raise ValueError(reason)
            if candidate.backtest_rule != fixed_rule:
                raise ValueError("backtest_rule 与固定交易规则不一致")
            accepted.append(candidate.model_dump())
        except Exception as exc:
            rejected.append(
                {
                    "factor_name": candidate.factor_name,
                    "formula": candidate.formula,
                    "reason": str(exc),
                }
            )
    write_json(OUTPUT_DIR / "llm" / "factors_validated.json", {"factors": accepted})
    write_json(OUTPUT_DIR / "llm" / "factors_rejected.json", {"factors": rejected})
    print(f"validated={len(accepted)}, rejected={len(rejected)}")


if __name__ == "__main__":
    try:
        run()
    except ValidationError as exc:
        write_json(
            OUTPUT_DIR / "llm" / "factors_rejected.json",
            {"factors": [{"factor_name": "payload", "formula": "", "reason": str(exc)}]},
        )
        raise
