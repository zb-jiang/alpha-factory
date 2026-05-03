from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_agents.chief_analyst import _build_chief_messages, _parse_design_direction, run_chief_analyst
from llm_agents.agent_runner import AgentConfig


class TestBuildChiefMessages:
    """测试 _build_chief_messages。"""

    def _make_analyst_outputs(self):
        return [
            {
                "agent_id": "trend_momentum",
                "recommendation_score": 0.8,
                "rationale": "趋势向上",
                "recommended_features": ["ret_20d", "breakout_20d"],
                "avoid_features": ["ret_1d"],
                "risk_warnings": ["波动率风险"],
                "suggested_factor_types": ["动量延续"],
            },
            {
                "agent_id": "reversal_mean_reversion",
                "recommendation_score": 0.6,
                "rationale": "存在过度反应",
                "recommended_features": ["ret_5d", "max_drawdown_20d"],
                "avoid_features": ["ret_60d"],
                "risk_warnings": ["趋势持续时反转失效"],
                "suggested_factor_types": ["短期反转"],
            },
        ]

    def _make_context(self):
        return {
            "llm_summary": {"top_features": ["ret_5d"]},
            "market_context": {"summary_text": "震荡上行"},
            "candidate_count": 10,
        }

    def test_returns_system_and_user_messages(self):
        outputs = self._make_analyst_outputs()
        context = self._make_context()
        messages = _build_chief_messages(outputs, context)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "首席量化分析师" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "trend_momentum" in messages[1]["content"]
        assert "10" in messages[1]["content"]

    def test_empty_analyst_outputs_included(self):
        messages = _build_chief_messages([], {"candidate_count": 5})
        assert "没有分析师提供有效建议" in messages[1]["content"]


class TestParseDesignDirection:
    """测试 _parse_design_direction。"""

    def test_valid_output(self):
        raw = {
            "primary_focus": "动量+反转",
            "recommended_features": ["ret_20d", "ret_5d"],
            "avoid_features": ["ret_1d"],
            "risk_warnings": ["波动率风险"],
            "diversification_goal": "覆盖2种逻辑",
            "candidate_count": 10,
            "analyst_consensus": {
                "high_agreement": ["动量有效"],
                "disagreements": ["反转存疑"],
            },
        }
        result = _parse_design_direction(raw, default_candidate_count=10)
        assert result["primary_focus"] == "动量+反转"
        assert result["candidate_count"] == 10
        assert "recommended_features" in result

    def test_missing_candidate_count_uses_default(self):
        raw = {"primary_focus": "动量"}
        result = _parse_design_direction(raw, default_candidate_count=8)
        assert result["candidate_count"] == 8

    def test_candidate_count_clamped_positive(self):
        raw = {"candidate_count": -5}
        result = _parse_design_direction(raw, default_candidate_count=10)
        assert result["candidate_count"] == 1

    def test_missing_fields_filled(self):
        raw = {"primary_focus": "动量"}
        result = _parse_design_direction(raw, default_candidate_count=10)
        assert result["recommended_features"] == []
        assert result["avoid_features"] == []
        assert result["risk_warnings"] == []
        assert result["diversification_goal"] == ""
        assert result["analyst_consensus"] == {"high_agreement": [], "disagreements": []}


class TestRunChiefAnalyst:
    """测试 run_chief_analyst。"""

    def _make_config(self):
        return AgentConfig(model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test")

    def _make_outputs(self):
        return [
            {
                "agent_id": "trend_momentum",
                "recommendation_score": 0.8,
                "rationale": "趋势向上",
                "recommended_features": ["ret_20d"],
                "avoid_features": [],
                "risk_warnings": [],
                "suggested_factor_types": [],
            }
        ]

    def _make_context(self):
        return {
            "llm_summary": {},
            "market_context": {},
            "candidate_count": 10,
        }

    @patch("llm_agents.chief_analyst.call_llm_agent")
    def test_success(self, mock_call):
        mock_call.return_value = {
            "primary_focus": "动量",
            "recommended_features": ["ret_20d"],
            "avoid_features": [],
            "risk_warnings": [],
            "diversification_goal": "",
            "candidate_count": 10,
            "analyst_consensus": {"high_agreement": [], "disagreements": []},
        }
        config = self._make_config()
        outputs = self._make_outputs()
        context = self._make_context()
        result = run_chief_analyst(config, outputs, context)

        assert result["primary_focus"] == "动量"
        assert result["candidate_count"] == 10
        mock_call.assert_called_once()

    @patch("llm_agents.chief_analyst.call_llm_agent")
    def test_failure_raises(self, mock_call):
        mock_call.side_effect = RuntimeError("LLM failed")
        config = self._make_config()
        outputs = self._make_outputs()
        context = self._make_context()
        with pytest.raises(RuntimeError, match="首席分析师执行失败"):
            run_chief_analyst(config, outputs, context)
