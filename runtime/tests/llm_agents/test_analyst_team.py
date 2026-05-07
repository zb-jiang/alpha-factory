from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm_agents.analyst_team import (
    ANALYST_AGENT_IDS,
    _build_analyst_messages,
    _parse_analyst_output,
    run_analyst,
    run_analyst_team,
)
from llm_agents.agent_runner import AgentConfig


class TestAnalystAgentIds:
    """测试分析师 ID 列表。"""

    def test_contains_all_six(self):
        assert "trend_momentum" in ANALYST_AGENT_IDS
        assert "reversal_mean_reversion" in ANALYST_AGENT_IDS
        assert "volatility_risk" in ANALYST_AGENT_IDS
        assert "volume_price" in ANALYST_AGENT_IDS
        assert "microstructure" in ANALYST_AGENT_IDS
        assert "chip_distribution" in ANALYST_AGENT_IDS
        assert len(ANALYST_AGENT_IDS) == 6


class TestBuildAnalystMessages:
    """测试 _build_analyst_messages。"""

    def _make_context(self):
        return {
            "feature_names": ["ret_1d", "ret_5d", "volume_ratio_20d"],
            "allowed_operators": ["ts_mean", "ts_std", "rank"],
            "llm_summary": {"top_features": ["ret_5d"], "weak_features": []},
            "market_context": {"summary_text": "震荡市", "labels": {"trend": "震荡"}},
            "previous_top": [{"factor_name": "f1", "formula": "ret_5d"}],
            "previous_skipped": [{"factor_name": "f2", "formula": "ret_1d", "reason": "IC too low"}],
        }

    def test_returns_list_of_dicts(self):
        context = self._make_context()
        messages = _build_analyst_messages("trend_momentum", context)
        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "趋势动量" in messages[0]["content"]
        assert "ret_5d" in messages[1]["content"]

    def test_each_agent_has_different_system_prompt(self):
        context = self._make_context()
        ids = ["trend_momentum", "reversal_mean_reversion", "volatility_risk"]
        prompts = [_build_analyst_messages(i, context)[0]["content"] for i in ids]
        assert prompts[0] != prompts[1]
        assert prompts[1] != prompts[2]


class TestParseAnalystOutput:
    """测试 _parse_analyst_output。"""

    def test_valid_output(self):
        raw = {
            "recommendation_score": 0.75,
            "rationale": "动量因子有效",
            "recommended_features": ["ret_20d"],
            "avoid_features": ["ret_1d"],
            "risk_warnings": ["波动率风险"],
            "suggested_factor_types": ["动量"],
        }
        result = _parse_analyst_output("trend_momentum", raw)
        assert result["agent_id"] == "trend_momentum"
        assert result["recommendation_score"] == 0.75

    def test_missing_optional_fields_filled(self):
        raw = {"rationale": "动量因子有效"}
        result = _parse_analyst_output("trend_momentum", raw)
        assert result["agent_id"] == "trend_momentum"
        assert result["recommendation_score"] == 0.5
        assert result["recommended_features"] == []
        assert result["avoid_features"] == []

    def test_invalid_score_clamped(self):
        raw = {"recommendation_score": 1.5, "rationale": "test"}
        result = _parse_analyst_output("trend_momentum", raw)
        assert result["recommendation_score"] == 1.0


class TestRunAnalyst:
    """测试 run_analyst。"""

    def _make_config(self):
        return AgentConfig(model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test")

    def _make_context(self):
        return {
            "feature_names": ["ret_1d"],
            "allowed_operators": ["ts_mean"],
            "llm_summary": {},
            "market_context": {},
            "previous_top": [],
            "previous_skipped": [],
        }

    @patch("llm_agents.analyst_team.call_llm_agent")
    def test_success(self, mock_call):
        mock_call.return_value = {
            "recommendation_score": 0.8,
            "rationale": "test",
            "recommended_features": ["ret_5d"],
            "avoid_features": [],
            "risk_warnings": [],
            "suggested_factor_types": [],
        }
        config = self._make_config()
        context = self._make_context()
        result = run_analyst("trend_momentum", config, context)

        assert result["agent_id"] == "trend_momentum"
        assert result["recommendation_score"] == 0.8
        mock_call.assert_called_once()

    @patch("llm_agents.analyst_team.call_llm_agent")
    def test_failure_returns_none(self, mock_call):
        mock_call.side_effect = RuntimeError("LLM failed")
        config = self._make_config()
        context = self._make_context()
        result = run_analyst("trend_momentum", config, context)
        assert result is None


class TestRunAnalystTeam:
    """测试 run_analyst_team 并行执行。"""

    def _make_env_cfg(self):
        return {
            "llm_agents": {
                "trend_momentum": {
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-test",
                },
                "reversal_mean_reversion": {
                    "model": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-test",
                },
            }
        }

    def _make_context(self):
        return {
            "feature_names": ["ret_1d"],
            "allowed_operators": ["ts_mean"],
            "llm_summary": {},
            "market_context": {},
            "previous_top": [],
            "previous_skipped": [],
        }

    @patch("llm_agents.analyst_team.call_llm_agent")
    def test_parallel_execution(self, mock_call):
        """验证 6 个分析师并行执行。"""
        mock_call.return_value = {
            "recommendation_score": 0.7,
            "rationale": "test",
            "recommended_features": [],
            "avoid_features": [],
            "risk_warnings": [],
            "suggested_factor_types": [],
        }
        env_cfg = self._make_env_cfg()
        context = self._make_context()
        results = run_analyst_team(env_cfg, context)

        # 只有配置中有的 2 个分析师会执行
        assert len(results) == 2
        assert results[0]["agent_id"] in ("trend_momentum", "reversal_mean_reversion")
        assert mock_call.call_count == 2

    @patch("llm_agents.analyst_team.call_llm_agent")
    def test_missing_agent_config_skips(self, mock_call):
        """验证缺少配置的分析师被跳过。"""
        mock_call.return_value = {"recommendation_score": 0.7, "rationale": "test"}
        env_cfg = {"llm_agents": {}}  # 没有任何分析师配置
        context = self._make_context()
        results = run_analyst_team(env_cfg, context)
        assert len(results) == 0
        assert mock_call.call_count == 0

    @patch("llm_agents.analyst_team.call_llm_agent")
    def test_failed_analyst_filtered_out(self, mock_call):
        """验证失败的分析师输出被过滤。"""
        mock_call.side_effect = [
            {"recommendation_score": 0.8, "rationale": "ok"},
            RuntimeError("fail"),
        ]
        env_cfg = self._make_env_cfg()
        context = self._make_context()
        results = run_analyst_team(env_cfg, context)
        assert len(results) == 1
