"""LLM Agent 共享上下文构建器。

将 summary / market / operator / constraints 等上下文的字段说明集中维护，
避免 analyst_team / generator / reviewer 各自维护导致不一致。
"""
from __future__ import annotations

from typing import Any


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


def build_market_context(market_context: dict[str, Any]) -> dict[str, Any]:
    """为市场环境增加字段说明。

    会剔除 meta 字段（包含 train_start_date / train_end_date / config_source / funding_sources），
    避免 LLM 看到具体年份后调用知识库记忆产生事后偏见。
    """
    # 浅拷贝并去除 meta 字段，避免污染传入对象
    sanitized = {k: v for k, v in market_context.items() if k != "meta"}
    return {
        "字段说明": {
            "train_context": "当前市场环境，不是实时行情；用于判断本轮因子更适合趋势、反转、防守、进攻等逻辑。",
            "summary_text": "市场环境的自然语言摘要。",
            "labels": "结构化市场标签集合。",
            "labels.trend": "市场方向状态，例如上行、震荡、下行。",
            "labels.volatility": "市场波动水平，例如高、中、低。",
            "labels.liquidity": "市场成交活跃度，例如高、中、低。",
            "labels.dispersion": "个股收益分化程度；分化越高，通常越有利于选股因子拉开差距。",
            "labels.breadth": "上涨覆盖面，例如普涨、分化、普跌。",
            "labels.style": "大小盘风格，例如大盘占优、小盘占优。",
            "labels.northbound": "北向资金方向，例如偏流入、中性、偏流出。",
            "labels.leverage": "两融情绪，例如升温、平稳、降温。",
            "labels.capital_structure": "资金结构组合状态，例如同向进攻、同向防守、外资谨慎、杠杆激进。",
            "labels.rate": "利率方向，例如宽松、收紧、中性。宽松利好成长股，收紧利好价值股。",
            "labels.macro_liquidity": "宏观流动性，例如扩张、收缩、中性。M2扩张利好小盘，收缩利好大盘蓝筹。",
            "labels.economy": "经济周期，例如扩张、收缩、中性。扩张利好周期股，收缩利好防守型资产。",
            "labels.inflation": "通胀方向，例如上行、下行、中性。上行利好上游资源品，下行利好成长股估值。",
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
