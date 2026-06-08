"""因子生成器：根据首席分析师的设计方向，生成具体因子公式列表。

输出格式与现有 raw_response.json 完全一致。
"""
from __future__ import annotations

import json
from typing import Any

from common import feature_pool_config

from .agent_runner import AgentConfig, call_llm_agent
from .context_builders import build_summary_context, build_market_context, build_operator_context, build_constraints_context

_GENERATOR_SYSTEM = """你是一个顶级的量化交易策略研究员和金融数据科学家。你精通A股市场微观结构、多因子模型、行为金融学以及Alpha挖掘。
你的任务是严格遵循首席分析师的设计方向，挖掘出具有强预测能力、低相关性且逻辑严密的全新选股因子（Alpha因子）。

核心约束：
1. 你只能使用【全部可用特征列表】中的特征；优先使用【首席分析师推荐特征】，严禁使用【首席分析师建议规避特征】
2. 每个因子必须有明确的经济学逻辑解释（reason 字段）
3. 每个因子必须指明最可能失效的市场环境（expected_failure_regime 字段）
4. 公式必须可被 Python 直接 eval 执行，仅允许使用 +、-、*、/ 等基本算术运算符和【允许的算子】中的函数算子
5. 禁止使用未来函数（如 .shift(-1)）
6. 公式必须严格遵守【公式约束】中的硬性规则（嵌套深度、特征数量、算子数量、禁止链等）
7. 公式应简洁清晰，避免过度复杂的多层嵌套
8. 构造公式时必须参考特征体检报告中的 high_corr_pairs。如果两个特征高度相关，同时放入同一公式通常是冗余，应避免；确有必要同时使用时，优先用差值或比值提取增量信号
9. 如果设计方向涉及 pe_ttm、pb、ps_ttm、dv_ttm、eps、roe、netprofit_yoy、or_yoy，请正确解释它们的金融含义：pe_ttm/pb 更偏价值，dv_ttm 更偏红利，ps_ttm 更偏高成长预期定价，roe 更偏质量，netprofit_yoy/or_yoy 更偏财务成长
10. 当前系统已接入按 ann_date 做 as-of 对齐的季频财务数据，因此可以使用 eps、roe、netprofit_yoy、or_yoy 来表达真实的盈利能力和成长逻辑；但 pe_ttm、pb、ps_ttm、dv_ttm 仍应优先解释为估值/红利/风格偏好，而不是直接等同于财务成长
11. 当使用基本面风格特征时，可以生成"价值修复""红利防守""质量成长""收入/利润同比改善"这类与当前数据含义一致的逻辑，但必须避免未来函数叙事，确保理由与已接入字段一致
12. 如果市场环境中出现 northbound、leverage、capital_structure 等资金面标签，请把它们用于决定因子更偏进攻、防守、拥挤修复还是风险收缩；尤其当"外资谨慎/杠杆激进"或"外资积极/杠杆收缩"出现时，要在 risk 和 expected_failure_regime 中明确体现资金分歧风险

方向只能是 higher_better（因子值越大越看多）或 lower_better（因子值越小越看多）。

输出必须是符合指定 JSON Schema 的纯 JSON，不要 Markdown 代码块。"""

# 因子必须包含的字段
_REQUIRED_FACTOR_FIELDS = {
    "factor_name",
    "formula",
    "fields",
    "direction",
    "reason",
    "risk",
    "expected_failure_regime",
}


def _build_feature_specs(feature_names: list[str]) -> list[dict[str, str]]:
    """从 feature_pool.yaml 读取特征的 description 和 expr，构建带说明的特征列表。"""
    feature_cfg = feature_pool_config()
    feature_info_map: dict[str, dict[str, str]] = {}
    for item in feature_cfg.get("base_features", []):
        name = str(item.get("name", ""))
        feature_info_map[name] = {
            "description": str(item.get("description", "")),
            "expr": str(item.get("expr", "")),
        }
    result: list[dict[str, str]] = []
    for name in feature_names:
        info = feature_info_map.get(name, {})
        result.append({
            "name": name,
            "description": info.get("description", ""),
            "expr": info.get("expr", ""),
        })
    return result


def _build_generator_messages(design_direction: dict[str, Any], context: dict[str, Any]) -> list[dict[str, str]]:
    """构建生成器的 messages。"""
    operator_context = build_operator_context(list(context.get("allowed_operators", [])))
    constraints_context = build_constraints_context(dict(context.get("generation_constraints", {})))
    summary_context = build_summary_context(dict(context.get("llm_summary", {})))
    summary_context["数据"]["previous_top"] = context.get("previous_top", [])
    summary_context["数据"]["previous_skipped"] = context.get("previous_skipped", [])
    market_context = build_market_context(dict(context.get("market_context", {})))

    user_content = f"""请根据以下设计方向，生成 {context.get('candidate_count', 10)} 个候选因子。

【设计方向】
{json.dumps(design_direction, ensure_ascii=False, indent=2)}

【全部可用特征列表】（公式中使用的特征必须来自此列表，每个特征包含 description 业务含义和 expr 计算公式）
{json.dumps(_build_feature_specs(context.get('feature_names', [])), ensure_ascii=False)}

【首席分析师推荐特征】（优先使用）
{json.dumps(design_direction.get('recommended_features', []), ensure_ascii=False)}

【首席分析师建议规避特征】（严禁使用）
{json.dumps(design_direction.get('avoid_features', []), ensure_ascii=False)}

【允许的算子及字段说明】
{json.dumps(operator_context, ensure_ascii=False)}

【公式约束及字段说明】（硬性规则，任何不满足的公式都会在验证阶段被直接拒绝）
{json.dumps(constraints_context, ensure_ascii=False, indent=2)}

【特征体检报告摘要及字段说明】
{json.dumps(summary_context, ensure_ascii=False)}

【市场环境及字段说明】
{json.dumps(market_context, ensure_ascii=False)}

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "factors": [
    {{
      "factor_name": "因子的英文名称，如 reversal_vol_ratio_v1",
      "formula": "Python 表达式，如 'ret_5d / (1 + realized_vol_20d)'",
      "fields": ["公式中实际使用的特征名称列表"],
      "direction": "higher_better 或 lower_better",
      "reason": "中文简述因子的经济学逻辑",
      "risk": "中文简述风险",
      "expected_failure_regime": "最可能失效的市场环境"
    }}
  ]
}}"""

    return [
        {"role": "system", "content": _GENERATOR_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _parse_factors(raw: dict[str, Any]) -> dict[str, Any]:
    """解析并校验生成器输出，过滤不完整的因子。"""
    factors = raw.get("factors", [])
    if not isinstance(factors, list):
        return {"factors": []}

    valid_factors: list[dict[str, Any]] = []
    for item in factors:
        if not isinstance(item, dict):
            continue
        missing = _REQUIRED_FACTOR_FIELDS - set(item.keys())
        if missing:
            # 容错：如果只是缺少 expected_failure_regime，我们给它补上空字符串而不是直接丢弃
            if missing == {"expected_failure_regime"}:
                item["expected_failure_regime"] = ""
            else:
                print(f"生成器产出的因子缺少字段 {missing}: {item.get('factor_name', 'unknown')}")
                continue
        if item.get("direction") not in ("higher_better", "lower_better"):
            print(f"生成器产出的因子方向无效: {item.get('factor_name', 'unknown')}")
            continue
        valid_factors.append(item)

    return {"factors": valid_factors}


def run_generator(
    agent_config: AgentConfig,
    design_direction: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """运行因子生成器，返回符合现有 raw_response.json schema 的字典。

    失败时抛出 RuntimeError。
    """
    try:
        messages = _build_generator_messages(design_direction, context)
        raw = call_llm_agent(agent_config, messages)
        if not isinstance(raw, dict):
            raise ValueError(f"生成器返回非字典: {type(raw)}")
        return _parse_factors(raw)
    except Exception as exc:
        raise RuntimeError(f"因子生成器执行失败: {exc}") from exc
