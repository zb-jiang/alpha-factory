from __future__ import annotations

import ast
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = RUNTIME_ROOT / "config"
DATA_DIR = RUNTIME_ROOT / "data"
OUTPUT_DIR = RUNTIME_ROOT / "outputs"
SRC_DIR = RUNTIME_ROOT / "src"


def ensure_runtime_dirs() -> None:
    for path in [
        DATA_DIR / "qlib_cn",
        OUTPUT_DIR / "health",
        OUTPUT_DIR / "llm",
        OUTPUT_DIR / "backtest",
        OUTPUT_DIR / "iter_01",
        OUTPUT_DIR / "iter_02",
        OUTPUT_DIR / "iter_03",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_env(val) for key, val in value.items()}
    return value


def load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return _substitute_env(payload)


def env_config() -> dict[str, Any]:
    return load_yaml_file(CONFIG_DIR / "env.yaml")


def feature_pool_config() -> dict[str, Any]:
    return load_yaml_file(CONFIG_DIR / "feature_pool.yaml")


def backtest_rule_config() -> dict[str, Any]:
    return load_yaml_file(CONFIG_DIR / "backtest_rule.yaml")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        frame.to_csv(path, index=True)
        return
    if path.suffix == ".parquet":
        frame.to_parquet(path, index=True)
        return
    raise ValueError(f"Unsupported table format: {path}")


def _qlib_region(region: str) -> str:
    from qlib.constant import REG_CN, REG_US

    return REG_CN if str(region).lower() == "cn" else REG_US


def init_qlib(config: dict[str, Any] | None = None) -> dict[str, Any]:
    import qlib

    cfg = config or env_config()
    provider_uri = str(cfg.get("provider_uri", "~/.qlib/qlib_data/cn_data"))
    Path(provider_uri).mkdir(parents=True, exist_ok=True)
    qlib.init(
        provider_uri=provider_uri,
        region=_qlib_region(str(cfg.get("region", "cn"))),
        kernels=1,
    )
    return cfg


def get_data_provider(config: dict[str, Any] | None = None) -> "BaseDataProvider":
    """获取数据提供者实例
    
    根据配置中的 data_source 字段创建对应的数据提供者。
    支持 qlib 和 ricequant 两种数据源。
    
    Args:
        config: 配置字典，如果为 None 则加载默认配置
        
    Returns:
        数据提供者实例
        
    示例:
        >>> provider = get_data_provider()
        >>> provider.initialize()
        >>> instruments = provider.get_instruments()
    """
    from data_provider import ProviderFactory
    
    cfg = config or env_config()
    return ProviderFactory.create_provider(cfg)


def init_data_source(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """初始化数据源（支持 Qlib 和米筐）
    
    根据配置自动选择并初始化对应的数据源。
    
    Args:
        config: 配置字典
        
    Returns:
        配置字典
    """
    cfg = config or env_config()
    data_source = cfg.get("data_source", "qlib")
    
    if data_source == "ricequant":
        # 初始化米筐
        provider = get_data_provider(cfg)
        provider.initialize()
    else:
        # 默认使用 Qlib
        init_qlib(cfg)
    
    return cfg


def list_instruments(config: dict[str, Any] | None = None) -> list[str]:
    cfg = config or env_config()
    
    # 获取数据源类型
    data_source = cfg.get("data_source", "qlib")
    
    # 检查是否使用新的股票池管理器
    stock_pool_config = cfg.get("stock_pool", {})
    if stock_pool_config:
        try:
            from stock_pool_manager import create_stock_pool_manager, validate_stock_pool_config
            
            is_valid, message = validate_stock_pool_config(cfg)
            if not is_valid:
                print(f"股票池配置配置验证失败: {message}")
                print("使用默认的全市场股票池")
            else:
                manager = create_stock_pool_manager(cfg)
                
                run_mode = cfg.get("run_mode", "train")
                if run_mode == "test":
                    start_time = str(cfg.get("test_start_date"))
                    end_time = str(cfg.get("test_end_date"))
                else:
                    start_time = str(cfg.get("train_start_date"))
                    end_time = str(cfg.get("train_end_date"))
                
                stock_pool = manager.get_stock_pool(start_time, end_time)
                pool_info = manager.get_pool_info()
                print(f"使用股票池: {pool_info['description']}")
                
                return stock_pool
                
        except ImportError:
            print("股票池管理器未找到，使用默认方法")
        except Exception as e:
            print(f"股票池管理器出错: {e}，使用默认方法")
    
    # 根据数据源类型选择获取方式
    run_mode = cfg.get("run_mode", "train")
    if run_mode == "test":
        start_time = str(cfg.get("test_start_date"))
        end_time = str(cfg.get("test_end_date"))
    else:
        start_time = str(cfg.get("train_start_date"))
        end_time = str(cfg.get("train_end_date"))
    
    if data_source == "qlib":
        # 使用 Qlib 本地数据
        init_qlib(cfg)
        from qlib.data import D
        
        instruments = D.instruments(market=str(cfg.get("market", "all")))
        codes = D.list_instruments(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            as_list=True,
        )
    else:
        # 使用数据提供者（Tushare、米筐等）
        provider = get_data_provider(cfg)
        provider.initialize()
        codes = provider.get_instruments()
        print(f"从 {data_source} 获取股票列表: {len(codes)} 只")
    
    # 只有在没有配置 stock_pool 时才使用 max_instruments
    max_instruments = int(cfg.get("max_instruments", 0) or 0)
    if max_instruments > 0:
        print(f"使用 max_instruments 限制: {max_instruments} 只股票（按字母顺序）")
        return codes[:max_instruments]
    
    return codes


def raw_field_tokens(raw_fields: list[str]) -> list[str]:
    return [field if field.startswith("$") else f"${field}" for field in raw_fields]


def load_raw_data(
    config: dict[str, Any] | None = None,
    raw_fields: list[str] | None = None,
) -> pd.DataFrame:
    cfg = config or env_config()
    fp_cfg = feature_pool_config()
    fields = raw_fields or list(fp_cfg.get("raw_fields", []))
    
    # 获取数据源类型
    data_source = cfg.get("data_source", "qlib")
    
    run_mode = cfg.get("run_mode", "train")
    if run_mode == "test":
        start_time = str(cfg.get("test_start_date"))
        end_time = str(cfg.get("test_end_date"))
    else:
        start_time = str(cfg.get("train_start_date"))
        end_time = str(cfg.get("train_end_date"))
    
    instruments = list_instruments(cfg)
    
    if data_source == "qlib":
        # 使用 Qlib 本地数据
        init_qlib(cfg)
        from qlib.data import D
        
        frame = D.features(
            instruments=instruments,
            fields=raw_field_tokens(fields),
            start_time=start_time,
            end_time=end_time,
            freq=str(cfg.get("freq", "day")),
        )
    else:
        # 使用数据提供者（Tushare、米筐等）
        provider = get_data_provider(cfg)
        provider.initialize()
        
        frame = provider.get_features(
            instruments=instruments,
            fields=raw_field_tokens(fields),
            start_date=start_time,
            end_date=end_time,
            freq=str(cfg.get("freq", "day")),
        )
    
    frame = frame.sort_index()
    frame.columns = [column.replace("$", "") for column in frame.columns]
    
    # 统一索引名称（支持单索引和 MultiIndex）
    if isinstance(frame.index, pd.MultiIndex):
        # MultiIndex: 设置为 (instrument, datetime)
        if len(frame.index.names) == 2:
            frame.index = frame.index.set_names(["instrument", "datetime"])
    else:
        # 单索引: 尝试转换为 MultiIndex
        if frame.index.name is None or frame.index.name == "":
            frame.index.name = "datetime"
    
    return frame


def _compute_group_features(group: pd.DataFrame, base_features: list[dict[str, str]]) -> pd.DataFrame:
    local = group.droplevel("instrument").copy()
    env = {column: local[column] for column in local.columns}
    values: dict[str, pd.Series] = {}
    for item in base_features:
        name = str(item["name"])
        expr = str(item["expr"])
        values[name] = eval(expr, {"__builtins__": {}}, env)
        env[name] = values[name]
    result = pd.DataFrame(values, index=local.index)
    result.index.name = "datetime"
    result["instrument"] = group.index.get_level_values("instrument")[0]
    result = result.set_index("instrument", append=True).reorder_levels(["instrument", "datetime"])
    return result


def build_feature_frame(
    raw_frame: pd.DataFrame,
    config: dict[str, Any] | None = None,
    feature_cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    cfg = config or env_config()
    fp_cfg = feature_cfg or feature_pool_config()
    base_features = list(fp_cfg.get("base_features", []))
    frames = []
    for _, group in raw_frame.groupby(level="instrument", sort=False):
        frames.append(_compute_group_features(group, base_features))
    feature_frame = pd.concat(frames).sort_index()
    horizon = int(cfg.get("label_horizon", 5))
    close = raw_frame["close"]
    label = close.groupby(level="instrument").shift(-horizon) / close - 1
    feature_frame[str(cfg.get("target_label", "future_5d_return"))] = label
    return feature_frame


def yearly_stability(series: pd.Series, label: pd.Series) -> float:
    merged = pd.concat([series, label], axis=1, keys=["feature", "label"]).dropna()
    if merged.empty:
        return 0.0
    merged["year"] = merged.index.get_level_values("datetime").year
    yearly_corr = merged.groupby("year").apply(lambda frame: frame["feature"].corr(frame["label"]))
    yearly_corr = yearly_corr.dropna()
    if yearly_corr.empty:
        return 0.0
    volatility = float(yearly_corr.std(ddof=0) or 0.0)
    return float(1 / (1 + volatility))


def compute_feature_stats(feature_frame: pd.DataFrame, label_name: str) -> pd.DataFrame:
    cfg = env_config()
    max_missing_ratio = float(cfg.get("max_missing_ratio", 0.2))

    rows = []
    label = feature_frame[label_name]
    for column in feature_frame.columns:
        if column == label_name:
            continue
        series = feature_frame[column]
        rows.append(
            {
                "feature_name": column,
                "missing_ratio": float(series.isna().mean()),
                "std": float(series.std(ddof=0) or 0.0),
                "label_corr": float(series.corr(label) or 0.0),
                "yearly_stability_score": yearly_stability(series, label),
            }
        )
    stats = pd.DataFrame(rows)
    stats["quality_flag"] = np.where(
        (stats["missing_ratio"] <= max_missing_ratio) & (stats["std"] > 0),
        "ok",
        "review",
    )
    return stats.sort_values("label_corr", ascending=False).reset_index(drop=True)


def compute_feature_corr(feature_frame: pd.DataFrame, label_name: str) -> pd.DataFrame:
    columns = [column for column in feature_frame.columns if column != label_name]
    corr = feature_frame[columns].corr()
    corr.index.name = "feature_name"
    return corr


def build_summary_payload(stats: pd.DataFrame, corr: pd.DataFrame, label_name: str) -> dict[str, Any]:
    cfg = env_config()
    top_k = int(cfg.get("summary_top_k", 3))
    unstable_top_k = int(cfg.get("unstable_top_k", 3))
    high_corr_threshold = float(cfg.get("high_corr_threshold", 0.5))

    # 按照相关系数的绝对值从大到小排序
    stats_abs = stats.copy()
    stats_abs["abs_corr"] = stats_abs["label_corr"].abs()
    stats_sorted = stats_abs.sort_values("abs_corr", ascending=False)
    
    # 提取绝对值最大的前 top_k 个特征（不论正负）
    top_features = stats_sorted.head(top_k)["feature_name"].tolist()
    
    # 提取绝对值最小（最没关系）的前 top_k 个特征
    weak_features = stats_sorted.tail(top_k)["feature_name"].tolist()
    
    unstable_features = stats.nsmallest(unstable_top_k, "yearly_stability_score")["feature_name"].tolist()
    poor_quality = stats.loc[stats["quality_flag"] != "ok", "feature_name"].tolist()
    pairs: list[list[Any]] = []
    seen: set[tuple[str, str]] = set()
    for left in corr.index:
        for right in corr.columns:
            if left == right:
                continue
            key = tuple(sorted((str(left), str(right))))
            if key in seen:
                continue
            value = float(corr.loc[left, right])
            if math.isnan(value):
                continue
            if abs(value) >= high_corr_threshold:
                pairs.append([key[0], key[1], round(value, 6)])
                seen.add(key)
    pairs = sorted(pairs, key=lambda item: abs(item[2]), reverse=True)[:10]
    return {
        "target": label_name,
        "top_features": top_features,
        "weak_features": weak_features,
        "high_corr_pairs": pairs,
        "unstable_features": unstable_features,
        "poor_quality_features": poor_quality,
    }


def parse_json_text(text: str) -> Any:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        return json.loads(object_match.group(1))
    array_match = re.search(r"(\[.*\])", stripped, flags=re.DOTALL)
    if array_match:
        return json.loads(array_match.group(1))
    raise ValueError("无法从大模型输出中提取 JSON")


ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
)


def validate_formula(
    formula: str,
    allowed_features: set[str],
    allowed_operators: set[str],
) -> tuple[bool, str]:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        return False, f"语法错误: {exc}"

    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            return False, f"不支持的语法节点: {type(node).__name__}"
        if isinstance(node, ast.Name):
            name = node.id
            if name not in allowed_features and name not in allowed_operators:
                return False, f"使用了未授权名称: {name}"
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return False, "只允许调用白名单函数"
            if node.func.id not in allowed_operators:
                return False, f"使用了未授权算子: {node.func.id}"
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Add) and "add" not in allowed_operators:
                return False, "当前不允许加法"
            if isinstance(node.op, ast.Sub) and "sub" not in allowed_operators:
                return False, "当前不允许减法"
            if isinstance(node.op, ast.Mult) and "mul" not in allowed_operators:
                return False, "当前不允许乘法"
            if isinstance(node.op, ast.Div) and "div" not in allowed_operators:
                return False, "当前不允许除法"
    return True, "ok"


def rolling_mean(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).mean()
    )


def rolling_std(series: pd.Series, window: int) -> pd.Series:
    win = int(window)
    return series.groupby(level="instrument").transform(
        lambda s: s.rolling(win, min_periods=win).std(ddof=0)
    )


def rank(series: pd.Series) -> pd.Series:
    return series.groupby(level="datetime").rank(pct=True, method="average")


def zscore(series: pd.Series) -> pd.Series:
    def _normalize(group: pd.Series) -> pd.Series:
        std = group.std(ddof=0)
        if pd.isna(std) or std == 0:
            return pd.Series(0.0, index=group.index)
        return (group - group.mean()) / std

    return series.groupby(level="datetime").transform(_normalize)


def minmax(series: pd.Series) -> pd.Series:
    def _normalize(group: pd.Series) -> pd.Series:
        minimum = group.min()
        maximum = group.max()
        if pd.isna(minimum) or pd.isna(maximum) or minimum == maximum:
            return pd.Series(0.0, index=group.index)
        return (group - minimum) / (maximum - minimum)

    return series.groupby(level="datetime").transform(_normalize)


OPERATOR_ENV = {
    "add": lambda left, right: left + right,
    "sub": lambda left, right: left - right,
    "mul": lambda left, right: left * right,
    "div": lambda left, right: left / right.replace(0, np.nan) if isinstance(right, pd.Series) else left / right,
    "abs": lambda x: np.abs(x),
    "log": lambda x: np.log(x.replace(0, np.nan)),
    "sqrt": lambda x: np.sqrt(np.maximum(x, 0)),
    "sign": lambda x: np.sign(x),
    "rolling_mean": rolling_mean,
    "rolling_std": rolling_std,
    "rolling_max": lambda series, window: series.groupby(level="instrument").rolling(window=window, min_periods=1).max().reset_index(level=0, drop=True),
    "rolling_min": lambda series, window: series.groupby(level="instrument").rolling(window=window, min_periods=1).min().reset_index(level=0, drop=True),
    "rank": rank,
    "zscore": zscore,
    "minmax": minmax,
    "clip": lambda series, lower, upper: series.clip(lower=lower, upper=upper),
}


def evaluate_formula(formula: str, data_frame: pd.DataFrame) -> pd.Series:
    env = {column: data_frame[column] for column in data_frame.columns}
    env.update(OPERATOR_ENV)
    result = eval(formula, {"__builtins__": {}}, env)
    if not isinstance(result, pd.Series):
        raise TypeError("公式执行结果不是 pandas Series")
    return result


def factor_metrics_from_series(
    factor_name: str,
    factor_series: pd.Series,
    label_series: pd.Series,
) -> dict[str, Any]:
    merged = pd.concat([factor_series.rename("score"), label_series.rename("label")], axis=1).dropna()
    if merged.empty:
        return {
            "factor_name": factor_name,
            "mean_ic": 0.0,
            "mean_rank_ic": 0.0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "positive_ic_ratio": 0.0,
            "coverage": 0.0,
        }
    ic_daily = merged.groupby(level="datetime").apply(lambda frame: frame["score"].corr(frame["label"]))
    rank_ic_daily = merged.groupby(level="datetime").apply(
        lambda frame: frame["score"].corr(frame["label"], method="spearman")
    )
    ic_daily = ic_daily.dropna()
    rank_ic_daily = rank_ic_daily.dropna()
    mean_ic = float(ic_daily.mean() or 0.0) if not ic_daily.empty else 0.0
    mean_rank_ic = float(rank_ic_daily.mean() or 0.0) if not rank_ic_daily.empty else 0.0
    ic_std = float(ic_daily.std(ddof=0) or 0.0) if not ic_daily.empty else 0.0
    rank_ic_std = float(rank_ic_daily.std(ddof=0) or 0.0) if not rank_ic_daily.empty else 0.0
    return {
        "factor_name": factor_name,
        "mean_ic": mean_ic,
        "mean_rank_ic": mean_rank_ic,
        "ic_ir": float(mean_ic / ic_std) if ic_std else 0.0,
        "rank_ic_ir": float(mean_rank_ic / rank_ic_std) if rank_ic_std else 0.0,
        "positive_ic_ratio": float((ic_daily > 0).mean()) if not ic_daily.empty else 0.0,
        "coverage": float(merged["score"].notna().mean()),
    }


def weekly_rebalance_dates(dates: pd.Index) -> list[pd.Timestamp]:
    unique_dates = pd.Index(pd.to_datetime(sorted(pd.unique(dates))))
    periods = unique_dates.to_period("W-MON")
    rebalance = unique_dates.to_series().groupby(periods).first().tolist()
    return [pd.Timestamp(item) for item in rebalance]


def compute_drawdown(nav_series: pd.Series) -> float:
    peak = nav_series.cummax()
    drawdown = nav_series / peak - 1
    return float(drawdown.min() or 0.0)


def annualized_return(nav_series: pd.Series) -> float:
    if len(nav_series) < 2:
        return 0.0
    days = (nav_series.index[-1] - nav_series.index[0]).days
    if days < 1:
        return 0.0
    total = float(nav_series.iloc[-1] / nav_series.iloc[0])
    return float(total ** (365.25 / days) - 1)


def sharpe_ratio(period_returns: pd.Series) -> float:
    std = float(period_returns.std(ddof=0) or 0.0)
    if std == 0:
        return 0.0
    return float(period_returns.mean() / std * np.sqrt(52))


def archive_iteration_outputs(iteration: int) -> None:
    iter_dir = OUTPUT_DIR / f"iter_{iteration:02d}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    for relative in [
        Path("health") / "feature_stats.csv",
        Path("health") / "feature_corr.csv",
        Path("health") / "llm_summary.json",
        Path("llm") / "raw_response.json",
        Path("llm") / "factors_validated.json",
        Path("llm") / "factors_rejected.json",
        Path("backtest") / "factor_values.parquet",
        Path("backtest") / "factor_metrics.csv",
        Path("backtest") / "strategy_metrics.csv",
        Path("backtest") / "final_score.csv",
        Path("backtest") / "top3_factors.json",
    ]:
        source = OUTPUT_DIR / relative
        if source.exists():
            target = iter_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".parquet":
                pd.read_parquet(source).to_parquet(target, index=True)
            elif source.suffix == ".csv":
                pd.read_csv(source).to_csv(target, index=False)
            else:
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
