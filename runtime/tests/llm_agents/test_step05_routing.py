from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 模拟现有 step05 的导入
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from common import OUTPUT_DIR  # noqa: E402


class TestRunMultiAgent:
    """测试 run_multi_agent 完整流程。"""

    @patch("step05_call_llm.run_analyst_team")
    @patch("step05_call_llm.run_chief_analyst")
    @patch("step05_call_llm.run_generator")
    @patch("step05_call_llm.run_reviewer")
    def test_full_pipeline(
        self, mock_reviewer, mock_generator, mock_chief, mock_team
    ):
        """验证多 Agent 完整流水线各阶段被正确调用。"""
        import step05_call_llm

        # 模拟各阶段输出
        mock_team.return_value = [
            {"agent_id": "trend_momentum", "recommendation_score": 0.8, "rationale": "ok"}
        ]
        mock_chief.return_value = {
            "primary_focus": "动量",
            "recommended_features": ["ret_5d"],
            "avoid_features": [],
            "risk_warnings": [],
            "diversification_goal": "",
            "candidate_count": 2,
            "analyst_consensus": {"high_agreement": [], "disagreements": []},
        }
        mock_generator.return_value = {
            "factors": [
                {
                    "factor_name": "f1",
                    "formula": "ret_5d",
                    "fields": ["ret_5d"],
                    "direction": "higher_better",
                    "reason": "动量",
                    "risk": "风险",
                    "expected_failure_regime": "失效",
                }
            ]
        }
        mock_reviewer.return_value = (
            [{"factor_name": "f1", "decision": "PASS", "reason": ""}],
            [],
        )

        # 模拟配置
        env_cfg = {
            "enable_multi_agent": True,
            "llm_agents": {
                "trend_momentum": {"model": "gpt-4o", "base_url": "url", "api_key": "key"},
                "chief_analyst": {"model": "gpt-4o", "base_url": "url", "api_key": "key"},
                "generator": {"model": "gpt-4o", "base_url": "url", "api_key": "key"},
                "reviewer": {"model": "gpt-4o", "base_url": "url", "api_key": "key"},
            },
            "llm_candidate_count": 10,
            "workflow_state": {"stage": "discovery"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            # 创建必要的输入文件
            (output_dir / "health").mkdir(parents=True)
            (output_dir / "llm").mkdir(parents=True)
            (output_dir / "backtest").mkdir(parents=True)

            # 写入 llm_summary.json
            (output_dir / "health" / "llm_summary.json").write_text(
                json.dumps({"top_features": ["ret_5d"]}), encoding="utf-8"
            )
            # 写入 market_context.json
            (output_dir / "health" / "market_context.json").write_text(
                json.dumps({"train_context": {"summary_text": "震荡"}}), encoding="utf-8"
            )

            # patch OUTPUT_DIR
            orig_output_dir = step05_call_llm.OUTPUT_DIR
            step05_call_llm.OUTPUT_DIR = output_dir
            try:
                step05_call_llm.run_multi_agent(env_cfg)

                # 验证各阶段被调用
                mock_team.assert_called_once()
                mock_chief.assert_called_once()
                mock_generator.assert_called_once()
                mock_reviewer.assert_called_once()

                # 验证输出文件
                assert (output_dir / "llm" / "raw_response.json").exists()
                raw = json.loads((output_dir / "llm" / "raw_response.json").read_text())
                parsed_content = json.loads(raw["content"])
                assert "factors" in parsed_content
                assert len(parsed_content["factors"]) == 1
                assert parsed_content["factors"][0]["factor_name"] == "f1"

                # 验证中间产物
                assert (output_dir / "llm" / "design_direction.json").exists()
                assert (output_dir / "llm" / "raw_response_draft.json").exists()
                assert (output_dir / "llm" / "review_report.json").exists()
                assert (output_dir / "llm" / "review_rejected.json").exists()
                assert (output_dir / "llm" / "agent_outputs" / "trend_momentum.json").exists()
            finally:
                step05_call_llm.OUTPUT_DIR = orig_output_dir

    @patch("step05_call_llm.run_analyst_team")
    def test_analyst_team_empty_returns_early(self, mock_team):
        """验证分析师团队返回空列表时，流程提前终止并回退。"""
        import step05_call_llm

        mock_team.return_value = []
        env_cfg = {
            "enable_multi_agent": True,
            "llm_agents": {},
            "llm_candidate_count": 10,
            "workflow_state": {"stage": "discovery"},
        }

        with pytest.raises(RuntimeError, match="多 Agent 模式"):
            step05_call_llm.run_multi_agent(env_cfg)
