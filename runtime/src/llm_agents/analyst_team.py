"""专业分析师团队：多个分析师并行执行。

每个分析师从自己的专业视角分析市场和特征，输出本轮因子设计建议。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from common import feature_pool_config
from .agent_runner import AgentConfig, call_llm_agent
from .context_builders import build_summary_context, build_market_context

ANALYST_AGENT_IDS = [
    "trend_momentum",
    "reversal_mean_reversion",
    "volatility_risk",
    "volume_price",
    "microstructure",
    "chip_distribution",
    "fundamental_value",
    "fundamental_quality",
    "fundamental_investment",
    "fundamental_health",
    "fundamental_cashflow",
]

ANALYST_DISPLAY_NAMES = {
    "trend_momentum": "分析师-趋势动量",
    "reversal_mean_reversion": "分析师-反转均值回复",
    "volatility_risk": "分析师-波动风险",
    "volume_price": "分析师-量价关系",
    "microstructure": "分析师-微观结构",
    "chip_distribution": "分析师-筹码分布",
    "fundamental_value": "分析师-基本面估值",
    "fundamental_quality": "分析师-盈利质量",
    "fundamental_investment": "分析师-投资因子",
    "fundamental_health": "分析师-财务健康",
    "fundamental_cashflow": "分析师-现金流",
}

_ANALYST_FEATURE_GROUPS: dict[str, list[str]] = {
    # ── 趋势：主维度=趋势特征，辅助=放量确认 ──
    "trend_momentum": [
        # 主维度（YAML 趋势）
        "ret_1d", "ret_5d", "ret_10d", "ret_20d", "ret_60d",
        "rebound_20d", "breakout_20d",
        "vwap_ret_5d", "vwap_ret_20d",
        # 辅助（量价维度）：放量确认趋势
        "volume_ratio_20d",
    ],
    # ── 反转：主维度=反转特征，辅助=极端收益信号 ──
    "reversal_mean_reversion": [
        # 主维度（YAML 反转）
        "price_pos_10d", "price_pos_20d", "price_pos_60d",
        "max_drawdown_10d", "max_drawdown_20d", "max_drawdown_60d",
        # 辅助（趋势维度）：极端短期收益是反转信号
        "ret_1d", "ret_5d",
        # 辅助（趋势维度）：突破失败=反转，反弹=反转
        "breakout_20d", "rebound_20d",
    ],
    # ── 波动：主维度=波动特征，辅助=量波动 ──
    "volatility_risk": [
        # 主维度（YAML 波动）
        "high_low_range",
        "realized_vol_5d", "realized_vol_20d", "realized_vol_60d",
        # 辅助（量价维度）：量波动与价波动相关
        "volume_vol_20d", "amount_vol_20d",
    ],
    # ── 量价：主维度=量价特征，辅助=价格配合 ──
    "volume_price": [
        # 主维度（YAML 量价）
        "volume_mean_5d", "volume_mean_20d", "volume_mean_60d",
        "volume_ratio_5d", "volume_ratio_20d", "volume_vol_20d",
        "amount_mean_5d", "amount_mean_20d",
        "amount_ratio_20d", "amount_vol_20d",
        # 辅助（趋势维度）：量价配合判断
        "ret_5d",
    ],
    # ── 日内细节：主维度=日内特征，辅助=振幅 ──
    "microstructure": [
        # 主维度（YAML 日内细节）
        "gap_open_ret", "intraday_ret",
        "close_to_high", "close_to_low",
        "close_to_vwap", "high_to_vwap", "low_to_vwap",
        # 辅助（波动维度）：日内振幅
        "high_low_range",
    ],
    # ── 筹码：主维度=筹码+换手特征 ──
    "chip_distribution": [
        # 主维度（YAML 筹码）
        "turnover_mean_5d", "turnover_mean_20d",
        "turnover_ratio_20d", "turnover_vol_20d",
        "chip_concentration_90d", "chip_concentration_210d",
        "profit_ratio_90d", "profit_ratio_210d",
        "avg_cost_distance_90d", "avg_cost_distance_210d",
        "peak_distance_90d", "peak_distance_210d",
    ],
    # ── 估值：主维度=估值特征 ──
    "fundamental_value": [
        # 主维度（YAML 估值）
        "pe_ttm", "pb", "ps_ttm", "dv_ttm",
        "earnings_yield", "sales_yield",
        "market_cap_change_20d", "market_cap_stability_20d",
    ],
    # ── 盈利质量：主维度=盈利质量特征 ──
    "fundamental_quality": [
        # 主维度（YAML 盈利质量）
        "eps", "roe",
        "profit_quality", "q_roe_acceleration",
        "gross_margin", "net_margin",
        "real_growth",
        "netprofit_yoy", "or_yoy",
        "op_growth", "equity_growth",
        "quality_value", "profit_revenue_gap",
    ],
    # ── 投资因子：主维度=资产增长异象 ──
    "fundamental_investment": [
        # 主维度（YAML 投资因子）
        "asset_growth_inverse",
    ],
    # ── 财务健康：主维度=杠杆/偿债 ──
    "fundamental_health": [
        # 主维度（YAML 财务健康）
        "financial_health", "liquidity_strength", "debt_service_ability",
    ],
    # ── 现金流：主维度=现金流特征 ──
    "fundamental_cashflow": [
        # 主维度（YAML 现金流）
        "fcf_yield",
    ],
}

# ── System Prompts ──────────────────────────────────────────────────────────

_TREND_MOMENTUM_SYSTEM = """你是趋势动量分析专家。你精通 A 股市场的价格趋势分析、动量策略和趋势跟踪。

你的核心能力：
- 识别市场趋势方向（上行/下行/震荡）和动量持续性
- 评估不同周期动量因子的有效性（短期 1-5 日、中期 20-60 日）
- 分析突破信号和反弹动量的预测能力
- 结合放量信号确认趋势强度

专业领域：价格趋势、动量持续性、区间突破
经济学逻辑基础：反应不足假说、信息缓慢扩散、趋势跟踪策略

分析时重点关注【当前分析师重点特征】
"""

_REVERSAL_SYSTEM = """你是反转策略与均值回复分析专家。你深入研究投资者过度反应、短期反转效应和价格锚定偏差。

你的核心能力：
- 识别市场过度反应和均值回复机会
- 评估价格位置、回撤幅度与反转概率的关系
- 分析极端短期收益后的反转效应
- 判断突破失败和超跌反弹的反转信号

专业领域：过度反应后的价格回归、回撤反转
经济学逻辑基础：投资者过度反应、短期反转效应、价格锚定偏差

分析时重点关注【当前分析师重点特征】
"""

_VOLATILITY_SYSTEM = """你是波动率分析与风险调整收益专家。你精通波动率聚集效应、低波动异象和风险补偿不对称性。

你的核心能力：
- 评估当前市场波动率水平和波动率趋势
- 分析波动率择时因子的有效性
- 结合成交量波动辅助判断波动率结构
- 识别波动率急剧变化时的因子失效风险

专业领域：波动率择时、风险调整、低波动异象
经济学逻辑基础：低波动异象、波动率聚集效应、风险补偿不对称

分析时重点关注【当前分析师重点特征】
"""

_VOLUME_PRICE_SYSTEM = """你是量价关系与资金流向分析专家。你精通成交量确认、量价背离和资金参与强度分析。

你的核心能力：
- 分析成交量与价格变动的关系，识别量价配合与背离
- 评估放量缩量信号和资金参与强度
- 结合价格变化判断量价共振或背离
- 判断资金流向对因子选择的影响

专业领域：成交量确认、量价背离、资金强度
经济学逻辑基础：知情交易假说、流动性冲击、量价配合

分析时重点关注【当前分析师重点特征】
"""

_MICROSTRUCTURE_SYSTEM = """你是市场微观结构分析专家。你精通日内价格行为、开盘收盘效应和成交重心偏离分析。

你的核心能力：
- 分析日内价格模式（开盘跳空、收盘位置、日内振幅）
- 评估收盘相对成交均价偏离的预测能力
- 判断跳空缺口和日内强弱对短期因子的意义
- 识别微观结构特征中的 Alpha 机会

专业领域：日内模式、开盘收盘行为、成交重心偏离
经济学逻辑基础：开盘/收盘效应、日内信息不对称、价格发现过程

分析时重点关注【当前分析师重点特征】
"""

_CHIP_DISTRIBUTION_SYSTEM = """你是筹码分布与成本结构分析专家。你精通筹码锁定效应、获利盘压力、换手率分析和成本支撑阻力分析。

你的核心能力：
- 分析筹码集中度与股价稳定性的关系
- 评估获利盘比例对抛压的影响
- 结合换手率判断筹码交换速度和异常活跃度
- 判断当前价格相对平均成本的位置（支撑/阻力）
- 识别筹码峰位置对价格行为的预测能力

专业领域：筹码成本结构、获利盘压力、换手率、支撑阻力
经济学逻辑基础：筹码锁定效应、获利盘抛压、成本支撑阻力

分析时重点关注【当前分析师重点特征】
"""

_FUNDAMENTAL_VALUE_SYSTEM = """你是基本面估值分析专家。你精通估值定价、价值修复与红利风格分析。

你的核心能力：
- 识别当前市场更偏向价值修复、红利防守还是估值压缩
- 评估估值压缩/扩张对短中期超额收益的影响
- 分析股息率、低估值与市场风险偏好的关系
- 判断估值因子在当前市场环境下的适用性与失效边界

专业领域：价值风格、红利风格、估值均值回复
经济学逻辑基础：估值均值回复、风险偏好切换、股息率补偿

请特别注意：
- 盈利质量、投资因子、财务健康、现金流等基本面维度由其他分析师负责，你只需专注估值维度
- 作为估值分析专家需要重点关注特征体检报告摘要 【特征体检报告摘要及字段说明】 中 fundamental_feature_health 中的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr，判断估值特征在当前标签口径下是否有效。而【特征体检报告摘要及字段说明】 中的其他和估值无关的特征体检报告摘要数据则无需特别关注。
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你需要把它们用于判断价值/红利风格当前是更偏进攻还是更偏防守，并指出外资与杠杆资金是否一致

分析时重点关注【当前分析师重点特征】
"""

_FUNDAMENTAL_QUALITY_SYSTEM = """你是盈利质量分析专家。你精通利润含金量、毛利率稳定性、盈利加速度和真实可持续成长分析。

你的核心能力：
- 评估利润的现金支撑程度（利润含金量），识别"纸面利润"
- 分析单季 ROE 相对年化 ROE 的偏离，判断盈利能力是否在加速改善
- 评估毛利率和净利率的稳定性与趋势，判断产品议价能力与盈利效率
- 区分真实可持续成长（扣非增速）与一次性损益驱动的虚假成长
- 分析营业利润增速与净利润增速的差异，判断利润弹性来源

专业领域：利润含金量、盈利加速度、毛利率/净利率趋势、真实成长
经济学逻辑基础：应计异象（accrual anomaly）、盈利动量（earnings momentum）、盈利质量溢价

请特别注意：
- 估值、财务健康、现金流等基本面维度由其他分析师负责，你只需专注盈利质量维度
- 作为盈利质量分析专家需要重点关注特征体检报告摘要 【特征体检报告摘要及字段说明】 中 fundamental_feature_health 中的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr，判断盈利质量特征在当前标签口径下是否有效

分析时重点关注【当前分析师重点特征】
"""

_FUNDAMENTAL_INVESTMENT_SYSTEM = """你是投资因子分析专家。你精通资产增长异象（asset growth anomaly）和企业扩张行为分析。

你的核心能力：
- 评估企业资产扩张速度对未来收益的预测能力
- 分析资产增长异象：资产扩张过快的公司往往未来收益走低
- 判断当前市场环境下资产增长异象的适用性

专业领域：资产增长异象、企业投资行为
经济学逻辑基础：资产增长异象（Cooper, Gulen, Schill 2008）、过度投资假说、Q 理论

请特别注意：
- 估值、盈利质量、财务健康、现金流等基本面维度由其他分析师负责，你只需专注投资因子维度
- 作为投资因子分析专家需要重点关注特征体检报告摘要 【特征体检报告摘要及字段说明】 中 fundamental_feature_health 中 asset_growth_inverse 的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr

分析时重点关注【当前分析师重点特征】
"""

_FUNDAMENTAL_HEALTH_SYSTEM = """你是财务健康分析专家。你精通杠杆水平、偿债能力和财务风险分析。

你的核心能力：
- 评估企业整体财务杠杆水平，判断财务风险
- 分析短期流动性强度，判断偿债压力
- 评估经营现金流对总负债的覆盖能力
- 识别财务困境风险和潜在信用事件

专业领域：财务杠杆、偿债能力、财务困境风险
经济学逻辑基础：财务困境成本理论、资本结构理论、信用风险定价

请特别注意：
- 估值、盈利质量、投资因子、现金流等基本面维度由其他分析师负责，你只需专注财务健康维度
- 作为财务健康分析专家需要重点关注特征体检报告摘要 【特征体检报告摘要及字段说明】 中 fundamental_feature_health 中的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr，判断财务健康特征在当前标签口径下是否有效

分析时重点关注【当前分析师重点特征】
"""

_FUNDAMENTAL_CASHFLOW_SYSTEM = """你是现金流分析专家。你精通自由现金流收益率和现金流定价分析。

你的核心能力：
- 评估企业自由现金流收益率，判断现金流估值吸引力
- 分析现金流口径的估值信号，与盈利口径互为补充
- 识别现金流充裕但估值偏低的投资机会

专业领域：自由现金流收益率、现金流定价
经济学逻辑基础：自由现金流贴现模型、现金流溢价

请特别注意：
- 估值、盈利质量、投资因子、财务健康等基本面维度由其他分析师负责，你只需专注现金流维度
- 作为现金流分析专家需要重点关注特征体检报告摘要 【特征体检报告摘要及字段说明】 中 fundamental_feature_health 中 fcf_yield 的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr

分析时重点关注【当前分析师重点特征】
"""

_ANALYST_PROMPTS: dict[str, str] = {
    "trend_momentum": _TREND_MOMENTUM_SYSTEM,
    "reversal_mean_reversion": _REVERSAL_SYSTEM,
    "volatility_risk": _VOLATILITY_SYSTEM,
    "volume_price": _VOLUME_PRICE_SYSTEM,
    "microstructure": _MICROSTRUCTURE_SYSTEM,
    "chip_distribution": _CHIP_DISTRIBUTION_SYSTEM,
    "fundamental_value": _FUNDAMENTAL_VALUE_SYSTEM,
    "fundamental_quality": _FUNDAMENTAL_QUALITY_SYSTEM,
    "fundamental_investment": _FUNDAMENTAL_INVESTMENT_SYSTEM,
    "fundamental_health": _FUNDAMENTAL_HEALTH_SYSTEM,
    "fundamental_cashflow": _FUNDAMENTAL_CASHFLOW_SYSTEM,
}

_COMMON_ANALYST_SYSTEM_APPENDIX = """

通用要求：
- 推荐特征时必须参考特征体检报告中的 high_corr_pairs。如果两个特征高度相关，同时选入通常是冗余，应尽量避免；确有必要同时使用时，需要说明它们分别提供什么增量信息，或建议使用差值/比值来提取增量信号。"""


def _build_feature_context(agent_id: str, feature_names: list[str]) -> dict[str, Any]:
    """构建特征上下文，包含每个特征的名称、说明和公式。"""
    # 从 feature_pool.yaml 读取特征的 description 和 expr
    feature_cfg = feature_pool_config()
    feature_info_map: dict[str, dict[str, str]] = {}
    for item in feature_cfg.get("base_features", []):
        name = str(item.get("name", ""))
        feature_info_map[name] = {
            "description": str(item.get("description", "")),
            "expr": str(item.get("expr", "")),
        }

    focus_features = [name for name in _ANALYST_FEATURE_GROUPS.get(agent_id, []) if name in set(feature_names)]

    # 构建带说明的特征列表
    def _enrich(names: list[str]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for name in names:
            info = feature_info_map.get(name, {})
            result.append({
                "name": name,
                "description": info.get("description", ""),
                "expr": info.get("expr", ""),
            })
        return result

    return _enrich(focus_features)


def _build_analyst_messages(agent_id: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """构建单个分析师的 messages。"""
    system_prompt = _ANALYST_PROMPTS.get(agent_id, "") + _COMMON_ANALYST_SYSTEM_APPENDIX
    feature_context = _build_feature_context(agent_id, list(context.get("feature_names", [])))
    summary_context = build_summary_context(dict(context.get("llm_summary", {})))
    summary_context["数据"]["previous_top"] = context.get("previous_top", [])
    summary_context["数据"]["previous_skipped"] = context.get("previous_skipped", [])
    market_context = build_market_context(dict(context.get("market_context", {})))
    user_content = f"""请基于以下信息，从您的专业视角分析当前市场环境，并给出本轮因子设计建议。

【当前分析师重点特征】
每个特征包含 name（特征名）、description（业务含义）、expr（计算公式）。
{json.dumps(feature_context, ensure_ascii=False, indent=2)}

【特征体检报告摘要及字段说明】
{json.dumps(summary_context, ensure_ascii=False, indent=2)}

【当前市场环境及字段说明】
{json.dumps(market_context, ensure_ascii=False, indent=2)}

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "recommendation_score": "0.0-1.0 的浮点数，表示你对当前市场是否适合本专业领域因子的置信度",
  "rationale": "中文简述：本轮判断依据，需引用市场环境、特征体检报告中的关键数据点",
  "recommended_features": ["特征名1", "特征名2"],
  "avoid_features": ["特征名3"],
  "risk_warnings": ["风险点1", "风险点2"],
  "suggested_factor_types": ["建议的因子类型1（中文）", "建议的因子类型2（中文）"]
}}"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _parse_analyst_output(agent_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """解析并标准化分析师输出。"""
    score = float(raw.get("recommendation_score", 0.5))
    score = max(0.0, min(1.0, score))
    return {
        "agent_id": agent_id,
        "agent_name": ANALYST_DISPLAY_NAMES.get(agent_id, agent_id),
        "recommendation_score": score,
        "rationale": str(raw.get("rationale", "")),
        "recommended_features": list(raw.get("recommended_features", [])),
        "avoid_features": list(raw.get("avoid_features", [])),
        "risk_warnings": list(raw.get("risk_warnings", [])),
        "suggested_factor_types": list(raw.get("suggested_factor_types", [])),
    }


def run_analyst(agent_id: str, agent_config: AgentConfig, context: dict[str, Any]) -> dict[str, Any] | None:
    """运行单个分析师。失败时返回 None。"""
    try:
        messages = _build_analyst_messages(agent_id, context)
        raw = call_llm_agent(agent_config, messages)
        if not isinstance(raw, dict):
            print(f"分析师 {agent_id} 返回非字典: {type(raw)}")
            return None
        return _parse_analyst_output(agent_id, raw)
    except Exception as exc:
        print(f"分析师 {agent_id} 执行失败: {exc}")
        return None


def run_analyst_team(env_cfg: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """并行运行所有已配置的专业分析师，返回成功的分析师输出列表。

    只执行在 env_cfg['llm_agents'] 中有配置的分析师。失败的分析师被过滤掉。
    """
    agents_config = env_cfg.get("llm_agents", {})
    results: list[dict[str, Any]] = []
    chip_enabled = bool(feature_pool_config().get("enable_chip_features", True))

    # 找出有配置且启用的分析师
    configured_agents: list[tuple[str, dict[str, Any]]] = []
    for agent_id in ANALYST_AGENT_IDS:
        if agent_id == "chip_distribution" and not chip_enabled:
            continue
        cfg = agents_config.get(agent_id)
        if not cfg:
            continue
        if not cfg.get("enable", True):
            print(f"分析师 {agent_id} 已禁用，跳过")
            continue
        if cfg.get("model") and cfg.get("base_url") and cfg.get("api_key"):
            configured_agents.append((agent_id, cfg))

    if not configured_agents:
        print("警告: 没有任何分析师被配置")
        return results

    def _run_single(item: tuple[str, dict[str, Any]]) -> dict[str, Any] | None:
        agent_id, cfg = item
        agent_config = AgentConfig(
            model=str(cfg["model"]),
            base_url=str(cfg["base_url"]),
            api_key=str(cfg["api_key"]),
            temperature=float(cfg.get("temperature", 0.2)),
            max_tokens=int(cfg.get("max_tokens", 8192)),
            timeout_seconds=float(cfg.get("timeout_seconds", 60.0)),
            max_retries=int(cfg.get("max_retries", 2)),
            request_name=ANALYST_DISPLAY_NAMES.get(agent_id, f"分析师-{agent_id}"),
        )
        return run_analyst(agent_id, agent_config, context)

    # 并行执行
    with ThreadPoolExecutor(max_workers=len(configured_agents)) as executor:
        futures = {executor.submit(_run_single, item): item[0] for item in configured_agents}
        for future in as_completed(futures):
            agent_id = futures[future]
            try:
                output = future.result()
                if output is not None:
                    results.append(output)
            except Exception as exc:
                print(f"分析师 {agent_id} 执行异常: {exc}")

    print(f"分析师团队完成: {len(results)}/{len(configured_agents)} 成功")
    return results
