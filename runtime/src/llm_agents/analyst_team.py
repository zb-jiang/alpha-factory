"""专业分析师团队：5 个分析师并行执行。

每个分析师从自己的专业视角分析市场和特征，输出本轮因子设计建议。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .agent_runner import AgentConfig, call_llm_agent

ANALYST_AGENT_IDS = [
    "trend_momentum",
    "reversal_mean_reversion",
    "volatility_risk",
    "volume_price",
    "microstructure",
]

# ── System Prompts ──────────────────────────────────────────────────────────

_TREND_MOMENTUM_SYSTEM = """你是趋势动量分析专家。你精通 A 股市场的价格趋势分析、动量策略和趋势跟踪。

你的核心能力：
- 识别市场趋势方向（上行/下行/震荡）和动量持续性
- 评估不同周期动量因子的有效性（短期 1-5 日、中期 20-60 日）
- 分析突破信号和价格位置的预测能力
- 判断趋势动量策略在当前市场环境下的适用性

专业领域：价格趋势、动量持续性、区间突破
经济学逻辑基础：反应不足假说、信息缓慢扩散、趋势跟踪策略

分析时重点关注以下特征：ret_1d, ret_5d, ret_10d, ret_20d, ret_60d, gap_open_ret, momentum_accel_5_20, momentum_accel_20_60, breakout_20d, price_pos_10d, price_pos_20d, price_pos_60d, rebound_20d

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

分析时重点关注以下特征：realized_vol_5d, realized_vol_20d, realized_vol_60d, volatility_ratio_5_20, high_low_range, intraday_range_mean_5d, intraday_range_mean_20d, volume_vol_20d, amount_vol_20d, turnover_vol_20d, market_cap_stability_20d

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

分析时重点关注以下特征：volume_ratio_5d, volume_ratio_20d, volume_mean_5d, volume_mean_20d, volume_mean_60d, volume_trend_5_20, volume_vol_20d, amount_mean_5d, amount_mean_20d, amount_ratio_20d, amount_vol_20d, close_to_vwap, close_to_vwap_mean_5d, high_to_vwap, low_to_vwap, vwap_ret_5d, vwap_ret_20d, price_volume_corr_20d, volume_price_resonance_20d, turnover_mean_5d, turnover_mean_20d, turnover_ratio_20d

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

分析时重点关注以下特征：intraday_ret, gap_open_ret, close_to_high, close_to_low, high_low_range, intraday_range_mean_5d, intraday_range_mean_20d, close_to_vwap, close_to_vwap_mean_5d, high_to_vwap, low_to_vwap

输出要求：
- recommendation_score: 0-1 的浮点数，表示你对"当前市场适合动量因子"的置信度
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
}


def _build_analyst_messages(agent_id: str, context: dict[str, Any]) -> list[dict[str, str]]:
    """构建单个分析师的 messages。"""
    system_prompt = _ANALYST_PROMPTS.get(agent_id, "")
    user_content = f"""请基于以下信息，从您的专业视角分析当前市场环境，并给出本轮因子设计建议。

【可用特征列表】
{json.dumps(context.get("feature_names", []), ensure_ascii=False)}

【可用算子】
{json.dumps(context.get("allowed_operators", []), ensure_ascii=False)}

【特征体检报告摘要】
{json.dumps(context.get("llm_summary", {}), ensure_ascii=False)}

【当前市场环境】
{json.dumps(context.get("market_context", {}), ensure_ascii=False)}

【上一轮表现最好的因子】
{json.dumps(context.get("previous_top", []), ensure_ascii=False)}

【上一轮被淘汰的因子】
{json.dumps(context.get("previous_skipped", []), ensure_ascii=False)}

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

    # 找出有配置的分析师
    configured_agents: list[tuple[str, dict[str, Any]]] = []
    for agent_id in ANALYST_AGENT_IDS:
        cfg = agents_config.get(agent_id)
        if cfg and cfg.get("model") and cfg.get("base_url") and cfg.get("api_key"):
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
            timeout_seconds=float(cfg.get("timeout_seconds", 60.0)),
            max_retries=int(cfg.get("max_retries", 2)),
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
