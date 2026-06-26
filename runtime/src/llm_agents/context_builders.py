"""LLM Agent 共享上下文构建器。

将 summary / market / operator / constraints 等上下文的字段说明集中维护，
避免 analyst_team / generator / reviewer 各自维护导致不一致。
"""
from __future__ import annotations

from typing import Any


_FEATURE_EVIDENCE_FIELD_DESCRIPTIONS_FULL = {
    "feature_name": "特征名称。",
    "description": "特征的业务含义，帮助理解这个特征在市场里代表什么。",
    "expr": "特征的计算公式，便于判断能否与其他特征形成互补。",
    "valid_observations": "该特征与标签同时非空、真正参与统计的股票×日期样本数。",
    "missing_ratio": "缺失比例，越高说明特征可用性越差。",
    "label_corr": "特征与收益标签的整体相关系数，反映线性相关方向和强弱。",
    "mean_rank_ic": "按调仓观察日计算的平均 Rank IC，反映横截面排序能力。",
    "rank_ic_ir": "Rank IC 的稳定性指标，绝对值越大通常越稳。",
    "positive_ic_ratio": "Rank IC 方向正确的比例，越高说明该特征更常在正确方向上工作。",
    "top_quantile_return": "每天按该特征排序后，特征值最高 20% 股票的平均收益。",
    "bottom_quantile_return": "每天按该特征排序后，特征值最低 20% 股票的平均收益。",
    "long_short_return": "top_quantile_return - bottom_quantile_return，衡量高分组相对低分组的收益差。",
    "yearly_stability_score": "按年份看特征-标签关系的稳定性评分，越高越稳。",
    "coverage_ratio": "在可评估样本里，该特征真实参与 Rank IC 统计的覆盖比例。",
    "high_corr_neighbors": "与该特征高度相关的其他特征，格式为 [特征名, 相关系数]；一起使用时要警惕冗余。",
    "poor_quality_flag": "是否属于低质量特征；为 true 时说明缺失率高或波动不足，应谨慎使用。",
    "recommended_focus_fields": "当前分析师最应该优先阅读的证据字段列表，用来减少不必要的信息干扰。",
}

_FEATURE_EVIDENCE_FIELD_DESCRIPTIONS_COMPACT = {
    "feature_name": "特征名",
    "description": "业务含义",
    "expr": "计算公式",
    "valid_observations": "有效样本数",
    "missing_ratio": "缺失率",
    "label_corr": "整体相关系数",
    "mean_rank_ic": "平均 Rank IC",
    "rank_ic_ir": "Rank IC 稳定性",
    "positive_ic_ratio": "方向正确比例",
    "top_quantile_return": "高分组平均收益",
    "bottom_quantile_return": "低分组平均收益",
    "long_short_return": "高低分组收益差",
    "yearly_stability_score": "跨年份稳定性",
    "coverage_ratio": "统计覆盖率",
    "high_corr_neighbors": "高相关邻居特征",
    "poor_quality_flag": "是否低质量",
    "recommended_focus_fields": "建议优先关注字段",
}


def build_summary_context(llm_summary: dict[str, Any]) -> dict[str, Any]:
    """为特征体检报告摘要增加字段说明。"""
    return {
        "字段说明": {
            "target": "当前因子研究的收益标签字段名，也就是各特征要预测的目标。",
            "top_features": "按 |label_corr| 从大到小选出的特征，表示与收益标签线性相关性最强；正负方向需结合具体相关系数或业务逻辑判断。",
            "weak_features": "按 |label_corr| 从小到大选出的特征，表示单独看与收益标签线性相关性很弱；可作为控制变量、惩罚项或分母，不代表完全无用。",
            "high_corr_pairs": "特征之间高度相关的特征对，格式为 [特征A, 特征B, 相关系数]；同时使用时要警惕共线性，优先考虑差值/比值提取增量信息。",
            "unstable_features": "年化稳定性评分最低的特征，表示不同年份的特征-标签相关性波动较大，使用时要谨慎或加入风险过滤。",
            "poor_quality_features": "缺失率过高或标准差为 0 的低质量特征，通常不建议作为核心信号。",
            "fundamental_feature_health": "基本面分析师专用体检信息，单独评估基本面特征与收益标签的相关性，用于区分相对稳定的基本面特征与变化频繁的非基本面特征。",
            "fundamental_feature_health.features": "基本面特征列表。",
            "fundamental_feature_health.features[].label_corr": "该特征与收益标签的整体相关系数。",
            "fundamental_feature_health.features[].top_quantile_return": "每天按该特征排序后，特征值最高 20% 股票的平均收益。",
            "fundamental_feature_health.features[].bottom_quantile_return": "每天按该特征排序后，特征值最低 20% 股票的平均收益。",
            "fundamental_feature_health.features[].long_short_return": "top_quantile_return - bottom_quantile_return，衡量高分组相对低分组的收益差。",
            "fundamental_feature_health.features[].valid_observations": "该特征与标签同时非空、参与统计的股票×日期样本数。",
            "fundamental_feature_health.top_long_short_features": "按 |long_short_return| 大小进行排列，排名前列的基本面特征。",
            "previous_top": "上一轮回测表现较好的因子，可参考成功逻辑，但不要简单复制。",
            "previous_skipped": "上一轮因 IC、ICIR、方向胜率等不达标被淘汰的因子，应避免生成雷同逻辑。",
        },
        "数据": llm_summary,
    }


def build_feature_evidence_context(
    feature_evidence: list[dict[str, Any]],
    focus_fields: list[str] | None = None,
    *,
    compact: bool = False,
) -> dict[str, Any]:
    """为重点特征证据包增加字段说明。"""
    focus_fields = [str(item) for item in (focus_fields or []) if str(item)]
    always_keep = [
        "feature_name",
        "description",
        "expr",
        "high_corr_neighbors",
        "poor_quality_flag",
    ]
    allowed_fields = always_keep + [field for field in focus_fields if field not in always_keep]
    filtered_rows: list[dict[str, Any]] = []
    for row in feature_evidence:
        if not isinstance(row, dict):
            continue
        filtered_rows.append({key: row.get(key) for key in allowed_fields if key in row})
    field_descriptions = (
        _FEATURE_EVIDENCE_FIELD_DESCRIPTIONS_COMPACT
        if compact
        else _FEATURE_EVIDENCE_FIELD_DESCRIPTIONS_FULL
    )
    return {
        "字段说明": field_descriptions,
        "recommended_focus_fields": focus_fields,
        "数据": filtered_rows,
    }


def select_feature_evidence_rows(
    feature_map: dict[str, Any],
    feature_names: list[str],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按名称顺序从 feature_evidence 映射中抽取并去重。"""
    if not isinstance(feature_map, dict):
        return []
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_count = limit if isinstance(limit, int) and limit > 0 else None
    for name in feature_names:
        feature_name = str(name or "").strip()
        if not feature_name or feature_name in seen:
            continue
        row = feature_map.get(feature_name)
        if not isinstance(row, dict):
            continue
        selected.append(dict(row))
        seen.add(feature_name)
        if max_count is not None and len(selected) >= max_count:
            break
    return selected


def build_market_context(market_context: dict[str, Any]) -> dict[str, Any]:
    """为市场环境增加字段说明。

    会剔除 meta 字段（包含 train_start_date / train_end_date / config_source / funding_sources），
    避免 LLM 看到具体年份后调用知识库记忆产生事后偏见。
    """
    # 浅拷贝并去除 meta 字段，避免污染传入对象
    sanitized = {k: v for k, v in market_context.items() if k != "meta"}
    return {
        "字段说明": {
            "train_context": "这是当前样本窗口的市场环境，不是实时行情；用于判断本轮因子更适合趋势、反转、防守、进攻等逻辑。",
            "summary_text": "对 train_context.labels 的自然语言总览，适合先快速建立整体印象，但不能替代后面的分段与稳定性证据。",
            "labels": "结构化市场标签集合。",
            "label_notes": "对每个标签含义与映射依据的补充说明，便于 LLM 理解标签不是拍脑袋得出的。",
            "mapping_explain": "把关键均值、阈值和标签映射关系串起来的详细解释。",
            "stats": "生成这些标签时用到的原始统计值与宏观观测值。",
            "segment_contexts": "把当前样本窗口按时间顺序切成若干段后的标签摘要，用来查看这个样本窗口内部前中后阶段是否发生变化。它通常比总 labels 更接近当前任务真正相关的结构证据，应优先阅读。",
            "segment_contexts[].segment": "分段名称，例如前段/中段/后段。",
            "segment_contexts[].start_date": "该分段起始日期。",
            "segment_contexts[].end_date": "该分段结束日期。",
            "segment_contexts[].trading_days": "该分段覆盖的交易日数量。",
            "segment_contexts[].labels": "该分段自己的完整市场标签，可与总 labels 或其他分段对比，看哪些维度在前中后段发生了变化。",
            "segment_contexts[].stats": "该分段对应的统计值与宏观观测值。",
            "segment_contexts[].summary_text": "该分段的简短自然语言摘要。",
            # `stability_metrics` 按阅读顺序分为三组：先看核心市场结构，再看辅助市场维度，最后看宏观分段稳定性。
            "stability_metrics": "当前样本窗口内部状态切换的稳定性指标，用来区分稳定环境和来回切换环境。",
            "stability_metrics.market_structure_stability": "核心市场结构稳定性，高表示市场主状态轴更稳定。",
            "stability_metrics.core_switch_counts": "核心市场结构维度的切换次数集合，例如 `trend_switches`、`style_switches`，重点看趋势、波动、广度、风格、资金结构是否频繁变脸。",
            "stability_metrics.core_state_paths": "核心市场结构维度去掉连续重复后的状态演变路径，便于看清窗口内部经历了哪些阶段。",
            "stability_metrics.core_switch_density_per_20d": "核心市场结构每 20 个交易日的切换密度，数值越高表示主状态轴越不稳定。",
            "stability_metrics.auxiliary_structure_stability": "辅助市场维度稳定性，高表示流动性和资金细节更稳定。",
            "stability_metrics.aux_switch_counts": "辅助市场维度的切换次数集合，例如 `liquidity_switches`、`northbound_switches`，补充看流动性、分化、北向、两融是否来回变化。",
            "stability_metrics.aux_state_paths": "辅助市场维度去重后的状态演变路径。",
            "stability_metrics.aux_switch_density_per_20d": "辅助市场维度每 20 个交易日的切换密度。",
            "stability_metrics.macro_structure_stability": "宏观分段稳定性，高表示宏观 4 维在前中后段变化较少。",
            "stability_metrics.macro_segment_switch_counts": "宏观 4 维在前段/中段/后段之间的切换次数集合，例如 `rate_segment_switches`，用来衡量宏观阶段是否稳定。",
            "stability_metrics.macro_state_paths": "宏观 4 维按分段压缩后的演变路径，便于看宏观环境是否前中后发生切换。",
            "stability_metrics.composite_stability": "综合市场结构稳定性、辅助稳定性和宏观分段稳定性后的综合稳定性，高表示更稳定。",
            "stability_metrics.summary_text": "对三组稳定性结果的自然语言总结。",
            "temporal_structure_summary_text": "把 segment_contexts 和 stability_metrics 串起来的时间结构摘要，用来快速理解当前样本窗口内部是持续延续还是频繁切换。",
            "labels.trend": "市场方向状态，例如上行、震荡、下行。",
            "labels.volatility": "市场波动水平，例如高、中、低。",
            "labels.liquidity": "市场成交活跃度，例如高、中、低。",
            "labels.dispersion": "个股收益分化程度；分化越高，通常越有利于选股因子拉开差距。",
            "labels.breadth": "上涨覆盖面，例如普涨、分化、普跌。",
            "labels.style": "大小盘风格，例如大盘占优、小盘占优。",
            "labels.northbound": "北向资金方向，例如偏流入、中性、偏流出。",
            "labels.leverage": "两融情绪，例如升温、平稳、降温。",
            "labels.capital_structure": "资金结构组合状态，例如同向进攻、同向防守、外资谨慎、杠杆激进。",
            "labels.rate": "利率方向，例如宽松、收紧、中性。它描述的是当前样本窗口结束附近的利率状态快照，不是整个样本窗口的平均宏观环境。",
            "labels.macro_liquidity": "宏观流动性，例如扩张、收缩、中性。它描述的是当前样本窗口结束附近的宏观流动性快照，不是整个样本窗口的平均宏观环境。",
            "labels.economy": "经济周期，例如扩张、收缩、中性。它描述的是当前样本窗口结束附近的经济状态快照，不是整个样本窗口的平均宏观环境。",
            "labels.inflation": "通胀方向，例如上行、下行、中性。它描述的是当前样本窗口结束附近的通胀状态快照，不是整个样本窗口的平均宏观环境。",
        },
        "数据": sanitized,
    }


def build_operator_context(allowed_operators: list[dict[str, str]]) -> dict[str, Any]:
    """从 feature_pool.yaml 的 allowed_operators 构建带说明的算子上下文。"""
    operators: dict[str, dict[str, str]] = {}
    for item in allowed_operators:
        name = str(item.get("name", ""))
        if not name:
            continue
        operators[name] = {
            "signature": str(item.get("signature", name)),
            "type": str(item.get("type", "未知")),
            "description": str(item.get("description", "")),
        }
    return {
        "字段说明": {
            "signature": "算子的调用方式，如 rolling_mean(series, window)；算术算子也支持中缀写法如 left + right",
            "type": "算子类别：算术（双目四则运算）、逐元素（单目变换）、时序（按单只股票沿时间窗口计算）、横截面（按同一交易日的股票截面计算）",
            "description": "算子的业务含义和注意事项",
        },
        "operators": operators,
    }


def build_constraints_context(constraints: dict[str, Any]) -> dict[str, Any]:
    """为 generation_constraints 增加字段说明。"""
    return {
        "字段说明": {
            "max_call_depth": "算子嵌套最大层数，超过即被拒绝",
            "max_feature_count": "单个公式最多使用的基础特征数量",
            "max_operator_count": "单个公式最多调用的算子次数",
            "max_same_feature_reuse": "同一特征在公式中最多重复使用次数",
            "max_same_operator_reuse": "同一算子在公式中最多重复使用次数",
            "allowed_windows": "时序算子的 window 参数只允许取这些值",
            "forbidden_operator_chains": "禁止的算子嵌套链组合，如 [rank, zscore] 表示 rank 的结果不能再做 zscore",
        },
        "数据": constraints,
    }
