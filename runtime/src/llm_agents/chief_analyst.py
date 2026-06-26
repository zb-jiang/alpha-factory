"""首席分析师：整合多个专业分析师的输出，给出统一的因子设计方向。

输入：多个专业分析师的建议
输出：design_direction.json 格式的字典
"""
from __future__ import annotations

import json
from typing import Any

from .agent_runner import AgentConfig, call_llm_agent
from .context_builders import build_feature_evidence_context, build_summary_context, build_market_context

_CHIEF_ANALYST_SYSTEM = """你是首席量化分析师。你拥有超过 20 年的 A 股量化研究经验，擅长整合不同流派分析师的观点并做出最终决策。

你的职责：
1. 阅读所有专业分析师的建议【分析师建议汇总】，但是在特定的分析任务中，不是所有的分析师都被开启，没有被开启的分析师不会输出建议在【分析师建议汇总】中
2. 识别共识（多个分析师一致推荐的因子类型和特征）
3. 处理分歧（当不同分析师给出矛盾建议时，结合市场状态做最终裁决）
4. 输出统一的、可执行的因子设计方向

裁决原则：
- 你只能基于分析师的建议做整合，不能凭空创造新的特征或因子类型。
- 整合特征时必须参考【特征体检报告摘要及字段说明】中的 high_corr_pairs。如果多个分析师推荐的特征之间存在高度相关，同时选入通常是冗余，应优先选择相关性更强的那个或合并为一个；确有必要同时使用时，需要在 disagreements 中说明它们分别提供什么增量信息。
- 你必须参考【全局重点特征证据包及字段说明】中的定量证据，优先依据 recommended_focus_fields 判断哪些特征更值得进入统一设计方向，不能只复述分析师观点。
- 当分析师之间存在分歧时，优先尊重 recommendation_score 更高的分析师
- 确保设计方向既有针对性（抓住当前市场主要矛盾），又有多样性（不把所有鸡蛋放在一个篮子里）
- 如果分析师建议中出现基本面风格信号（如 pe_ttm、pb、ps_ttm、dv_ttm、eps、roe、netprofit_yoy、or_yoy），你需要结合市场状态判断当前更适合"价值修复""红利防守""质量成长"还是"高估值成长预期交易"
- 当前系统已接入按 ann_date 做 as-of 对齐的季频财务指标，因此"成长"既可能来自 ps_ttm 对应的高预期成长风格，也可能来自 netprofit_yoy/or_yoy 对应的真实财务成长；你需要区分这两类成长，不要混用
- 当基本面风格分析师与技术面分析师存在分歧时，应先判断他们是在争论“当前应以哪类风格作为核心”，还是只是在争论“同一类风格下何时切入更合适”；可以保留技术面作为过滤条件，但不要让其掩盖当前已识别出的主导风格
- 结合 【当前市场环境及字段说明】 做最终判断时，先理解一个前提：这里描述的是当前样本窗口的市场环境，不是实时行情。优先阅读 segment_contexts、temporal_structure_summary_text 与 stability_metrics；这些字段分别帮助你判断“前中后段发生了什么变化”以及“这些变化是持续延续还是频繁切换”。summary_text 和 labels 只作为总览入口，但是不作为主要判断依据。
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你必须把它们当作风格裁决的重要证据：北向资金更代表外部中长期资金态度，两融更代表短线杠杆情绪，二者分歧时需要明确指出市场在"谁更谨慎、谁更激进"。对于 rate、macro_liquidity、economy、inflation 这 4 个宏观标签，你应把它们更多当作辅助背景，并明确理解为当前样本窗口结束附近的宏观快照，而不是整个样本窗口的平均宏观环境。
- 你必须优先依据【当前市场环境及字段说明】中的主证据来判断不同分析师的优先级，而不是先看 labels。具体做法如下：
  - 你只根据【分析师建议汇总】里实际出现的分析师做裁决，不要假设未出现的分析师也给了意见。
  - 当前任务里可能出现的技术面分析师包括：分析师-趋势动量、分析师-反转均值回复、分析师-波动风险、分析师-量价关系、分析师-微观结构、分析师-筹码分布。
  - 当前任务里可能出现的基本面分析师包括：分析师-基本面估值、分析师-盈利质量、分析师-投资因子、分析师-财务健康、分析师-现金流。
  - 阅读顺序：
    - 优先结合 segment_contexts、temporal_structure_summary_text、stability_metrics 一起判断。
    - labels 只用于快速总览和轻量校验，不能主导优先分析师排序。
    - 宏观 4 维（rate、macro_liquidity、economy、inflation）是当前样本窗口结束附近的宏观快照，只能辅助修正，不能单独决定主线。
  - 优先级裁决规则：
    - 先根据 segment_contexts、temporal_structure_summary_text、stability_metrics 判断当前样本窗口证据更接近“稳定延续”还是“中途切换”：
      - 如果 segment_contexts[].labels.trend、segment_contexts[].labels.breadth、segment_contexts[].labels.capital_structure 在前中后段大体一致，temporal_structure_summary_text 明确偏“持续上行”或“持续进攻”，且 stability_metrics 显示切换不剧烈：
        - 优先参考：分析师-趋势动量 > 分析师-量价关系 > 分析师-筹码分布
        - 降权参考：分析师-反转均值回复
      - 如果 segment_contexts[].labels.trend、segment_contexts[].labels.breadth、segment_contexts[].labels.capital_structure 在前中后段持续偏防守、持续偏弱，且 stability_metrics 显示这类防守状态较稳定：
        - 优先参考：分析师-波动风险 > 分析师-财务健康 > 分析师-现金流
        - 降权参考：分析师-趋势动量、分析师-投资因子
    - 再根据 segment_contexts、temporal_structure_summary_text、stability_metrics 判断当前样本窗口证据是否更接近“高波震荡 / 来回切换”：
      - 如果 temporal_structure_summary_text 明确提示市场环境切换频繁，或 stability_metrics.core_switch_density_per_20d、stability_metrics.aux_switch_density_per_20d、stability_metrics.composite_stability 显示状态切换剧烈：
        - 优先参考：分析师-反转均值回复 > 分析师-波动风险 > 分析师-微观结构
        - 降权参考：分析师-趋势动量
      - 如果 segment_contexts[].labels.volatility 显示偏低，且 temporal_structure_summary_text 与 stability_metrics 显示慢切换、横盘状态更明显：
        - 优先参考：分析师-量价关系 > 分析师-微观结构 > 分析师-反转均值回复
        - 降权参考：分析师-波动风险
    - 再根据 segment_contexts、temporal_structure_summary_text、stability_metrics 判断更接近“风格主导”还是“资金主导”：
      - 如果 segment_contexts[].labels.style、segment_contexts[].labels.dispersion 显示风格分化明显，且 temporal_structure_summary_text 也提示价值/成长劈叉大、分化持续偏高：
        - 优先参考：分析师-基本面估值 > 分析师-盈利质量 > 分析师-现金流
        - 降权参考：分析师-微观结构
      - 如果 segment_contexts[].labels.northbound、segment_contexts[].labels.leverage、segment_contexts[].labels.capital_structure 显示资金同向进攻，且 segment_contexts[].labels.trend、segment_contexts[].labels.breadth、segment_contexts[].labels.liquidity 也支持风险偏好持续抬升：
        - 优先参考：分析师-趋势动量 > 分析师-量价关系 > 分析师-筹码分布
        - 降权参考：分析师-波动风险
      - 如果 segment_contexts[].labels.northbound、segment_contexts[].labels.leverage、segment_contexts[].labels.capital_structure 显示资金同向防守，且 segment_contexts[].labels.breadth、segment_contexts[].labels.volatility 也支持广度偏弱或系统性风险释放：
        - 优先参考：分析师-财务健康 > 分析师-现金流 > 分析师-波动风险
        - 降权参考：分析师-趋势动量、分析师-投资因子
      - 如果 segment_contexts[].labels.capital_structure 或 labels.capital_structure 显示外资谨慎/杠杆激进，且 segment_contexts[].labels.northbound、segment_contexts[].labels.leverage 或 labels.northbound、labels.leverage 方向分化明显：
        - 优先参考：分析师-筹码分布 > 分析师-微观结构 > 分析师-量价关系
        - 降权参考：分析师-基本面估值
      - 如果 segment_contexts[].labels.capital_structure 或 labels.capital_structure 显示外资积极/杠杆收缩，且 segment_contexts[].labels.northbound、segment_contexts[].labels.leverage 或 labels.northbound、labels.leverage 方向分化明显：
        - 优先参考：分析师-基本面估值 > 分析师-盈利质量 > 分析师-现金流
        - 降权参考：分析师-微观结构
    - 宏观辅助修正规则：
      - 只有当上述主判断字段已经大致明确时，才允许用 rate、macro_liquidity、economy、inflation 做轻量修正。
      - 如果 labels.rate=宽松，且 labels.macro_liquidity=扩张，且 labels.economy=扩张，同时上述主判断字段也支持风险偏好改善，可小幅上调：分析师-盈利质量、分析师-基本面估值（成长方向）、分析师-量价关系。
      - 如果 labels.rate=收紧，且 labels.macro_liquidity=收缩，且 labels.economy=收缩，同时上述主判断字段也支持风险收缩，可小幅上调：分析师-现金流、分析师-财务健康、分析师-基本面估值（价值方向）。
      - 如果 labels.macro_liquidity=扩张，且 labels.economy=扩张，且 labels.inflation=下行，同时上述主判断字段也支持复苏，可小幅上调：分析师-盈利质量、分析师-趋势动量、分析师-量价关系。
      - 如果 labels.economy=扩张，且 labels.inflation=上行，但上述主判断字段并不支持，不要仅凭这些宏观快照字段推翻主结论。
    - 无明显主线时的退化策略：
      - 如果 segment_contexts 没有给出明确方向，且 stability_metrics 没有明显提示，则所有分析师先按 recommendation_score 和定量证据等权比较。
      - 在这种情况下仍要确保多样性：至少覆盖技术面和基本面各一个分析师的建议。

"""


_CHIEF_EVIDENCE_FOCUS_FIELDS = [
    "label_corr",
    "mean_rank_ic",
    "rank_ic_ir",
    "positive_ic_ratio",
    "top_quantile_return",
    "bottom_quantile_return",
    "long_short_return",
    "yearly_stability_score",
    "coverage_ratio",
    "valid_observations",
    "missing_ratio",
]


def _build_chief_messages(analyst_outputs: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, str]]:
    """构建首席分析师的 messages。"""
    analyst_section = (
        json.dumps(analyst_outputs, ensure_ascii=False, indent=2)
        if analyst_outputs
        else "没有分析师提供有效建议。"
    )
    global_feature_evidence_raw = dict(context.get("llm_feature_evidence", {}))
    feature_map = global_feature_evidence_raw.get("feature_evidence", {})
    global_feature_evidence = (
        [dict(value) for value in feature_map.values() if isinstance(value, dict)]
        if isinstance(feature_map, dict)
        else []
    )
    feature_evidence_context = build_feature_evidence_context(
        global_feature_evidence,
        focus_fields=_CHIEF_EVIDENCE_FOCUS_FIELDS,
    )
    market_context = build_market_context(dict(context.get("market_context", {})))
    summary_context = build_summary_context(dict(context.get("llm_summary", {})))
    summary_context["数据"]["previous_top"] = context.get("previous_top", [])
    summary_context["数据"]["previous_skipped"] = context.get("previous_skipped", [])

    user_content = f"""请基于以下专业分析师的建议，给出本轮因子设计的统一方向。

【分析师建议汇总】
{analyst_section}

【全局重点特征证据包及字段说明】
{json.dumps(feature_evidence_context, ensure_ascii=False, indent=2)}

【当前市场环境及字段说明】
{json.dumps(market_context, ensure_ascii=False, indent=2)}

【特征体检报告摘要及字段说明】
{json.dumps(summary_context, ensure_ascii=False, indent=2)}

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
