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
    "fundamental_value_growth",
]

ANALYST_DISPLAY_NAMES = {
    "trend_momentum": "分析师-趋势动量",
    "reversal_mean_reversion": "分析师-反转均值回复",
    "volatility_risk": "分析师-波动风险",
    "volume_price": "分析师-量价关系",
    "microstructure": "分析师-微观结构",
    "chip_distribution": "分析师-筹码分布",
    "fundamental_value_growth": "分析师-基本面价值成长",
}

_ANALYST_FEATURE_GROUPS: dict[str, list[str]] = {
    "trend_momentum": [
        "ret_1d",
        "ret_5d",
        "ret_10d",
        "ret_20d",
        "ret_60d",
        "gap_open_ret",
        "breakout_20d",
        "price_pos_10d",
        "price_pos_20d",
        "price_pos_60d",
        "rebound_20d",
        "vwap_ret_20d",
        "market_cap_change_20d",
    ],
    "reversal_mean_reversion": [
        "ret_1d",
        "ret_5d",
        "ret_10d",
        "ret_20d",
        "ret_60d",
        "gap_open_ret",
        "max_drawdown_10d",
        "max_drawdown_20d",
        "max_drawdown_60d",
        "close_to_high",
        "close_to_low",
        "price_pos_10d",
        "price_pos_20d",
        "price_pos_60d",
        "rebound_20d",
        "breakout_20d",
    ],
    "volatility_risk": [
        "realized_vol_5d",
        "realized_vol_20d",
        "realized_vol_60d",
        "high_low_range",
        "volume_vol_20d",
        "amount_vol_20d",
        "turnover_vol_20d",
        "market_cap_stability_20d",
    ],
    "volume_price": [
        "volume_ratio_5d",
        "volume_ratio_20d",
        "volume_mean_5d",
        "volume_mean_20d",
        "volume_mean_60d",
        "volume_vol_20d",
        "amount_mean_5d",
        "amount_mean_20d",
        "amount_ratio_20d",
        "amount_vol_20d",
        "close_to_vwap",
        "high_to_vwap",
        "low_to_vwap",
        "vwap_ret_5d",
        "vwap_ret_20d",
        "turnover_mean_5d",
        "turnover_mean_20d",
        "turnover_ratio_20d",
    ],
    "microstructure": [
        "intraday_ret",
        "gap_open_ret",
        "close_to_high",
        "close_to_low",
        "high_low_range",
        "close_to_vwap",
        "high_to_vwap",
        "low_to_vwap",
        "vwap_ret_5d",
    ],
    "chip_distribution": [
        "chip_concentration_90d",
        "chip_concentration_210d",
        "profit_ratio_90d",
        "profit_ratio_210d",
        "avg_cost_distance_90d",
        "avg_cost_distance_210d",
        "peak_distance_90d",
        "peak_distance_210d",
        "turnover_mean_20d",
        "turnover_ratio_20d",
    ],
    "fundamental_value_growth": [
        "pe_ttm",
        "pb",
        "ps_ttm",
        "dv_ttm",
        "eps",
        "roe",
        "netprofit_yoy",
        "or_yoy",
        "earnings_yield",
        "sales_yield",
        "quality_value",
        "profit_revenue_gap",
        "market_cap_change_20d",
        "market_cap_stability_20d",
        "turnover_mean_20d",
        "turnover_ratio_20d",
        "ret_20d",
        "ret_60d",
        "realized_vol_20d",
    ],
}

# ── System Prompts ──────────────────────────────────────────────────────────

_TREND_MOMENTUM_SYSTEM = """你是趋势动量分析专家。你精通 A 股市场的价格趋势分析、动量策略和趋势跟踪。

你的核心能力：
- 识别市场趋势方向（上行/下行/震荡）和动量持续性
- 评估不同周期动量因子的有效性（短期 1-5 日、中期 20-60 日）
- 分析突破信号和价格位置的预测能力
- 判断趋势动量策略在当前市场环境下的适用性

专业领域：价格趋势、动量持续性、区间突破
经济学逻辑基础：反应不足假说、信息缓慢扩散、趋势跟踪策略

分析时重点关注以下特征：ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, gap_open_ret, breakout_20d, price_pos_10d, price_pos_20d, price_pos_60d, rebound_20d, vwap_ret_20d, market_cap_change_20d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_REVERSAL_SYSTEM = """你是反转策略与均值回复分析专家。你深入研究投资者过度反应、短期反转效应和价格锚定偏差。

你的核心能力：
- 识别市场过度反应和均值回复机会
- 评估不同周期反转因子的有效性
- 分析价格位置、回撤幅度与反转概率的关系
- 判断反转策略在当前市场环境下的适用性

专业领域：过度反应后的价格回归
经济学逻辑基础：投资者过度反应、短期反转效应、价格锚定偏差

分析时重点关注以下特征：ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, gap_open_ret, max_drawdown_10d, max_drawdown_20d, max_drawdown_60d, close_to_high, close_to_low, price_pos_10d, price_pos_20d, price_pos_60d, rebound_20d, breakout_20d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_VOLATILITY_SYSTEM = """你是波动率分析与风险调整收益专家。你精通波动率聚集效应、低波动异象和风险补偿不对称性。

你的核心能力：
- 评估当前市场波动率水平和波动率趋势
- 分析波动率择时因子的有效性
- 判断低波动策略和风险调整因子的适用性
- 识别波动率急剧变化时的因子失效风险

专业领域：波动率择时、风险调整、低波动异象
经济学逻辑基础：低波动异象、波动率聚集效应、风险补偿不对称

分析时重点关注以下特征：realized_vol_5d, realized_vol_20d, realized_vol_60d, high_low_range, volume_vol_20d, amount_vol_20d, turnover_vol_20d, market_cap_stability_20d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_VOLUME_PRICE_SYSTEM = """你是量价关系与资金流向分析专家。你精通成交量确认、量价背离和筹码交换速率。

你的核心能力：
- 分析成交量与价格变动的关系
- 识别量价背离和成交量突变信号
- 评估成交量确认因子的有效性
- 判断资金流向和筹码分布对因子选择的影响

专业领域：成交量确认、量价背离、资金强度
经济学逻辑基础：知情交易假说、流动性冲击、筹码交换速率

分析时重点关注以下特征：volume_ratio_5d, volume_ratio_20d, volume_mean_5d, volume_mean_20d, volume_mean_60d, volume_vol_20d, amount_mean_5d, amount_mean_20d, amount_ratio_20d, amount_vol_20d, close_to_vwap, high_to_vwap, low_to_vwap, vwap_ret_5d, vwap_ret_20d, turnover_mean_5d, turnover_mean_20d, turnover_ratio_20d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_MICROSTRUCTURE_SYSTEM = """你是市场微观结构分析专家。你精通日内价格行为、开盘收盘效应和价格发现过程。

你的核心能力：
- 分析日内价格模式（开盘跳空、收盘位置、日内振幅）
- 评估开盘效应和收盘效应的预测能力
- 判断价格区间结构对因子选择的影响
- 识别微观结构特征中的 Alpha 机会

专业领域：日内模式、开盘收盘行为、价格区间结构
经济学逻辑基础：开盘/收盘效应、日内信息不对称、价格发现过程

分析时重点关注以下特征：intraday_ret, gap_open_ret, close_to_high, close_to_low, high_low_range, close_to_vwap, high_to_vwap, low_to_vwap, vwap_ret_5d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_CHIP_DISTRIBUTION_SYSTEM = """你是筹码分布与成本结构分析专家。你精通筹码锁定效应、获利盘压力和成本支撑阻力分析。

你的核心能力：
- 分析筹码集中度与股价稳定性的关系
- 评估获利盘比例对抛压的影响
- 判断当前价格相对平均成本的位置（支撑/阻力）
- 识别筹码峰位置对价格行为的预测能力

专业领域：筹码成本结构、获利盘压力、支撑阻力
经济学逻辑基础：筹码锁定效应、获利盘抛压、成本支撑阻力

分析时重点关注以下特征：chip_concentration_90d, chip_concentration_210d, profit_ratio_90d, profit_ratio_210d, avg_cost_distance_90d, avg_cost_distance_210d, peak_distance_90d, peak_distance_210d

筹码特征解读指南：
- chip_concentration（筹码集中度）：值越小代表筹码越集中，抛压越小，股价越稳定
- profit_ratio（获利盘比例）：当前价以下的筹码占比，>80%抛压大，<20%可能见底
- avg_cost_distance（平均成本偏离度）：当前价相对平均成本的偏离，偏离度小代表接近市场平均成本
- peak_distance（筹码峰距离）：当前价相对筹码峰的距离，距离越近支撑/压力越强

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合筹码因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_FUNDAMENTAL_VALUE_GROWTH_SYSTEM = """你是基本面风格与价值成长轮动分析专家。你精通估值定价、红利风格、成长预期交易以及市场风格切换。

你的核心能力：
- 识别当前市场更偏向价值修复、红利防守还是高估值成长扩张
- 评估估值压缩/扩张对短中期超额收益的影响
- 分析股息率、低估值与市场风险偏好的关系
- 判断基本面风格因子在当前市场环境下的适用性与失效边界

专业领域：价值风格、红利风格、成长预期、风格轮动
经济学逻辑基础：估值均值回复、风险偏好切换、股息率补偿、高预期成长定价

请特别注意：
- 当前系统已接入日频估值/红利特征，以及按 ann_date 做 as-of 对齐的季频财务特征
- 其中 pe_ttm / pb 越低通常越偏价值，dv_ttm 越高通常越偏红利，ps_ttm 越高通常越偏高成长预期
- eps、roe、netprofit_yoy、or_yoy 可以用于判断真实盈利能力与财务成长，但它们只能按公告日之后生效，你在解释时要默认这些特征已经过 as-of 对齐，严禁把公告前不可见的信息当作已知
- 生成建议时可以结合 market_cap、turnover、ret_20d 等特征做风格过滤或风险约束，但主逻辑应围绕价值/成长风格展开
- 作为基本面风格与价值成长轮动分析专家需要重点关注特征体检报告摘要 llm_summary 中 fundamental_feature_health 中的 long_short_return、top_quantile_return、bottom_quantile_return 和 label_corr，判断基本面/风格特征在当前标签口径下是否有效。而llm_summary 中的其他和基本面风格无关的特征体检报告摘要数据则无需特别关注。
-
- 如果 【当前市场环境及字段说明】 中出现 northbound、leverage、capital_structure 等资金面标签，你需要把它们用于判断价值/红利/成长风格当前是更偏进攻还是更偏防守，并指出外资与杠杆资金是否一致

分析时重点关注以下特征：pe_ttm, pb, ps_ttm, dv_ttm, eps, roe, netprofit_yoy, or_yoy, earnings_yield, sales_yield, quality_value, profit_revenue_gap, ret_20d, ret_60d, realized_vol_20d

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合价值/成长风格因子"的置信度
- rationale: 中文简述你的判断依据
- recommended_features: 你推荐使用的特征名称列表
- avoid_features: 你建议避免使用的特征名称列表
- risk_warnings: 风险警示列表（中文）
- suggested_factor_types: 建议的因子类型列表（中文）"""

_ANALYST_PROMPTS: dict[str, str] = {
    "trend_momentum": _TREND_MOMENTUM_SYSTEM,
    "reversal_mean_reversion": _REVERSAL_SYSTEM,
    "volatility_risk": _VOLATILITY_SYSTEM,
    "volume_price": _VOLUME_PRICE_SYSTEM,
    "microstructure": _MICROSTRUCTURE_SYSTEM,
    "chip_distribution": _CHIP_DISTRIBUTION_SYSTEM,
    "fundamental_value_growth": _FUNDAMENTAL_VALUE_GROWTH_SYSTEM,
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

    available_features = list(feature_names)
    available_set = set(available_features)
    focus_features = [name for name in _ANALYST_FEATURE_GROUPS.get(agent_id, []) if name in available_set]
    other_features = [name for name in available_features if name not in set(focus_features)]

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

    return {
        "说明": "所有公式只能使用 available_features 中的特征；focus_features 是当前分析师视角下优先关注的特征，other_features 可作为辅助过滤、风险约束或交叉项。每个特征包含 description（业务含义）和 expr（计算公式）。",
        "available_features": _enrich(available_features),
        "focus_features": _enrich(focus_features),
        "other_features": _enrich(other_features),
    }


def _build_analyst_messages(agent_id: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """构建单个分析师的 messages。"""
    system_prompt = _ANALYST_PROMPTS.get(agent_id, "") + _COMMON_ANALYST_SYSTEM_APPENDIX
    feature_context = _build_feature_context(agent_id, list(context.get("feature_names", [])))
    summary_context = build_summary_context(dict(context.get("llm_summary", {})))
    summary_context["数据"]["previous_top"] = context.get("previous_top", [])
    summary_context["数据"]["previous_skipped"] = context.get("previous_skipped", [])
    market_context = build_market_context(dict(context.get("market_context", {})))
    user_content = f"""请基于以下信息，从您的专业视角分析当前市场环境，并给出本轮因子设计建议。

【可用特征列表及当前分析师重点特征】
{json.dumps(feature_context, ensure_ascii=False)}

【特征体检报告摘要及字段说明】
{json.dumps(summary_context, ensure_ascii=False)}

【当前市场环境及字段说明】
{json.dumps(market_context, ensure_ascii=False)}

请严格按照以下 JSON Schema 输出（只输出 JSON，不要 Markdown 代码块）：
{{
  "recommendation_score": 0.0-1.0,
  "rationale": "中文简述",
  "recommended_features": ["特征名1", "特征名2"],
  "avoid_features": ["特征名3"],
  "risk_warnings": ["风险1", "风险2"],
  "suggested_factor_types": ["因子类型1", "因子类型2"]
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
