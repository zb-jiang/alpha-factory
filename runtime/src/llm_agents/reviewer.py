"""因子评审员：对每个候选因子逐一评审，只给出 PASS 或 REJECT 两种决定。

评审维度：
1. 未来函数风险（最高优先级）
2. 特征合规性
3. 逻辑一致性
4. 与已淘汰因子相似度
5. 过度拟合嫌疑
"""
from __future__ import annotations

import json
from typing import Any

from .agent_runner import AgentConfig, call_llm_agent

_REVIEWER_SYSTEM = """你是因子质量评审员。你以极其严格的标准评审每个候选因子，只给出两种决定：

- PASS：因子通过评审，可以进入后续流程
- REJECT:<原因>：因子被拒绝，必须说明具体原因

评审维度（按优先级排序）：
1. 未来函数风险：公式中是否使用了未来数据（如 .shift(-1)）—— 发现即 REJECT
2. 特征合规性：公式中使用的特征是否在允许的特征列表中 —— 使用未授权特征即 REJECT
3. 公式约束合规性：公式是否违反【公式约束】中的硬性规则（嵌套深度超限、特征数量超限、算子数量超限、触及禁止算子链等）—— 违反即 REJECT
4. 逻辑一致性：因子逻辑是否与首席分析师的设计方向一致 —— 严重偏离即 REJECT
5. 与已淘汰因子相似度：公式是否与历史被淘汰因子过度相似 —— 高度相似即 REJECT
6. 过度拟合嫌疑：公式是否过于复杂（超过 4 层嵌套）、参数过多、缺乏经济学直觉 —— 嫌疑高即 REJECT

你的决策标准必须一致且可解释。不要给出模糊的评审意见。"""


def _build_reviewer_messages(
    raw_response_draft: dict[str, Any], context: dict[str, Any]
) -> list[dict[str, str]]:
    """构建评审员的 messages。"""
    user_content = f"""请对以下候选因子逐一进行严格评审。

【待评审因子】
{json.dumps(raw_response_draft.get('factors', []), ensure_ascii=False, indent=2)}

【允许使用的特征列表】
{json.dumps(context.get('feature_names', []), ensure_ascii=False)}

【允许使用的算子】
{json.dumps(context.get('allowed_operators', []), ensure_ascii=False)}

【公式约束】（硬性规则，违反即 REJECT）
{json.dumps(context.get('generation_constraints', {}), ensure_ascii=False, indent=2)}

【设计方向（用于逻辑一致性检查）】
{json.dumps(context.get('design_direction', {}), ensure_ascii=False)}

【历史被淘汰因子（用于相似度检查）】
{json.dumps(context.get('previous_skipped', []), ensure_ascii=False)}

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "review_results": [
    {{
      "factor_name": "因子名称",
      "decision": "PASS 或 REJECT:具体原因",
      "reason": "如果 REJECT，这里写具体原因；如果 PASS，留空"
    }}
  ]
}}"""

    return [
        {"role": "system", "content": _REVIEWER_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _parse_review_results(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析评审结果，分离通过和拒绝的因子。

    返回 (passed_factors, rejected_factors)。
    """
    results = raw.get("review_results", [])
    if not isinstance(results, list):
        return [], []

    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in results:
        if not isinstance(item, dict):
            continue
        factor_name = str(item.get("factor_name", ""))
        decision = str(item.get("decision", "")).strip()
        reason = str(item.get("reason", ""))

        if not factor_name:
            continue

        if decision == "PASS":
            passed.append({"factor_name": factor_name, "decision": decision, "reason": reason})
        elif decision.startswith("REJECT"):
            rejected.append({"factor_name": factor_name, "decision": decision, "reason": reason})
        else:
            # 未知决策视为拒绝
            rejected.append(
                {
                    "factor_name": factor_name,
                    "decision": "REJECT:评审结果不明确，按拒绝处理",
                    "reason": reason,
                }
            )

    return passed, rejected


def run_reviewer(
    agent_config: AgentConfig,
    raw_response_draft: dict[str, Any],
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """运行因子评审员，返回 (passed_factors, rejected_factors)。

    评审员失败时，默认所有因子通过（激进策略），但会打印警告日志。
    """
    try:
        messages = _build_reviewer_messages(raw_response_draft, context)
        raw = call_llm_agent(agent_config, messages)
        if not isinstance(raw, dict):
            raise ValueError(f"评审员返回非字典: {type(raw)}")
        return _parse_review_results(raw)
    except Exception as exc:
        print(f"警告: 因子评审员执行失败，默认所有因子通过: {exc}")
        # 激进策略：所有因子默认通过
        factors = raw_response_draft.get("factors", [])
        passed = [
            {
                "factor_name": f.get("factor_name", ""),
                "decision": "PASS",
                "reason": "评审员失败，默认通过",
            }
            for f in factors
            if isinstance(f, dict) and f.get("factor_name")
        ]
        return passed, []
