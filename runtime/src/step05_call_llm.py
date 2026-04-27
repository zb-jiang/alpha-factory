from __future__ import annotations

import json
import os
import time

from openai import OpenAI

from common import (
    OUTPUT_DIR,
    env_config,
    feature_pool_config,
    label_description,
    label_name,
    parse_json_text,
    read_json,
    write_json,
)


def build_messages(candidate_count_override: int | None = None) -> list[dict[str, str]]:
    config = env_config()
    feature_cfg = feature_pool_config()
    summary = read_json(OUTPUT_DIR / "health" / "llm_summary.json")
    market_context_path = OUTPUT_DIR / "health" / "market_context.json"
    market_context = read_json(market_context_path) if market_context_path.exists() else summary.get("market_context", {})
    train_context = market_context.get("train_context", market_context)
    workflow_state = dict(config.get("workflow_state", {}))
    label_column = label_name(config)
    label_spec = label_description(config)
    
    # 尝试读取上一轮表现最好的因子（若文件存在）
    top_factors_path = OUTPUT_DIR / "backtest" / "top3_factors.json"
    if top_factors_path.exists():
        previous_top_factors = read_json(top_factors_path)
        summary["previous_round_top_factors"] = previous_top_factors
        
    # 尝试读取上一轮因为 IC/胜率 不达标而被淘汰的因子（若文件存在）
    skipped_factors_path = OUTPUT_DIR / "backtest" / "skipped_factors.json"
    if skipped_factors_path.exists():
        skipped_factors = read_json(skipped_factors_path)
        summary["previous_round_skipped_factors"] = skipped_factors
    
    base_feature_names = [item["name"] for item in feature_cfg.get("base_features", [])]
    generation_constraints = feature_cfg.get("generation_constraints", {})
    candidate_count = int(
        candidate_count_override
        if candidate_count_override is not None
        else config.get("llm_candidate_count", 10)
    )
    
    system_prompt = """你是一个顶级的量化交易策略研究员和金融数据科学家。你精通A股市场微观结构、多因子模型、行为金融学以及Alpha挖掘。
你的任务是在一个自动化的AI量化研究系统中，根据机器提供的数据体检报告和可用的数据资产，挖掘出具有强预测能力、低相关性且逻辑严密的全新选股因子（Alpha因子）。

【你的思考原则】
1. 逻辑优先：每个因子必须有坚实的金融学或行为经济学逻辑（如量价背离、均值回复、波动率倒挂等），绝不能是纯粹的无脑数据拼接。
2. 目标一致：以当前收益标签口径（label）作为预测学习目标，以实际交易正向绝对收益（年化收益率 annualized_return）作为最终业务目标。仅仅在统计上（如 IC 值）显著但不赚钱的因子是没有价值的。
3. 避免共线性：体检报告中已经提示了高度相关的特征，在组合新因子时，应尽量寻找低相关性特征的交叉，或者对高相关特征做差值/比值处理提取增量信息。
4. 鲁棒性控制：关注体检报告中的“不稳定特征(unstable_features)”，尽量少用，或者通过非线性变换（如除以波动率）来控制其风险暴露。
5. 表达式规范：只能使用给定的【允许的基础特征】和【允许的算子】，必须保证公式能够被Python直接解析执行。

请严格遵守系统设定的输出格式，以纯JSON格式返回，不要包含任何Markdown代码块标记（如```json），也不要任何额外的解释性对话。

【JSON 输出硬性规范】
1. 所有 key 必须使用双引号；禁止单引号、注释、尾逗号。
2. 括号必须完全配平：{{}} 与 [] 必须一一对应。
3. 只输出 1 个 JSON 对象，且首字符是 {{、末字符是 }}。
4. 因子列表中的每个元素都必须是完整对象，禁止截断。
5. 在输出前请先做一次“自检”：确认可被 Python json.loads 直接解析。"""

    user_prompt = f"""【任务背景】
我们需要你挖掘出 {candidate_count} 个全新的量化选股因子。
当前模型的预测目标是：{label_column}
当前收益标签字段名是：{label_column}
当前收益标签口径是：{label_spec}

【当前流程上下文】
{json.dumps(workflow_state, ensure_ascii=False, indent=2)}
请注意：当前阶段只允许在 discovery 阶段生成新因子。如果后续系统进入 validation 阶段，则应复用 discovery 阶段已经产出的候选因子，而不是重新生成新公式。

【数据资产与限制】
1. 允许使用的基础特征列表：
{json.dumps(base_feature_names, ensure_ascii=False)}

2. 允许使用的数学算子：
{json.dumps(feature_cfg.get("allowed_operators", []), ensure_ascii=False)}
注意：公式中仅允许使用加(+)、减(-)、乘(*)、除(/)等基本算数运算符，以及上述列表中的算子。

3. 生产环境公式约束：
{json.dumps(generation_constraints, ensure_ascii=False, indent=2)}
请注意：以上约束不是建议，而是硬性校验规则。任何不满足规则的公式都会在验证阶段被直接拒绝。

【特征体检与历史经验报告】
以下是系统对当前可用特征在历史数据上的表现总结（llm_summary.json）：
{json.dumps(summary, ensure_ascii=False, indent=2)}

【市场环境（必须使用）】
以下是当前的市场状态描述：
{json.dumps(train_context, ensure_ascii=False, indent=2)}
请你严格基于上述市场状态描述来设计该市场环境下能够获得高收益标签的的因子，并在风险说明中（expected_failure_regime）明确指出该因子最可能失效的市场环境。

【挖掘指南】
- top_features：与目标相关性最强的特征，可作为构建因子的核心基石。
- weak_features：单看相关性极弱，但可能在组合中作为惩罚项或分母（如控制市值、控制换手率）。
- high_corr_pairs：高度共线性的特征对。如果要同时使用它们，强烈建议采用做差或作比值的方式。
- unstable_features：历史表现不稳定的特征。使用时需极其谨慎，或者配以强力的信号过滤。
- previous_round_top_factors（若有）：上一轮迭代中回测表现最好的因子。你可以参考它们的成功逻辑，或者寻找与它们截然不同的正交逻辑以丰富策略库。
- previous_round_skipped_factors（若有）：上一轮由于预测能力弱（IC低）、稳定性差（ICIR低）或方向胜率不足而被自动淘汰的因子。请务必分析它们的失败原因（如逻辑过度拟合、使用了无意义的高波动特征等），并确保本轮不要生成类似或雷同的因子。
- train_context.summary_text：当前市场状态自然语言摘要，可用于确定本轮主攻逻辑（趋势/反转、进攻/防守、风格偏向）。
- train_context.labels：6个离散当前市场环境标签，含义分别是 trend=方向状态（上行/震荡/下行）、volatility=波动水平（高/中/低）、liquidity=成交活跃度（高/中/低）、dispersion=个股分化强弱（高/中/低）、breadth=上涨覆盖面（普涨/分化/普跌）、style=大小盘风格（大盘占优/小盘占优）；用于给每个因子匹配“适用环境”与“失效环境”。
- 优先生成“短公式、少特征、少层级、少重复加工”的因子。宁可简单清晰，也不要为了复杂而复杂。
- 如果两个特征高度相似，不要用多个近似算子反复包装同一信号；优先通过差值、比值、分母惩罚等方式提取增量信息。

【输出格式要求】
你必须返回一个符合以下JSON Schema的纯JSON对象，绝对不能有任何其他字符！
{{
  "factors": [
    {{
      "factor_name": "字符串：因子的英文名称（要求见名知意，如 momentum_vol_ratio_v1）",
      "formula": "字符串：只使用【允许的基础特征】和【允许的算子】组成的Python表达式（例如：'(ret_20d * volume_ratio_5d) / (1 + volatility_20d)'）",
      "fields": ["列表：公式中实际使用到的特征名称，必须属于【允许的基础特征列表】"],
      "direction": "枚举值：'higher_better'(因子值越大越看多) 或 'lower_better'(因子值越小越看多)。这是你对因子经济含义的原始方向假设，系统后续会将其记录为 llm_direction，并再根据样本内数据计算 empirical_direction",
      "reason": "字符串：用中文简述该因子的金融学逻辑或设计意图（例如：'结合了短期动量和成交量放大的共振，同时剔除高波动率股票的风险'）",
      "risk": "字符串：用中文简述该因子在什么市场环境下可能失效（例如：'在市场风格急剧切换或低流动性环境下容易发生回撤'）",
      "expected_failure_regime": "字符串：必须明确写出该因子最可能失效的市场状态（例如：'高波动单边逼空行情'、'极端缩量普跌且离散度很低的环境'）"
    }}
  ]
}}

【最终警告】
1. 挖掘过程中绝对不能使用未来数据（未来函数）！
2. 绝对不能使用不在【允许的基础特征列表】中的未授权字段！
3. 返回结果必须是直接可解析的JSON字典，严禁输出 ```json 等Markdown格式标记！
4. 不要输出 backtest_rule 字段，交易规则由系统自动注入。
5. 再次强调：请在输出前做括号配平检查，确保不存在缺失 `}}` 或 `]` 的情况。
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def run() -> None:
    config = env_config()
    workflow_state = dict(config.get("workflow_state", {}))
    stage = str(workflow_state.get("stage", "discovery"))
    if stage != "discovery":
        raise RuntimeError(f"step05_call_llm 只允许在 discovery 阶段运行，当前阶段是: {stage}")
    api_key = str(config.get("llm_api_key", "")).strip()
    base_url = str(config.get("llm_base_url", "")).strip()
    model = str(config.get("llm_model", "")).strip()
    llm_request_timeout_seconds = max(float(config.get("llm_request_timeout_seconds", 120.0) or 120.0), 1.0)
    llm_max_retries = max(int(config.get("llm_max_retries", 2) or 2), 0)
    llm_retry_wait_seconds = max(float(config.get("llm_retry_wait_seconds", 3.0) or 3.0), 0.0)
    candidate_count = max(int(config.get("llm_candidate_count", 10) or 10), 1)
    llm_min_candidate_count = max(int(config.get("llm_min_candidate_count", 3) or 3), 1)
    
    # 代理设置检查：如果有全局代理导致 SSL 握手失败，可以选择在这里临时清理环境变量
    # os.environ.pop("http_proxy", None)
    # os.environ.pop("https_proxy", None)
    
    if not api_key or not base_url or not model:
        raise RuntimeError("请先在 env.yaml 中配置 llm_api_key、llm_base_url、llm_model")
    
    import httpx
    # 使用自定义 httpx client 关闭系统代理，防止某些梯子软件导致 TLS 握手失败或请求无响应
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("all_proxy", None)
    os.environ.pop("ALL_PROXY", None)
    
    # 强制 httpx 忽略所有代理设置，直接连接外网
    # httpx < 0.24.0 使用 proxies=None，> 0.24.0 使用 proxy=None 或 proxies=None，为兼容性只使用 proxy 且捕获异常
    try:
        http_client = httpx.Client(verify=False, trust_env=False, proxy=None) 
    except TypeError:
        # 如果不支持 proxy（如老版本或特定版本限制），直接不传代理参数
        http_client = httpx.Client(verify=False, trust_env=False)
    
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    messages = build_messages(candidate_count)
    
    print(f"正在调用大模型 ({model})，请稍候...")
    response = None
    content = ""
    last_error: Exception | None = None
    for attempt in range(llm_max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                timeout=llm_request_timeout_seconds,
            )
            content = response.choices[0].message.content or ""
            parsed = parse_json_text(content)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("factors"), list):
                raise ValueError("LLM 返回内容不是包含 factors 列表的 JSON 对象")
            break
        except Exception as error:
            last_error = error
            finish_reason = ""
            if response is not None:
                finish_reason = str(response.choices[0].finish_reason or "")
                invalid_payload = {
                    "model": model,
                    "base_url": base_url,
                    "messages": messages,
                    "content": content,
                    "raw": response.model_dump(),
                    "finish_reason": finish_reason,
                    "error": str(error),
                    "attempt": attempt + 1,
                }
                write_json(OUTPUT_DIR / "llm" / "raw_response_invalid.json", invalid_payload)
            error_text = str(error)
            should_reduce_candidate_count = (
                "Unterminated string" in error_text
                or "Expecting ',' delimiter" in error_text
                or finish_reason == "length"
            )
            if should_reduce_candidate_count and candidate_count > llm_min_candidate_count:
                next_count = max(llm_min_candidate_count, candidate_count // 2)
                if next_count < candidate_count:
                    candidate_count = next_count
                    messages = build_messages(candidate_count)
                    print(
                        f"检测到大模型输出疑似截断，候选因子数调整为 {candidate_count}，"
                        "并继续重试..."
                    )
            if attempt >= llm_max_retries:
                print(f"大模型调用在第 {attempt + 1} 次尝试后失败，停止重试。")
                raise RuntimeError(
                    f"大模型调用失败: model={model}, timeout={llm_request_timeout_seconds}s, "
                    f"retries={llm_max_retries}, error={error}"
                ) from error
            wait_seconds = llm_retry_wait_seconds * (attempt + 1)
            print(
                f"大模型调用异常(第 {attempt + 1}/{llm_max_retries + 1} 次): {error}，"
                f"{wait_seconds:.1f} 秒后重试..."
            )
            time.sleep(wait_seconds)

    if response is None:
        raise RuntimeError(f"大模型调用失败: {last_error}")
    payload = {
        "model": model,
        "base_url": base_url,
        "messages": messages,
        "content": content,
        "raw": response.model_dump(),
    }
    write_json(OUTPUT_DIR / "llm" / "raw_response.json", payload)
    print("llm response saved")


if __name__ == "__main__":
    run()
