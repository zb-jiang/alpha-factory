"""
Qlib 因子到米筐平台的转换器
将 Qlib 因子表达式转换为米筐 RQFactor 格式的 compute() 函数

生成的代码可以直接复制到米筐平台的因子编辑器中运行

注意：特征映射需要与 runtime/config/feature_pool.yaml 保持同步
如果 feature_pool.yaml 新增特征，请在此处添加对应的米筐映射
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any


class QlibToRicequantConverter:
    """Qlib 到米筐因子转换器"""
    
    def __init__(self):
        # Qlib 特征到米筐的映射
        # 与 runtime/config/feature_pool.yaml 中的 base_features 保持同步
        self.feature_mapping = {
            # 收益类
            "ret_5d": "ROCP($close, 5)",
            "ret_20d": "ROCP($close, 20)",
            "ret_60d": "ROCP($close, 60)",
            
            # 成交量类
            "volume_mean_5d": "Mean($volume, 5)",
            "volume_mean_20d": "Mean($volume, 20)",
            "volume_ratio_5d": "$volume / Mean($volume, 5)",
            
            # 波动率类
            "volatility_20d": "Std(ROCP($close, 1), 20)",
            "volatility_60d": "Std(ROCP($close, 1), 60)",
            
            # 价格位置类
            "price_pos_20d": "($close - Min($close, 20)) / (Max($close, 20) - Min($close, 20))",
            "price_pos_60d": "($close - Min($close, 60)) / (Max($close, 60) - Min($close, 60))",
            
            # 回撤类
            "max_drawdown_20d": "($close - Max($close, 20)) / Max($close, 20)",
            "max_drawdown_60d": "($close - Max($close, 60)) / Max($close, 60)",
            
            # 估值类（米筐内置）
            "pe_ratio": "$pe_ratio",
            "pb_ratio": "$pb_ratio",
            "ps_ratio": "$ps_ratio",
            "roe": "$roe",
            "roa": "$roa",
        }
        
        # 算子映射
        # 与 runtime/config/feature_pool.yaml 中的 allowed_operators 保持同步
        self.operator_mapping = {
            "zscore": "ZScore",
            "rank": "Rank",
            "minmax": "MinMax",
            "abs": "Abs",
            "log": "Log",
            "sqrt": "Sqrt",
            "sign": "Sign",
            "rolling_max": "Max",
            "rolling_min": "Min",
            "clip": "Clip",
        }

    def convert_formula(self, formula: str, fields: List[str]) -> str:
        """
        将 Qlib 公式转换为米筐公式
        
        示例:
        Qlib: "rank(price_pos_20d) * rank(volume_ratio_5d)"
        米筐: "Rank(($close - Min($close, 20)) / (Max($close, 20) - Min($close, 20))) * Rank($volume / Mean($volume, 5))"
        """
        ricequant_formula = formula
        
        # 1. 先替换特征（按长度降序，避免短名影响长名）
        sorted_fields = sorted(fields, key=len, reverse=True)
        
        for field in sorted_fields:
            if field in self.feature_mapping:
                ricequant_formula = ricequant_formula.replace(field, self.feature_mapping[field])
        
        # 2. 替换算子
        for qlib_op, rq_op in self.operator_mapping.items():
            pattern = rf'\b{qlib_op}\s*\('
            replacement = f'{rq_op}('
            ricequant_formula = re.sub(pattern, replacement, ricequant_formula)
        
        return ricequant_formula

    def convert_to_ricequant(self, factor_data: Dict[str, Any]) -> str:
        """
        将 Qlib 因子转换为米筐因子代码（compute() 函数格式）
        
        生成的代码可以直接复制到米筐平台的因子编辑器中
        """
        factor_name = factor_data.get("factor_name", "unnamed_factor")
        formula = factor_data.get("formula", "")
        fields = factor_data.get("fields", [])
        direction = factor_data.get("direction", "higher_better")
        reason = factor_data.get("reason", "")
        
        # 转换公式
        ricequant_formula = self.convert_formula(formula, fields)
        
        # 生成米筐代码
        code = f'''from rqfactor import *

# 因子名称: {factor_name}
# 原始 Qlib 公式: {formula}
# 米筐公式: {ricequant_formula}
# 方向: {direction} ({"值越大越好" if direction == "higher_better" else "值越小越好"})
# 逻辑: {reason}

def compute():
    return {ricequant_formula}
'''
        return code

    def convert_factor_file(self, input_path: Path, output_dir: Path) -> List[str]:
        """
        转换单个因子文件
        
        支持两种输入格式:
        1. top3_factors.json: {"top3": [...]}
        2. factors_validated.json: {"factors": [...]}
        """
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 支持多种输入格式
        if "top3" in data:
            factors = data["top3"]
        elif "factors" in data:
            factors = data["factors"]
        else:
            factors = [data]
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        converted_files = []
        
        for factor in factors:
            try:
                code = self.convert_to_ricequant(factor)
                factor_name = factor.get("factor_name", "unnamed")
                output_file = output_dir / f"{factor_name}.py"
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                converted_files.append(str(output_file))
                print(f"✓ 已转换: {factor_name}")
                
            except Exception as e:
                factor_name = factor.get("factor_name", "unnamed")
                print(f"✗ 转换失败 {factor_name}: {e}")
        
        return converted_files


def main():
    """主函数"""
    converter = QlibToRicequantConverter()
    
    # 默认路径
    input_file = Path("d:/test/factor-factory/runtime/outputs/backtest/top3_factors.json")
    output_dir = Path("d:/test/factor-factory/ricequant/converted_factors")
    
    print("=" * 60)
    print("Qlib 到米筐因子转换器")
    print("=" * 60)
    print(f"输入文件: {input_file}")
    print(f"输出目录: {output_dir}")
    print("=" * 60)
    
    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        print("请先运行 Qlib 回测生成 top3_factors.json")
        return
    
    try:
        converted = converter.convert_factor_file(input_file, output_dir)
        print("=" * 60)
        print(f"转换完成! 共转换 {len(converted)} 个因子")
        print("=" * 60)
        print("\n生成的文件:")
        for f in converted:
            print(f"  - {f}")
        print("\n使用方法:")
        print("1. 打开米筐平台的因子编辑器")
        print("2. 复制生成的 .py 文件内容")
        print("3. 粘贴到因子编辑器中并保存")
        print("4. 运行因子检验验证效果")
        
    except Exception as e:
        print(f"转换过程中出现错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
