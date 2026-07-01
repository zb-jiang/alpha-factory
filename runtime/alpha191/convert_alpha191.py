"""Alpha191 公式转换器：将国泰君安 alpha191 的 C 风格公式转为 Python eval 可执行的表达式。"""

import re
import json

# ── 算子名映射 ──
OPERATOR_MAP = {
    "DECAYLINEAR": "decay_linear",
    "HIGHDAY": "highday",
    "LOWDAY": "lowday",
    "COVIANCE": "covariance",
    "SUMIF": "sumif",
    "SUMAC": "sumac",
    "TSMAX": "rolling_max",
    "TSMIN": "rolling_min",
    "TSRANK": "ts_rank",
    "FILTER": "filter_cond",
    "DELAY": "delay",
    "DELTA": "delta",
    "COUNT": "count",
    "CORR": "rolling_corr",
    "RANK": "rank",
    "SUM": "rolling_sum",
    "MEAN": "rolling_mean",
    "STD": "rolling_std",
    "ABS": "abs",
    "LOG": "log",
    "SIGN": "sign",
    "SMA": "sma",
    "WMA": "wma",
    "PROD": "prod",
    "MA": "rolling_mean",
}

# ── 字段名映射（含 alpha191 辅助变量）──
FIELD_MAP = {
    "BANCHMARKINDEXCLOSE": "benchmark_close",
    "BANCHMARKINDEXOPEN": "benchmark_open",
    "BANCHMARKINDEXHIGH": "benchmark_high",
    "BANCHMARKINDEXLOW": "benchmark_low",
    "VOLUME": "volume",
    "AMOUNT": "amount",
    "CLOSE": "close",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "VWAP": "vwap",
    "VOL": "volume",
    "RET": "ret_1d",
    # alpha191 辅助变量（大写 → 小写）
    "DTM": "dtm",
    "DBM": "dbm",
    "LD": "ld",
    "HD": "hd",
    "TR": "tr",
}

# ── 原文拼写错误修正 ──
TYPO_FIX = {
    "DELAT": "delta",
    "HGIH": "high",
    "SMEAN": "rolling_mean",
    "L": "low",  # Alpha52 中的 L 是 LOW 的缩写
}


def find_matching_paren(s: str, start: int) -> int:
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_top_level(s: str, sep: str) -> list[str]:
    parts = []
    depth = 0
    current = ""
    for ch in s:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == sep and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def wrap_comparisons(expr: str) -> str:
    """在 & 和 | 两侧的比较表达式加括号，解决 Python 运算符优先级问题。

    例如：close>open & benchmark_close<benchmark_open
    变成：(close>open) & (benchmark_close<benchmark_open)
    """
    # 找到 depth=0 的 & 和 |
    result = ""
    i = 0
    depth = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "(":
            depth += 1
            result += ch
            i += 1
        elif ch == ")":
            depth -= 1
            result += ch
            i += 1
        elif depth == 0 and ch in "&|":
            # 回溯：找到左侧比较表达式的起点
            left = result.rstrip()
            # 找到左侧最近的比较运算符
            cmp_ops = [">", "<", ">=", "<=", "==", "!="]
            has_cmp = any(op in left for op in cmp_ops)
            if has_cmp and not left.endswith(")"):
                # 检查 left 是否已被括号包裹
                # 找到比较表达式的起点：往左找直到 & | ( 或开头
                start_idx = len(left) - 1
                while start_idx > 0 and left[start_idx - 1] not in "&|(":
                    start_idx -= 1
                left_expr = left[start_idx:].strip()
                left_before = left[:start_idx]
                result = left_before + f"({left_expr})"

            # 前进到 & 或 | 之后，找到右侧比较表达式
            result += ch
            i += 1
            # 找右侧直到下一个 & | 或结尾
            right_start = i
            while i < len(expr) and (expr[i] != "&" and expr[i] != "|" or depth > 0):
                if expr[i] == "(":
                    depth += 1
                elif expr[i] == ")":
                    depth -= 1
                i += 1
            right_expr = expr[right_start:i].strip()
            # 检查右侧是否有比较运算符
            has_cmp_right = any(op in right_expr for op in [">", "<", ">=", "<=", "==", "!="])
            if has_cmp_right and not right_expr.startswith("("):
                result += f"({right_expr})"
            else:
                result += right_expr
        else:
            result += ch
            i += 1
    return result


def convert_ternary(expr: str) -> str:
    """递归转换三元表达式 cond ? a : b → where(cond, a, b)"""
    expr = expr.strip()

    if "?" not in expr:
        return process_inner_parens(expr, convert_ternary)

    depth = 0
    question_pos = -1
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "?" and depth == 0:
            question_pos = i
            break

    if question_pos == -1:
        return process_inner_parens(expr, convert_ternary)

    cond = expr[:question_pos].strip()

    depth = 0
    colon_pos = -1
    for i in range(question_pos + 1, len(expr)):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ":" and depth == 0:
            colon_pos = i
            break

    if colon_pos == -1:
        return process_inner_parens(expr, convert_ternary)

    then_part = expr[question_pos + 1:colon_pos].strip()
    else_part = expr[colon_pos + 1:].strip()

    cond_converted = convert_ternary(cond)
    then_converted = convert_ternary(then_part)
    else_converted = convert_ternary(else_part)

    return f"where({cond_converted}, {then_converted}, {else_converted})"


def process_inner_parens(expr: str, func) -> str:
    result = expr
    i = 0
    while i < len(result):
        if result[i] == "(":
            end = find_matching_paren(result, i)
            if end == -1:
                break
            inner = result[i + 1:end]
            inner_converted = func(inner)
            result = result[:i + 1] + inner_converted + result[end:]
            i = i + 1 + len(inner_converted) + 1
        else:
            i += 1
    return result


def is_integer_gt1(s: str) -> bool:
    s = s.strip()
    try:
        val = int(s)
        return val > 1
    except ValueError:
        return False


def convert_max_min(expr: str) -> str:
    """处理裸 MAX/MIN 歧义（TSMAX/TSMIN 已在算子映射中处理）"""
    result = expr
    changed = True
    while changed:
        changed = False
        for func_name, ts_op, cs_op in [("MAX", "rolling_max", "maximum"),
                                         ("MIN", "rolling_min", "minimum")]:
            pattern = f"{func_name}("
            idx = result.find(pattern)
            while idx != -1:
                # 确保前面不是 TS（避免 TSMAX 被拆开）
                if idx >= 2 and result[idx-2:idx] == "TS":
                    idx = result.find(pattern, idx + 1)
                    continue
                paren_start = idx + len(pattern) - 1
                paren_end = find_matching_paren(result, paren_start)
                if paren_end == -1:
                    break

                inner = result[paren_start + 1:paren_end]
                args = split_top_level(inner, ",")

                if len(args) == 2:
                    arg1 = args[0].strip()
                    arg2 = args[1].strip()
                    if is_integer_gt1(arg2):
                        replacement = f"{ts_op}({arg1}, {arg2})"
                    else:
                        replacement = f"{cs_op}({arg1}, {arg2})"
                elif len(args) == 3:
                    arg1, arg2, arg3 = args[0].strip(), args[1].strip(), args[2].strip()
                    replacement = f"{cs_op}({cs_op}({arg1}, {arg2}), {arg3})"
                else:
                    replacement = result[paren_start:paren_end + 1]

                result = result[:idx] + replacement + result[paren_end + 1:]
                changed = True
                break
            if changed:
                break
    return result


def convert_regbeta(expr: str) -> str:
    """将 REGBETA(y, SEQUENCE(n)) → regbeta_seq(y, n)
    将 REGBETA(y, x, n) → regbeta(y, x, n)（非 SEQUENCE 第二参数）"""
    result = expr
    idx = 0
    while True:
        match = re.search(r"REGBETA\(", result[idx:])
        if not match:
            break

        pos = idx + match.start()
        paren_start = pos + len("REGBETA(") - 1
        paren_end = find_matching_paren(result, paren_start)
        if paren_end == -1:
            idx = pos + 1
            continue

        inner = result[paren_start + 1:paren_end]
        args = split_top_level(inner, ",")

        if len(args) == 2:
            y_expr = args[0].strip()
            seq_expr = args[1].strip()
            seq_match = re.match(r"SEQUENCE\((\d+)\)", seq_expr)
            if seq_match:
                n = seq_match.group(1)
                replacement = f"regbeta_seq({y_expr}, {n})"
            elif seq_expr == "SEQUENCE":
                replacement = f"regbeta_seq({y_expr}, 20)"
            else:
                replacement = f"regbeta({y_expr}, {seq_expr}, 20)"
            result = result[:pos] + replacement + result[paren_end + 1:]
            idx = pos + len(replacement)
            continue
        elif len(args) == 3:
            # REGBETA(y, x, n) 格式
            y_expr, x_expr, n_expr = args[0].strip(), args[1].strip(), args[2].strip()
            replacement = f"regbeta({y_expr}, {x_expr}, {n_expr})"
            result = result[:pos] + replacement + result[paren_end + 1:]
            idx = pos + len(replacement)
            continue

        idx = pos + 1

    return result


def convert_formula(raw: str) -> str:
    expr = raw.strip()

    # 0. 预处理：统一特殊字符
    expr = expr.replace("，", ",")
    expr = expr.replace("（", "(").replace("）", ")")
    expr = expr.replace("–", "-")  # U+2013
    expr = expr.replace("—", "-")  # U+2014

    # 1. MATLAB 风格点运算
    expr = expr.replace("./", "/")
    expr = expr.replace(".*", "*")

    # 2. 逻辑运算符
    expr = expr.replace("&&", "&")
    expr = expr.replace("||", "|")
    expr = re.sub(r"\bOR\b", "|", expr)
    expr = re.sub(r"\bAND\b", "&", expr)

    # 3. 幂运算 ^ → **
    expr = re.sub(r"(?<![<>=!])\^(?![=])", "**", expr)

    # 4. 比较运算 = → ==
    expr = re.sub(r"(?<![<>=!])=(?![=])", "==", expr)

    # 5. 先做算子名映射（在 convert_max_min 之前，避免 TSMAX 被拆开）
    for op_name, op_mapped in sorted(OPERATOR_MAP.items(), key=lambda x: -len(x[0])):
        expr = re.sub(r"\b" + op_name + r"\b", op_mapped, expr)

    # 6. 处理三元表达式（使用 where 而非 np.where）
    expr = convert_ternary(expr)

    # 7. 处理 REGBETA
    expr = convert_regbeta(expr)

    # 8. 处理裸 MAX/MIN
    expr = convert_max_min(expr)

    # 9. 拼写错误修正（在算子映射之后，因为 L 可能被算子映射影响）
    for typo, correct in sorted(TYPO_FIX.items(), key=lambda x: -len(x[0])):
        expr = re.sub(r"\b" + typo + r"\b", correct, expr)

    # 10. 字段名映射
    for field_name, field_mapped in sorted(FIELD_MAP.items(), key=lambda x: -len(x[0])):
        expr = re.sub(r"\b" + field_name + r"\b", field_mapped, expr)

    # 11. 修复 STD(CLOSE:20) 冒号笔误
    expr = re.sub(r"rolling_std\(([^,]+):(\d+)\)", r"rolling_std(\1, \2)", expr)

    # 12. 修复逻辑运算符优先级：在 & 和 | 两侧的比较表达式加括号
    expr = wrap_comparisons(expr)

    # 13. 清理空格
    expr = re.sub(r"\s+", " ", expr).strip()

    return expr


def parse_alpha191_file(filepath: str) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    formulas = []
    pattern = re.compile(r"^Alpha(\d+):\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(content):
        alpha_num = int(match.group(1))
        raw_formula = match.group(2).strip()
        formulas.append({"num": alpha_num, "raw": raw_formula})

    return formulas


def main():
    input_file = r"d:\works\alpha-factory\国泰君安alpha191.txt"
    output_file = r"d:\works\alpha-factory\runtime\alpha191\alpha191_formulas.json"

    formulas = parse_alpha191_file(input_file)
    print(f"解析到 {len(formulas)} 个公式")

    skip = {30, 143}
    results = []
    errors = []

    for item in formulas:
        num = item["num"]
        raw = item["raw"]
        if num in skip:
            print(f"  Alpha{num}: 跳过")
            continue
        try:
            converted = convert_formula(raw)
            results.append({
                "name": f"alpha{num}",
                "formula": converted,
                "direction": 1,
                "raw": raw,
            })
        except Exception as e:
            errors.append({"num": num, "raw": raw, "error": str(e)})
            print(f"  Alpha{num}: 转换失败 - {e}")

    print(f"\n转换成功: {len(results)} 个")
    if errors:
        print(f"转换失败: {len(errors)} 个")

    output = [{"name": r["name"], "formula": r["formula"], "direction": r["direction"]} for r in results]
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"已输出到: {output_file}")

    debug_file = output_file.replace(".json", "_debug.json")
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"调试版本: {debug_file}")


if __name__ == "__main__":
    main()
