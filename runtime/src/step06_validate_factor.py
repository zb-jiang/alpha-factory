from __future__ import annotations

from typing import Any, List, Dict

from pydantic import BaseModel, Field, ValidationError

from common import (
    OUTPUT_DIR,
    feature_pool_config,
    formula_feature_names,
    generation_constraints,
    log_step_end,
    log_step_start,
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
    expected_failure_regime: str = ""
    backtest_rule: Dict[str, Any] = Field(default_factory=dict)


class FactorPayload(BaseModel):
    factors: List[FactorCandidate]


def run() -> None:
    log_step_start("06", "因子公式验证")
    raw = read_json(OUTPUT_DIR / "llm" / "raw_response.json")
    payload = parse_json_text(str(raw.get("content", "")))
    factor_payload = FactorPayload.model_validate(payload)
    feature_cfg = feature_pool_config()
    allowed_features = {item["name"] for item in feature_cfg.get("base_features", [])}
    allowed_operators = {item["name"] for item in feature_cfg.get("allowed_operators", [])}
    constraints = generation_constraints(feature_cfg)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in factor_payload.factors:
        try:
            if candidate.direction not in {"higher_better", "lower_better"}:
                raise ValueError("LLM direction 只能是 higher_better 或 lower_better")
            field_set = set(candidate.fields)
            if field_set - allowed_features:
                raise ValueError("fields 中包含未授权特征")
            ok, reason = validate_formula(candidate.formula, allowed_features, allowed_operators, constraints)
            if not ok:
                raise ValueError(reason)
            formula_fields = formula_feature_names(candidate.formula, allowed_features)
            if field_set != formula_fields:
                raise ValueError(
                    f"fields 与公式实际使用特征不一致: fields={sorted(field_set)}, formula={sorted(formula_fields)}"
                )
            factor_data = candidate.model_dump()
            factor_data["llm_direction"] = factor_data.pop("direction")
            factor_data.pop("backtest_rule", None)
            accepted.append(factor_data)
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
    log_step_end("06", "因子验证完成", details=[f"通过: {len(accepted)}, 拒绝: {len(rejected)}"])


if __name__ == "__main__":
    try:
        run()
    except ValidationError as exc:
        write_json(
            OUTPUT_DIR / "llm" / "factors_rejected.json",
            {"factors": [{"factor_name": "payload", "formula": "", "reason": str(exc)}]},
        )
        raise
