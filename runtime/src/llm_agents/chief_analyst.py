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
- 如果分析师建议中出现基本面风格信号（如 pe_ttm、pb、ps_ttm、dv_ttm、eps、roe、netprofit_yoy、or_yoy），你需要结合市场状态判断当前更适合"价值修复""红利防守""质量成长"还是"高估值成长预期交易"
- 当前系统已接入按 ann_date 做 as-of 对齐的季频财务指标，因此"成长"既可能来自 ps_ttm 对应的高预期成长风格，也可能来自 netprofit_yoy/or_yoy 对应的真实财务成长；你需要区分这两类成长，不要混用
- 当基本面风格分析师与技术面分析师存在分歧时，应先判断这是"风格主线分歧"还是"入场时点分歧"；可以保留技术面作为过滤条件，但不要让其掩盖当前已识别出的主导风格
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你必须把它们当作风格裁决的重要证据：北向资金更代表外部中长期资金态度，两融更代表短线杠杆情绪，二者分歧时需要明确指出市场在"谁更谨慎、谁更激进"

你只能基于分析师的建议做整合，不能凭空创造新的特征或因子类型。

整合特征时必须参考特征体检报告中的 high_corr_pairs。如果多个分析师推荐的特征之间存在高度相关，同时选入通常是冗余，应优先选择相关性更强的那个或合并为一个；确有必要同时使用时，需要在 disagreements 中说明它们分别提供什么增量信息。

## 基于市场环境标签的维度权重调整

你必须根据【当前市场环境及字段说明】中的 labels 调整对不同分析师的重视程度。当前系统有 11 个分析师：

- 技术面：分析师-趋势动量、分析师-反转均值回复、分析师-波动风险、分析师-量价关系、分析师-微观结构、分析师-筹码分布
- 基本面：分析师-基本面估值、分析师-盈利质量、分析师-投资因子、分析师-财务健康、分析师-现金流

### 趋势明确（单边上涨/下跌）
- 标签特征：trend_up、trend_down、momentum_strong、breakout
- 优先分析师：分析师-趋势动量 > 分析师-量价关系 > 分析师-筹码分布
- 降权分析师：分析师-反转均值回复（趋势市中反转因子容易失效）
- 裁决逻辑：动量是主线，反转建议仅作为风险警示参考

### 震荡市 / 高波动
- 标签特征：range_bound、high_volatility、mean_reversion、oversold
- 优先分析师：分析师-反转均值回复 > 分析师-波动风险 > 分析师-筹码分布
- 降权分析师：分析师-趋势动量（震荡市中趋势因子容易反复止损）
- 裁决逻辑：过度反应后的反转机会是主线，趋势建议仅作为突破预警

### 估值极端分化（价值/成长劈叉大）
- 标签特征：value_rotation、growth_rotation、valuation_extreme、style_switch
- 优先分析师：分析师-基本面估值 > 分析师-盈利质量 > 分析师-现金流
- 降权分析师：分析师-微观结构（估值修复是中期逻辑，日内结构影响小）
- 裁决逻辑：估值均值回复是主线，盈利质量用于区分"真价值"vs"价值陷阱"

### 信用收紧 / 流动性危机
- 标签特征：credit_tightening、liquidity_crisis、risk_off、flight_to_quality
- 优先分析师：分析师-财务健康 > 分析师-现金流 > 分析师-基本面估值
- 降权分析师：分析师-投资因子（信用收紧时几乎所有扩张型企业都受损，区分度下降）
- 裁决逻辑：生存能力是主线，高杠杆企业暴雷风险大，现金流是底线

### 盈利季 / 业绩密集披露期
- 标签特征：earnings_season、profit_surprise、earnings_revision
- 优先分析师：分析师-盈利质量 > 分析师-投资因子 > 分析师-基本面估值
- 降权分析师：分析师-筹码分布（业绩驱动行情，筹码结构是次要矛盾）
- 裁决逻辑：区分真成长vs纸面利润是主线，资产增长异象在业绩验证期最显著

### 政策驱动 / 主题炒作
- 标签特征：policy_driven、theme_hype、retail_dominant
- 优先分析师：分析师-筹码分布 > 分析师-量价关系 > 分析师-微观结构
- 降权分析师：基本面全部（短期交易主导，基本面逻辑被忽略）
- 裁决逻辑：资金博弈是主线，筹码锁定和资金流向比估值更重要

### 经济扩张后期
- 标签特征：late_cycle、overheating、capex_surge
- 优先分析师：分析师-投资因子 > 分析师-财务健康 > 分析师-盈利质量
- 降权分析师：分析师-趋势动量（扩张后期趋势可能突然反转）
- 裁决逻辑：资产增长异象最显著，过度投资企业未来收益走低

### 经济衰退期
- 标签特征：recession、contraction、deflation_risk
- 优先分析师：分析师-现金流 > 分析师-财务健康 > 分析师-基本面估值
- 降权分析师：分析师-盈利质量（衰退期成长预期不可靠）
- 裁决逻辑：现金为王，低估值防守，避开高杠杆

### 无明确标签 / 中性环境
- 所有分析师等权对待，依赖 recommendation_score 做裁决
- 确保多样性：至少覆盖技术面和基本面各一个分析师的建议

"""


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
