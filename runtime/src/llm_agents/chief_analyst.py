"""首席分析师：整合多个专业分析师的输出，给出统一的因子设计方向。

输入：多个专业分析师的建议
输出：design_direction.json 格式的字典
"""
from __future__ import annotations

import json
from typing import Any

from .agent_runner import AgentConfig, call_llm_agent
from .context_builders import build_summary_context, build_market_context

_CHIEF_ANALYST_SYSTEM = """你是首席量化分析师。你拥有超过 20 年的 A 股量化研究经验，擅长整合不同流派分析师的观点并做出最终决策。

你的职责：
1. 阅读所有专业分析师的建议
2. 识别共识（多个分析师一致推荐的因子类型和特征）
3. 处理分歧（当不同分析师给出矛盾建议时，结合市场状态做最终裁决）
4. 输出统一的、可执行的因子设计方向

裁决原则：
- 当分析师之间存在分歧时，优先尊重 recommendation_score 更高的分析师
- 结合 【当前市场环境及字段说明】 中的 summary_text 和 labels 做最终判断
- 确保设计方向既有针对性（抓住当前市场主要矛盾），又有多样性（不把所有鸡蛋放在一个篮子里）
- 如果分析师建议中出现基本面风格信号（如 pe_ttm、pb、ps_ttm、dv_ttm、eps、roe、netprofit_yoy、or_yoy），你需要结合市场状态判断当前更适合“价值修复”“红利防守”“质量成长”还是“高估值成长预期交易”
- 当前系统已接入按 ann_date 做 as-of 对齐的季频财务指标，因此“成长”既可能来自 ps_ttm 对应的高预期成长风格，也可能来自 netprofit_yoy/or_yoy 对应的真实财务成长；你需要区分这两类成长，不要混用
- 当基本面风格分析师与技术面分析师存在分歧时，应先判断这是“风格主线分歧”还是“入场时点分歧”；可以保留技术面作为过滤条件，但不要让其掩盖当前已识别出的主导风格
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你必须把它们当作风格裁决的重要证据：北向资金更代表外部中长期资金态度，两融更代表短线杠杆情绪，二者分歧时需要明确指出市场在“谁更谨慎、谁更激进”

你只能基于分析师的建议做整合，不能凭空创造新的特征或因子类型。

整合特征时必须参考特征体检报告中的 high_corr_pairs。如果多个分析师推荐的特征之间存在高度相关，同时选入通常是冗余，应优先选择相关性更强的那个或合并为一个；确有必要同时使用时，需要在 disagreements 中说明它们分别提供什么增量信息。"""


def _build_chief_messages(analyst_outputs: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, str]]:
    """构建首席分析师的 messages。"""
    analyst_section = (
        json.dumps(analyst_outputs, ensure_ascii=False, indent=2)
        if analyst_outputs
        else "没有分析师提供有效建议。"
    )
    market_context = build_market_context(dict(context.get("market_context", {})))
    summary_context = build_summary_context(dict(context.get("llm_summary", {})))
    summary_context["数据"]["previous_top"] = context.get("previous_top", [])
    summary_context["数据"]["previous_skipped"] = context.get("previous_skipped", [])

    user_content = f"""请基于以下专业分析师的建议，给出本轮因子设计的统一方向。

【分析师建议汇总】
{analyst_section}

【当前市场环境及字段说明】
{json.dumps(market_context, ensure_ascii=False, indent=2)}

【特征体检报告摘要及字段说明】
{json.dumps(summary_context, ensure_ascii=False, indent=2)}

【补充裁决提醒】
- 若分析师建议中明确推荐 pe_ttm、pb、dv_ttm，应优先考虑“价值/红利”方向，并根据市场环境决定是进攻型价值修复还是防守型红利风格
- 若分析师建议中明确推荐 ps_ttm，应优先将其理解为“高成长预期 / 高估值风格偏好”；若同时推荐 netprofit_yoy、or_yoy、roe，则可以进一步裁决是否存在真实财务成长主线
- 若你选择基本面风格方向，请在 primary_focus、recommended_features 和 risk_warnings 中体现这种风格判断，而不是只给出泛化表述
- 若当前市场环境里出现北向资金与两融情绪分歧，请在 analyst_consensus.disagreements 或 risk_warnings 中明确写出这种分歧，不要把它当作普通中性环境略过

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "primary_focus": "本轮主攻方向，如'动量+量价共振'",
  "recommended_features": ["首席分析师推荐的特征列表"],
  "avoid_features": ["首席分析师建议避免的特征列表"],
  "risk_warnings": ["首席级别的风险警示"],
  "diversification_goal": "多样性目标，如'至少覆盖2种不同逻辑'",
  "analyst_consensus": {{
    "high_agreement": ["分析师高度共识的要点"],
    "disagreements": ["分析师之间的分歧及你的裁决"]
  }}
}}"""

    return [
        {"role": "system", "content": _CHIEF_ANALYST_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def _parse_design_direction(raw: dict[str, Any]) -> dict[str, Any]:
    """解析并标准化首席分析师输出。"""
    consensus = raw.get("analyst_consensus", {}) or {}
    if not isinstance(consensus, dict):
        consensus = {}

    return {
        "primary_focus": str(raw.get("primary_focus", "")),
        "recommended_features": list(raw.get("recommended_features", [])),
        "avoid_features": list(raw.get("avoid_features", [])),
        "risk_warnings": list(raw.get("risk_warnings", [])),
        "diversification_goal": str(raw.get("diversification_goal", "")),
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
        return _parse_design_direction(raw)
    except Exception as exc:
        raise RuntimeError(f"首席分析师执行失败: {exc}") from exc
