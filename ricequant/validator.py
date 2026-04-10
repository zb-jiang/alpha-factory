"""
米筐平台策略生成器
根据 Qlib 回测结果和 backtest_rule.yaml 生成米筐平台可用的策略回测代码
"""

import json
import yaml
import re
from pathlib import Path
from typing import Dict, List, Any


class RicequantStrategyGenerator:
    """米筐策略生成器"""
    
    def __init__(self):
        self.backtest_rules = {}
        self.qlib_results = {}
        
        # Qlib 特征到米筐的映射（与 factor_converter.py 保持一致）
        self.feature_mapping = {
            # 收益类
            "ret_5d": ("close_prices", "close_prices[-1] / close_prices[-6] - 1", 6),
            "ret_20d": ("close_prices", "close_prices[-1] / close_prices[-21] - 1", 21),
            "ret_60d": ("close_prices", "close_prices[-1] / close_prices[-61] - 1", 61),
            
            # 成交量类
            "volume_mean_5d": ("volumes", "volumes[-5:].mean()", 5),
            "volume_mean_20d": ("volumes", "volumes[-20:].mean()", 20),
            "volume_ratio_5d": ("volumes", "volumes[-1] / volumes[-5:].mean()", 5),
            
            # 波动率类
            "volatility_20d": ("close_prices", "close_prices.pct_change().std()", 21),
            "volatility_60d": ("close_prices", "close_prices.pct_change().std()", 61),
            
            # 价格位置类
            "price_pos_20d": ("close_prices", "(close_prices[-1] - close_prices[-20:].min()) / (close_prices[-20:].max() - close_prices[-20:].min())", 20),
            "price_pos_60d": ("close_prices", "(close_prices[-1] - close_prices[-60:].min()) / (close_prices[-60:].max() - close_prices[-60:].min())", 60),
            
            # 回撤类
            "max_drawdown_20d": ("close_prices", "(close_prices[-1] - close_prices[-20:].max()) / close_prices[-20:].max()", 20),
            "max_drawdown_60d": ("close_prices", "(close_prices[-1] - close_prices[-60:].max()) / close_prices[-60:].max()", 60),
        }
        
        # 算子映射
        self.operator_mapping = {
            "zscore": "self._zscore",
            "rank": "self._rank",
            "minmax": "self._minmax",
            "abs": "abs",
            "log": "np.log",
            "sqrt": "np.sqrt",
            "sign": "np.sign",
            "rolling_max": "self._rolling_max",
            "rolling_min": "self._rolling_min",
            "clip": "np.clip",
        }
    
    def load_backtest_rules(self, rules_path: Path) -> Dict[str, Any]:
        """加载 Qlib 回测规则"""
        with open(rules_path, 'r', encoding='utf-8') as f:
            self.backtest_rules = yaml.safe_load(f)
        print(f"加载回测规则: {rules_path}")
        return self.backtest_rules
    
    def load_qlib_results(self, results_path: Path) -> Dict[str, Any]:
        """加载 Qlib 回测结果"""
        with open(results_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        top3 = data.get("top3", [])
        for factor in top3:
            factor_name = factor.get("factor_name")
            self.qlib_results[factor_name] = factor
        
        print(f"加载了 {len(self.qlib_results)} 个因子结果")
        return self.qlib_results
    
    def _generate_factor_calculation(self, formula: str) -> tuple:
        """
        根据 Qlib 公式生成米筐因子计算代码
        
        Returns:
            (factor_calculation_code, max_window)
        """
        # 解析公式中的特征和算子
        max_window = 20  # 默认窗口
        calculation_lines = []
        
        # 提取所有特征
        features_used = set()
        for feature_name in self.feature_mapping.keys():
            if feature_name in formula:
                features_used.add(feature_name)
                feature_info = self.feature_mapping[feature_name]
                max_window = max(max_window, feature_info[2])
        
        # 生成特征计算代码
        for feature_name in features_used:
            feature_info = self.feature_mapping[feature_name]
            var_name = feature_name.replace("_", "_")
            calculation_lines.append(f"# {feature_name}")
            calculation_lines.append(f"{var_name} = {feature_info[1]}")
        
        # 处理算子
        formula_code = formula
        for feature_name in features_used:
            var_name = feature_name.replace("_", "_")
            formula_code = formula_code.replace(feature_name, var_name)
        
        # 处理 zscore 算子
        if "zscore(" in formula_code:
            formula_code = re.sub(r'zscore\(([^)]+)\)', r'(\1 - np.mean([\1])) / np.std([\1])', formula_code)
        
        # 处理 rank 算子
        if "rank(" in formula_code:
            formula_code = re.sub(r'rank\(([^)]+)\)', r'pd.Series([\1]).rank().iloc[-1]', formula_code)
        
        # 生成最终计算代码
        if calculation_lines:
            calculation_code = "\n        ".join(calculation_lines)
            calculation_code += f"\n        \n        # 计算最终因子值\n        factor_value = {formula_code}"
        else:
            calculation_code = f"factor_value = {formula_code}"
        
        return calculation_code, max_window
    
    def generate_strategy_code(self, factor_name: str, factor_data: Dict[str, Any]) -> str:
        """
        生成米筐策略代码
        
        生成的代码符合米筐平台标准格式，配置通过界面设置
        """
        formula = factor_data.get("formula", "")
        direction = factor_data.get("direction", "higher_better")
        reason = factor_data.get("reason", "")
        
        # 从 backtest_rule.yaml 读取配置
        rebalance = self.backtest_rules.get("rebalance", "weekly")
        holding_count = self.backtest_rules.get("holding_count", 20)
        buy_top_n = self.backtest_rules.get("buy_top_n", 20)
        sell_drop_to = self.backtest_rules.get("sell_drop_to", 40)
        weight_mode = self.backtest_rules.get("weight_mode", "equal_weight")
        stock_universe = self.backtest_rules.get("stock_universe", "all")
        
        # 生成调仓逻辑
        rebalance_logic = self._generate_rebalance_logic(rebalance)
        
        # 生成排序逻辑
        sort_reverse = "True" if direction == "higher_better" else "False"
        
        # 生成权重逻辑
        weight_logic = self._generate_weight_logic(weight_mode)
        
        # 生成因子计算代码
        factor_calculation_code, max_window = self._generate_factor_calculation(formula)
        
        code = f'''# 米筐平台策略代码
# 
# 因子名称: {factor_name}
# Qlib 公式: {formula}
# 因子逻辑: {reason}
# 因子方向: {direction}
#
# 【请在右侧界面设置以下参数】
# 回测日期: 2018-01-01 至 2022-12-31
# 初始资金: 1000000
# 基准合约: 000300.XSHG
# 调仓频率: {rebalance}
# 佣金倍率: 1
# 滑点: 0

# 策略参数（与 backtest_rule.yaml 对应）
HOLDING_COUNT = {holding_count}      # 持仓数量
BUY_TOP_N = {buy_top_n}              # 买入前N名
SELL_DROP_TO = {sell_drop_to}        # 跌出前N名卖出
DIRECTION = "{direction}"            # 因子方向


def init(context):
    """
    策略初始化
    在回测开始前调用一次
    """
    # 设置策略参数
    context.holding_count = HOLDING_COUNT
    context.buy_top_n = BUY_TOP_N
    context.sell_drop_to = SELL_DROP_TO
    context.direction = DIRECTION
    
    # 调仓计数器（用于控制调仓频率）
    context.rebalance_counter = 0
    
    # 打印策略信息
    print(f"策略初始化完成: {factor_name}")
    print(f"因子公式: {formula}")
    print(f"调仓频率: {rebalance}")


def before_trading(context):
    """
    每日开盘前调用
    """
    pass


def handle_bar(context, bar_dict):
    """
    每个 bar 调用（日线策略每天调用一次）
    """
    {rebalance_logic}


def should_rebalance(context):
    """
    判断是否满足调仓条件
    """
    {self._generate_rebalance_condition(rebalance)}


def get_stock_pool(context):
    """
    获取股票池
    """
    # 获取全市场股票
    stocks = get_index_stocks('000300.XSHG')  # 默认沪深300
    
    # 过滤ST和停牌
    stock_pool = []
    for stock in stocks:
        try:
            # 跳过ST股票
            if is_st_stock(stock):
                continue
            # 跳过停牌股票
            if is_suspended(stock):
                continue
            stock_pool.append(stock)
        except:
            continue
    
    return stock_pool


def calculate_factor(context, stock):
    """
    计算单个股票的因子值
    
    根据 Qlib 公式自动生成: {formula}
    """
    try:
        # 获取历史数据
        close_prices = history(stock, 'close', {max_window}, '1d', skip_paused=True)
        volumes = history(stock, 'volume', {max_window}, '1d', skip_paused=True)
        
        if len(close_prices) < {max_window} or len(volumes) < {max_window}:
            return None
        
        # 计算因子值
        {factor_calculation_code}
        
        return factor_value
    except:
        return None


def get_factor_values(context, stock_pool):
    """
    计算所有股票的因子值
    """
    factor_values = {{}}
    
    for stock in stock_pool:
        value = calculate_factor(context, stock)
        if value is not None:
            factor_values[stock] = value
    
    return factor_values


def rebalance_portfolio(context):
    """
    执行调仓
    """
    # 1. 获取股票池
    stock_pool = get_stock_pool(context)
    
    # 2. 计算因子值
    factor_values = get_factor_values(context, stock_pool)
    
    if len(factor_values) == 0:
        print("没有有效的因子值，跳过调仓")
        return
    
    # 3. 根据因子值排序
    sorted_stocks = sorted(
        factor_values.items(), 
        key=lambda x: x[1], 
        reverse={sort_reverse}
    )
    
    # 4. 选出目标股票（买入前 buy_top_n）
    target_stocks = [stock for stock, value in sorted_stocks[:context.buy_top_n]]
    
    # 5. 获取当前持仓
    current_positions = list(context.portfolio.positions.keys())
    
    # 6. 计算需要卖出的股票（跌出 sell_drop_to 名）
    top_stocks = set([stock for stock, value in sorted_stocks[:context.sell_drop_to]])
    
    # 7. 卖出不在目标列表中的股票
    for stock in current_positions:
        if stock not in target_stocks:
            order_target_value(stock, 0)
    
    # 8. 买入目标股票
    {weight_logic}
    
    print(f"调仓完成，持仓数量: {{len(target_stocks)}}")


# 辅助函数
def _rolling_max(series, window):
    """滚动最大值"""
    return series.rolling(window=window).max()


def _rolling_min(series, window):
    """滚动最小值"""
    return series.rolling(window=window).min()


def is_st_stock(stock):
    """判断是否为ST股票"""
    try:
        info = get_security_info(stock)
        return info.is_st if hasattr(info, 'is_st') else False
    except:
        return False


def is_suspended(stock):
    """判断股票是否停牌"""
    try:
        return is_suspended(stock)
    except:
        return True
'''
        return code
    
    def _generate_rebalance_logic(self, rebalance: str) -> str:
        """生成调仓逻辑代码"""
        if rebalance == "daily":
            return """# 每日调仓
    context.rebalance_counter += 1
    if context.rebalance_counter >= 1:
        context.rebalance_counter = 0
        rebalance_portfolio(context)"""
        elif rebalance == "weekly":
            return """# 每周调仓（周一）
    if context.current_dt.weekday() == 0:  # 周一
        rebalance_portfolio(context)"""
        elif rebalance == "monthly":
            return """# 每月调仓（第一个交易日）
    if context.current_dt.day <= 5:  # 每月前5天
        rebalance_portfolio(context)"""
        else:
            return """# 默认每日调仓
    rebalance_portfolio(context)"""
    
    def _generate_rebalance_condition(self, rebalance: str) -> str:
        """生成调仓条件判断"""
        if rebalance == "daily":
            return "return True  # 每日调仓"
        elif rebalance == "weekly":
            return "return context.current_dt.weekday() == 0  # 周一调仓"
        elif rebalance == "monthly":
            return "return context.current_dt.day <= 5  # 每月前5天调仓"
        else:
            return "return True"
    
    def _generate_weight_logic(self, weight_mode: str) -> str:
        """生成权重分配逻辑"""
        if weight_mode == "equal_weight":
            return """# 等权重分配
    if len(target_stocks) > 0:
        weight_per_stock = context.portfolio.total_value / len(target_stocks)
        for stock in target_stocks:
            order_target_value(stock, weight_per_stock)"""
        else:
            return """# 等权重分配（默认）
    if len(target_stocks) > 0:
        weight_per_stock = context.portfolio.total_value / len(target_stocks)
        for stock in target_stocks:
            order_target_value(stock, weight_per_stock)"""
    
    def generate_strategy_files(self, output_dir: Path) -> List[str]:
        """为所有因子生成策略文件"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        strategy_files = []
        
        for factor_name, factor_data in self.qlib_results.items():
            strategy_code = self.generate_strategy_code(factor_name, factor_data)
            strategy_file = output_dir / f"{factor_name}_strategy.py"
            
            with open(strategy_file, 'w', encoding='utf-8') as f:
                f.write(strategy_code)
            
            strategy_files.append(str(strategy_file))
            print(f"✓ 已生成策略: {factor_name}")
        
        return strategy_files
    
    def generate_summary_report(self, output_dir: Path) -> str:
        """生成策略汇总报告"""
        rebalance = self.backtest_rules.get("rebalance", "weekly")
        holding_count = self.backtest_rules.get("holding_count", 20)
        buy_top_n = self.backtest_rules.get("buy_top_n", 20)
        sell_drop_to = self.backtest_rules.get("sell_drop_to", 40)
        buy_cost = self.backtest_rules.get("buy_cost", 0.0015)
        sell_cost = self.backtest_rules.get("sell_cost", 0.0025)
        
        report = f"""# 米筐平台策略汇总报告

## 回测规则（来自 backtest_rule.yaml）

| 参数 | 值 | 说明 |
|------|-----|------|
| 调仓频率 | {rebalance} | 每周/每日/每月调仓 |
| 持仓数量 | {holding_count} | 最多持有股票数量 |
| 买入规则 | 前 {buy_top_n} 名 | 买入排名前 N 的股票 |
| 卖出规则 | 跌出前 {sell_drop_to} 名 | 跌出前 N 名则卖出 |
| 买入手续费 | {buy_cost} | 千分之 {buy_cost*1000:.1f} |
| 卖出手续费 | {sell_cost} | 千分之 {sell_cost*1000:.1f} |

## 生成的策略文件

"""
        
        for factor_name, factor_data in self.qlib_results.items():
            report += f"""### {factor_name}

- 公式: `{factor_data.get('formula', '')}`
- 方向: {factor_data.get('direction', 'higher_better')}
- 逻辑: {factor_data.get('reason', '')}

"""
        
        report += """## 使用步骤

1. 打开米筐平台的"策略回测"页面
2. 复制生成的策略代码到左侧编辑器
3. 在右侧界面设置回测参数：
   - 回测日期: 2018-01-01 至 2022-12-31
   - 初始资金: 1000000
   - 基准合约: 000300.XSHG
   - 调仓频率: 根据 backtest_rule.yaml 设置
4. 点击"运行回测"开始验证

## 注意事项

1. `calculate_factor` 函数已根据 Qlib 公式自动生成
2. 确保米筐平台的数据与 Qlib 数据一致
3. 如需调整因子计算逻辑，请修改 `calculate_factor` 函数
"""
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "strategy_summary.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        
        return str(report_file)


def main():
    """主函数"""
    generator = RicequantStrategyGenerator()
    
    # 加载回测规则
    rules_path = Path("d:/test/factor-factory/runtime/config/backtest_rule.yaml")
    if rules_path.exists():
        generator.load_backtest_rules(rules_path)
    else:
        print(f"警告: 未找到回测规则文件: {rules_path}")
        print("将使用默认配置")
    
    # 加载 Qlib 结果
    results_path = Path("d:/test/factor-factory/runtime/outputs/backtest/top3_factors.json")
    if not results_path.exists():
        print(f"错误: 未找到 Qlib 结果文件: {results_path}")
        print("请先运行 Qlib 回测生成 top3_factors.json")
        return
    
    generator.load_qlib_results(results_path)
    
    # 生成输出目录
    output_dir = Path("d:/test/factor-factory/ricequant/strategies")
    
    # 生成策略文件
    print("=" * 60)
    print("生成米筐策略文件")
    print("=" * 60)
    strategy_files = generator.generate_strategy_files(output_dir)
    print(f"\n共生成 {len(strategy_files)} 个策略文件")
    
    # 生成汇总报告
    report_file = generator.generate_summary_report(output_dir)
    print(f"\n生成汇总报告: {report_file}")
    
    print("\n" + "=" * 60)
    print("下一步操作:")
    print("=" * 60)
    print("1. 查看生成的策略文件")
    print("2. 根据因子公式完善 calculate_factor 函数")
    print("3. 在米筐平台运行策略回测")
    print("4. 对比 Qlib 和米筐的回测结果")


if __name__ == "__main__":
    main()
