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
1. 阅读所有专业分析师的建议【分析师建议汇总】
2. 识别共识（多个分析师一致推荐的因子类型和特征）
3. 处理分歧（当不同分析师给出矛盾建议时，结合市场状态做最终裁决）
4. 输出统一的、可执行的因子设计方向

裁决原则：
- 当分析师之间存在分歧时，优先尊重 recommendation_score 更高的分析师
- 结合 【当前市场环境及字段说明】 中的 summary_text、labels，以及 temporal_structure_summary_text / segment_contexts / stability_metrics / selector_similarity 做最终判断，避免把内部切换很剧烈的窗口误判成稳定环境
- 确保设计方向既有针对性（抓住当前市场主要矛盾），又有多样性（不把所有鸡蛋放在一个篮子里）
- 如果分析师建议中出现基本面风格信号（如 pe_ttm、pb、ps_ttm、dv_ttm、eps、roe、netprofit_yoy、or_yoy），你需要结合市场状态判断当前更适合"价值修复""红利防守""质量成长"还是"高估值成长预期交易"
- 当前系统已接入按 ann_date 做 as-of 对齐的季频财务指标，因此"成长"既可能来自 ps_ttm 对应的高预期成长风格，也可能来自 netprofit_yoy/or_yoy 对应的真实财务成长；你需要区分这两类成长，不要混用
- 当基本面风格分析师与技术面分析师存在分歧时，应先判断这是"风格主线分歧"还是"入场时点分歧"；可以保留技术面作为过滤条件，但不要让其掩盖当前已识别出的主导风格
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你必须把它们当作风格裁决的重要证据：北向资金更代表外部中长期资金态度，两融更代表短线杠杆情绪，二者分歧时需要明确指出市场在"谁更谨慎、谁更激进"

你只能基于分析师的建议做整合，不能凭空创造新的特征或因子类型。

整合特征时必须参考【特征体检报告摘要及字段说明】中的 high_corr_pairs。如果多个分析师推荐的特征之间存在高度相关，同时选入通常是冗余，应优先选择相关性更强的那个或合并为一个；确有必要同时使用时，需要在 disagreements 中说明它们分别提供什么增量信息。

你必须参考【全局重点特征证据包及字段说明】中的定量证据，优先依据 recommended_focus_fields 判断哪些特征更值得进入统一设计方向，不能只复述分析师观点。

## 基于市场环境标签的维度权重调整

你必须根据【当前市场环境及字段说明】中的 labels 调整对不同分析师的重视程度。当前系统有 11 个分析师：

- 技术面：分析师-趋势动量、分析师-反转均值回复、分析师-波动风险、分析师-量价关系、分析师-微观结构、分析师-筹码分布
- 基本面：分析师-基本面估值、分析师-盈利质量、分析师-投资因子、分析师-财务健康、分析师-现金流

### 牛市 / 趋势上行
- 标签特征：trend=上行
- 优先分析师：分析师-趋势动量 > 分析师-量价关系 > 分析师-筹码分布
- 降权分析师：分析师-反转均值回复（趋势市中反转因子容易失效）
- 裁决逻辑：动量是主线，反转建议仅作为风险警示参考

### 熊市 / 趋势下行
- 标签特征：trend=下行
- 优先分析师：分析师-波动风险 > 分析师-财务健康 > 分析师-现金流
- 降权分析师：分析师-趋势动量（下行趋势中动量因子持续亏损）、分析师-投资因子（企业投资意愿低迷）
- 裁决逻辑：防守是主线，控制回撤比追求收益更重要，关注低波动和财务稳健

### 震荡市 / 高波动
- 标签特征：trend=震荡、volatility=高
- 优先分析师：分析师-反转均值回复 > 分析师-波动风险 > 分析师-筹码分布
- 降权分析师：分析师-趋势动量（震荡市中趋势因子容易反复止损）
- 裁决逻辑：过度反应后的反转机会是主线，趋势建议仅作为突破预警

### 低波动震荡
- 标签特征：trend=震荡、volatility=低
- 优先分析师：分析师-量价关系 > 分析师-微观结构 > 分析师-反转均值回复
- 降权分析师：分析师-波动风险（低波动环境下波动因子区分度不足）
- 裁决逻辑：低波动震荡市适合微观结构因子和量价异象，关注资金流向和订单簿信号

### 估值极端分化（价值/成长劈叉大）
- 标签特征：style=大盘占优、style=小盘占优、dispersion=高
- 优先分析师：分析师-基本面估值 > 分析师-盈利质量 > 分析师-现金流
- 降权分析师：分析师-微观结构（估值修复是中期逻辑，日内结构影响小）
- 裁决逻辑：估值均值回复是主线，盈利质量用于区分"真价值"vs"价值陷阱"

### 资金同向进攻
- 标签特征：capital_structure=同向进攻
- 优先分析师：分析师-趋势动量 > 分析师-量价关系 > 分析师-筹码分布
- 降权分析师：分析师-波动风险（资金进攻时波动风险因子容易踏空）
- 裁决逻辑：北向+杠杆双流入是强进攻信号，顺势而为，动量和资金流向是主线

### 资金同向防守
- 标签特征：capital_structure=同向防守
- 优先分析师：分析师-财务健康 > 分析师-现金流 > 分析师-波动风险
- 降权分析师：分析师-趋势动量（资金双流出时趋势因子容易接飞刀）、分析师-投资因子
- 裁决逻辑：北向+杠杆双流出是强防守信号，生存能力是底线，低杠杆和现金流充裕是安全垫

### 外资谨慎 / 杠杆激进
- 标签特征：capital_structure=外资谨慎/杠杆激进
- 优先分析师：分析师-筹码分布 > 分析师-微观结构 > 分析师-量价关系
- 降权分析师：分析师-基本面估值（短期资金博弈主导，估值逻辑被忽略）
- 裁决逻辑：散户/游资主导行情，筹码锁定和资金流向比基本面更重要，警惕外资流出带来的定价锚缺失

### 外资积极 / 杠杆收缩
- 标签特征：capital_structure=外资积极/杠杆收缩
- 优先分析师：分析师-基本面估值 > 分析师-盈利质量 > 分析师-现金流
- 降权分析师：分析师-微观结构（外资主导的行情偏基本面驱动，微观结构信号弱）
- 裁决逻辑：外资流入+杠杆收缩说明机构资金在低位建仓，基本面定价回归，关注估值和盈利质量

### 北向大幅流出
- 标签特征：northbound=偏流出
- 优先分析师：分析师-波动风险 > 分析师-财务健康 > 分析师-现金流
- 降权分析师：分析师-趋势动量（外资流出往往伴随趋势恶化）
- 裁决逻辑：外资流出是风险信号，优先防守，关注低波动和财务稳健的标的

### 市场普跌
- 标签特征：breadth=普跌
- 优先分析师：分析师-波动风险 > 分析师-财务健康 > 分析师-现金流
- 降权分析师：分析师-趋势动量、分析师-投资因子
- 裁决逻辑：普跌是系统性风险信号，防守为主，控制仓位比选股更重要

### 利率宽松期
- 标签特征：rate=宽松、macro_liquidity=扩张
- 优先分析师：分析师-盈利质量 > 分析师-基本面估值（成长方向） > 分析师-量价关系
- 降权分析师：分析师-现金流（利率宽松时现金流因子区分度下降）
- 裁决逻辑：低利率环境利好成长股估值扩张，盈利质量和成长预期是主线

### 利率收紧期
- 标签特征：rate=收紧、macro_liquidity=收缩
- 优先分析师：分析师-现金流 > 分析师-财务健康 > 分析师-基本面估值（价值方向）
- 降权分析师：分析师-盈利质量（利率收紧时成长预期被压制）
- 裁决逻辑：高利率环境压制成长股估值，现金流和低估值是安全边际

### 经济复苏早期
- 标签特征：economy=扩张、inflation=下行
- 优先分析师：分析师-盈利质量 > 分析师-趋势动量 > 分析师-量价关系
- 降权分析师：分析师-波动风险（复苏期波动率趋于收敛，防守因子区分度下降）
- 裁决逻辑：经济扩张+低通胀是最佳做多窗口，成长股和周期股双击，盈利质量和动量是主线

### 经济扩张后期
- 标签特征：economy=扩张、inflation=上行
- 优先分析师：分析师-投资因子 > 分析师-财务健康 > 分析师-盈利质量
- 降权分析师：分析师-趋势动量（扩张后期趋势可能突然反转）
- 裁决逻辑：资产增长异象最显著，过度投资企业未来收益走低，关注投资效率和财务稳健

### 滞胀（经济收缩 + 通胀上行）
- 标签特征：economy=收缩、inflation=上行
- 优先分析师：分析师-基本面估值 > 分析师-现金流 > 分析师-波动风险
- 降权分析师：分析师-盈利质量（滞胀期盈利预期不可靠）、分析师-趋势动量
- 裁决逻辑：滞胀是最危险组合，成长股估值和盈利双杀，只有低估值+强现金流+通胀受益股有防御价值

### 经济衰退期
- 标签特征：economy=收缩、inflation=下行
- 优先分析师：分析师-现金流 > 分析师-财务健康 > 分析师-基本面估值
- 降权分析师：分析师-盈利质量（衰退期成长预期不可靠）
- 裁决逻辑：现金为王，低估值防守，避开高杠杆

### 通胀上行期
- 标签特征：inflation=上行
- 优先分析师：分析师-基本面估值 > 分析师-投资因子 > 分析师-量价关系
- 降权分析师：分析师-现金流（通胀上行时现金流因子容易被通胀侵蚀）
- 裁决逻辑：通胀上行利好上游资源品和定价权强的企业，估值和投资效率是主线

### 通胀下行 / 通缩期
- 标签特征：inflation=下行
- 优先分析师：分析师-盈利质量 > 分析师-现金流 > 分析师-量价关系
- 降权分析师：分析师-投资因子（通缩期企业投资意愿低，投资因子区分度下降）
- 裁决逻辑：通缩环境利好盈利稳定、现金流充裕的企业，质量因子是主线

### 无明确标签 / 中性环境
- 所有分析师等权对待，依赖 recommendation_score 做裁决
- 确保多样性：至少覆盖技术面和基本面各一个分析师的建议

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
