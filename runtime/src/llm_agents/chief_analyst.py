"""首席分析师：整合多个专业分析师的输出，给出统一的因子设计方向。

输入：5 个专业分析师的建议
输出：design_direction.json 格式的字典
"""
from __future__ import annotations

import json
from typing import Any

from .agent_runner import AgentConfig, call_llm_agent

_CHIEF_ANALYST_SYSTEM = """你是首席量化分析师。你拥有超过 20 年的 A 股量化研究经验，擅长整合不同流派分析师的观点并做出最终决策。

你的职责：
1. 阅读所有专业分析师的建议
2. 识别共识（多个分析师一致推荐的因子类型和特征）
3. 处理分歧（当不同分析师给出矛盾建议时，结合市场状态做最终裁决）
4. 输出统一的、可执行的因子设计方向

裁决原则：
- 当分析师之间存在分歧时，优先尊重 recommendation_score 更高的分析师
- 结合 market_context 中的 summary_text 和 labels 做最终判断
- 确保设计方向既有针对性（抓住当前市场主要矛盾），又有多样性（不把所有鸡蛋放在一个篮子里）
- candidate_count 必须保持为正值，默认继承自全局配置

你只能基于分析师的建议做整合，不能凭空创造新的特征或因子类型。"""


def _build_chief_messages(analyst_outputs: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, str]]:
    """构建首席分析师的 messages。"""
    analyst_section = (
        json.dumps(analyst_outputs, ensure_ascii=False, indent=2)
        if analyst_outputs
        else "没有分析师提供有效建议。"
    )

    user_content = f"""请基于以下专业分析师的建议，给出本轮因子设计的统一方向。

【分析师建议汇总】
{analyst_section}

【当前市场环境】
{json.dumps(context.get("market_context", {}), ensure_ascii=False, indent=2)}

【特征体检报告摘要】
{json.dumps(context.get("llm_summary", {}), ensure_ascii=False, indent=2)}

【上一轮表现最好的因子】
{json.dumps(context.get("previous_top", []), ensure_ascii=False, indent=2)}

【上一轮被淘汰的因子】
{json.dumps(context.get("previous_skipped", []), ensure_ascii=False, indent=2)}

【默认候选因子数量】
{context.get("candidate_count", 10)}

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "primary_focus": "本轮主攻方向，如'动量+量价共振'",
  "recommended_features": ["首席分析师推荐的特征列表"],
  "avoid_features": ["首席分析师建议避免的特征列表"],
  "risk_warnings": ["首席级别的风险警示"],
  "diversification_goal": "多样性目标，如'至少覆盖2种不同逻辑'",
  "candidate_count": 数字,
  "analyst_consensus": {{
    "high_agreement": ["分析师高度共识的要点"],
    "disagreements": ["分析师之间的分歧及你的裁决"]
  }}
}}"""

    return [
        {"role": "system", "content": _CHIEF_ANALYST_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _parse_design_direction(raw: dict[str, Any], default_candidate_count: int) -> dict[str, Any]:
    """解析并标准化首席分析师输出。"""
    candidate_count = int(raw.get("candidate_count", default_candidate_count))
    candidate_count = max(1, candidate_count)

    consensus = raw.get("analyst_consensus", {}) or {}
    if not isinstance(consensus, dict):
        consensus = {}

    return {
        "primary_focus": str(raw.get("primary_focus", "")),
        "recommended_features": list(raw.get("recommended_features", [])),
        "avoid_features": list(raw.get("avoid_features", [])),
        "risk_warnings": list(raw.get("risk_warnings", [])),
        "diversification_goal": str(raw.get("diversification_goal", "")),
        "candidate_count": candidate_count,
        "analyst_consensus": {
            "high_agreement": list(consensus.get("high_agreement", [])),
            "disagreements": list(consensus.get("disagreements", [])),
        },
    }


def run_chief_analyst(
    agent_config: AgentConfig,
    analyst_outputs: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """运行首席分析师，整合分析师输出为设计方向。

    失败时抛出 RuntimeError。
    """
    try:
        messages = _build_chief_messages(analyst_outputs, context)
        raw = call_llm_agent(agent_config, messages)
        if not isinstance(raw, dict):
            raise ValueError(f"首席分析师返回非字典: {type(raw)}")
        default_count = int(context.get("candidate_count", 10))
        return _parse_design_direction(raw, default_count)
    except Exception as exc:
        raise RuntimeError(f"首席分析师执行失败: {exc}") from exc
