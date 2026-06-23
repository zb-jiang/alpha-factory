from __future__ import annotations

import json

from common import (
    OUTPUT_DIR,
    env_config,
    feature_pool_config,
    generation_constraints as _extract_generation_constraints,
    log_step_end,
    log_step_start,
    read_json,
    write_json,
)

from llm_agents import (
    AgentConfig,
    run_analyst_team,
    run_chief_analyst,
    run_generator,
    run_reviewer,
)


def _log(message: str) -> None:
    print(message, flush=True)


def _slice_feature_evidence_by_analyst(feature_evidence: dict) -> dict[str, list[dict]]:
    if not isinstance(feature_evidence, dict):
        return {}
    feature_map = feature_evidence.get("feature_evidence", {})
    focus_by_analyst = feature_evidence.get("focus_features_by_analyst", {})
    if not isinstance(feature_map, dict) or not isinstance(focus_by_analyst, dict):
        return {}

    sliced: dict[str, list[dict]] = {}
    for agent_id, feature_names in focus_by_analyst.items():
        names = feature_names if isinstance(feature_names, list) else []
        sliced[agent_id] = [
            dict(feature_map[name])
            for name in names
            if isinstance(name, str) and name in feature_map and isinstance(feature_map[name], dict)
        ]
    return sliced


def _build_multi_agent_context() -> dict:
    """构建多 Agent 模式所需的统一上下文。"""
    config = env_config()
    feature_cfg = feature_pool_config()
    summary = read_json(OUTPUT_DIR / "health" / "llm_summary.json")
    feature_evidence_path = OUTPUT_DIR / "health" / "llm_feature_evidence.json"
    feature_evidence = read_json(feature_evidence_path) if feature_evidence_path.exists() else {}
    analyst_feature_evidence = _slice_feature_evidence_by_analyst(feature_evidence)
    market_context_path = OUTPUT_DIR / "health" / "market_context.json"
    market_context = (
        read_json(market_context_path)
        if market_context_path.exists()
        else summary.get("market_context", {})
    )
    top_factors_path = OUTPUT_DIR / "backtest" / "top3_factors.json"
    previous_top = read_json(top_factors_path) if top_factors_path.exists() else []
    skipped_factors_path = OUTPUT_DIR / "backtest" / "skipped_factors.json"
    previous_skipped = read_json(skipped_factors_path) if skipped_factors_path.exists() else []

    feature_names = [item["name"] for item in feature_cfg.get("base_features", [])]
    allowed_operators = feature_cfg.get("allowed_operators", [])
    generation_constraints = _extract_generation_constraints(feature_cfg)

    return {
        "feature_names": feature_names,
        "allowed_operators": allowed_operators,
        "generation_constraints": generation_constraints,
        "llm_summary": summary,
        "llm_feature_evidence": feature_evidence,
        "analyst_feature_evidence": analyst_feature_evidence,
        "market_context": market_context,
        "previous_top": previous_top,
        "previous_skipped": previous_skipped,
        "candidate_count": int(config.get("llm_candidate_count", 10)),
    }


def _write_agent_outputs(agent_outputs: list[dict]) -> None:
    """将每个分析师的输出写入单独文件，便于审计。"""
    agent_dir = OUTPUT_DIR / "llm" / "agent_outputs"
    agent_dir.mkdir(parents=True, exist_ok=True)
    for output in agent_outputs:
        agent_id = output.get("agent_id", "unknown")
        write_json(agent_dir / f"{agent_id}.json", output)


def run() -> None:
    """多 Agent 因子挖掘主流程。

    1. 并行运行分析师团队
    2. 运行首席分析师整合
    3. 运行因子生成器
    4. 运行因子评审员
    5. 输出最终 raw_response.json
    """
    log_step_start("05", "调用 LLM 生成因子")
    env_cfg = env_config()

    workflow_state = dict(env_cfg.get("workflow_state", {}))
    stage = str(workflow_state.get("stage", "discovery"))
    if stage != "discovery":
        raise RuntimeError(f"step05_call_llm 只允许在 discovery 阶段运行，当前阶段是: {stage}")

    context = _build_multi_agent_context()

    # ── 阶段 1: 分析师团队（并行）──
    _log("  阶段1: 启动分析师团队...")
    analyst_outputs = run_analyst_team(env_cfg, context)
    _write_agent_outputs(analyst_outputs)
    _log(f"  分析师团队完成: {len(analyst_outputs)} 位分析师贡献了建议")

    if not analyst_outputs:
        raise RuntimeError("没有分析师成功执行，无法继续")

    # ── 阶段 2: 首席分析师（串行）──
    _log("  阶段2: 启动首席分析师整合...")
    chief_cfg = env_cfg.get("llm_agents", {}).get("chief_analyst", {})
    if not chief_cfg.get("model"):
        raise RuntimeError("未配置 chief_analyst")
    chief_agent_config = AgentConfig(
        model=str(chief_cfg["model"]),
        base_url=str(chief_cfg["base_url"]),
        api_key=str(chief_cfg["api_key"]),
        temperature=float(chief_cfg.get("temperature", 0.1)),
        max_tokens=int(chief_cfg.get("max_tokens", 8192)),
        timeout_seconds=float(chief_cfg.get("timeout_seconds", 90.0)),
        max_retries=int(chief_cfg.get("max_retries", 2)),
        request_name="首席分析师",
    )
    design_direction = run_chief_analyst(chief_agent_config, analyst_outputs, context)
    write_json(OUTPUT_DIR / "llm" / "design_direction.json", design_direction)
    _log(f"  首席分析师完成: 主攻方向 = {design_direction.get('primary_focus', 'N/A')}")

    # ── 阶段 3: 因子生成器（串行）──
    _log("  阶段3: 启动因子生成器...")
    gen_cfg = env_cfg.get("llm_agents", {}).get("generator", {})
    if not gen_cfg.get("model"):
        raise RuntimeError("未配置 generator")
    gen_agent_config = AgentConfig(
        model=str(gen_cfg["model"]),
        base_url=str(gen_cfg["base_url"]),
        api_key=str(gen_cfg["api_key"]),
        temperature=float(gen_cfg.get("temperature", 0.2)),
        max_tokens=int(gen_cfg.get("max_tokens", 8192)),
        timeout_seconds=float(gen_cfg.get("timeout_seconds", 90.0)),
        max_retries=int(gen_cfg.get("max_retries", 2)),
        request_name="因子生成器",
    )
    raw_response_draft = run_generator(gen_agent_config, design_direction, context)
    write_json(OUTPUT_DIR / "llm" / "raw_response_draft.json", raw_response_draft)
    _log(f"  生成器完成: 产出 {len(raw_response_draft.get('factors', []))} 个候选因子")

    # ── 阶段 4: 因子评审员（串行）──
    _log("  阶段4: 启动因子评审员...")
    rev_cfg = env_cfg.get("llm_agents", {}).get("reviewer", {})
    if not rev_cfg.get("model"):
        raise RuntimeError("未配置 reviewer")
    rev_agent_config = AgentConfig(
        model=str(rev_cfg["model"]),
        base_url=str(rev_cfg["base_url"]),
        api_key=str(rev_cfg["api_key"]),
        temperature=float(rev_cfg.get("temperature", 0.1)),
        max_tokens=int(rev_cfg.get("max_tokens", 8192)),
        timeout_seconds=float(rev_cfg.get("timeout_seconds", 60.0)),
        max_retries=int(rev_cfg.get("max_retries", 2)),
        request_name="因子评审员",
    )
    # 评审员上下文需要包含 design_direction 用于逻辑一致性检查
    review_context = dict(context)
    review_context["design_direction"] = design_direction
    passed, rejected = run_reviewer(rev_agent_config, raw_response_draft, review_context)

    # 组装最终输出
    factors = raw_response_draft.get("factors", [])
    passed_names = {p["factor_name"] for p in passed}
    final_factors = [f for f in factors if f.get("factor_name") in passed_names]

    final_payload = {
        "model": gen_cfg.get("model", ""),
        "base_url": gen_cfg.get("base_url", ""),
        "messages": [],  # 简化，不保存完整 messages
        "content": json.dumps({"factors": final_factors}, ensure_ascii=False),
        "raw": {"multi_agent": True, "design_direction": design_direction, "review": {"passed": len(passed), "rejected": len(rejected)}},
    }
    write_json(OUTPUT_DIR / "llm" / "raw_response.json", final_payload)
    write_json(OUTPUT_DIR / "llm" / "review_report.json", {"review_results": passed + rejected})
    write_json(OUTPUT_DIR / "llm" / "review_rejected.json", {"factors": rejected})
    _log(f"  评审完成: {len(final_factors)} 通过, {len(rejected)} 拒绝")
    log_step_end("05", "LLM 因子生成完成", details=[f"候选因子: {len(final_factors)} 通过, {len(rejected)} 拒绝"])


if __name__ == "__main__":
    run()
