from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_agents.generator import _build_generator_messages, _parse_factors, run_generator
from llm_agents.agent_runner import AgentConfig


class TestBuildGeneratorMessages:
    """测试 _build_generator_messages。"""

    def _make_design_direction(self):
        return {
            "primary_focus": "反转+波动率调整",
            "recommended_features": ["ret_5d", "realized_vol_20d", "volume_ratio_20d"],
            "avoid_features": ["ret_60d"],
            "risk_warnings": ["高波动风险"],
            "diversification_goal": "覆盖2种逻辑",
            "candidate_count": 5,
        }

    def _make_context(self):
        return {
            "feature_names": ["ret_1d", "ret_5d", "realized_vol_20d", "volume_ratio_20d"],
            "allowed_operators": ["ts_mean", "ts_std", "rank"],
            "llm_summary": {},
            "market_context": {},
        }

    def test_returns_system_and_user_messages(self):
        dd = self._make_design_direction()
        context = self._make_context()
        messages = _build_generator_messages(dd, context)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "因子公式生成器" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "反转+波动率调整" in messages[1]["content"]
        assert "ret_5d" in messages[1]["content"]
        assert "ret_60d" in messages[1]["content"]  # avoid_features


class TestParseFactors:
    """测试 _parse_factors。"""

    def test_valid_output(self):
        raw = {
            "factors": [
                {
                    "factor_name": "test_factor",
                    "formula": "ret_5d / realized_vol_20d",
                    "fields": ["ret_5d", "realized_vol_20d"],
                    "direction": "higher_better",
                    "reason": "测试",
                    "risk": "测试风险",
                    "expected_failure_regime": "高波动",
                }
            ]
        }
        result = _parse_factors(raw)
        assert "factors" in result
        assert len(result["factors"]) == 1
        assert result["factors"][0]["factor_name"] == "test_factor"

    def test_missing_factors_key(self):
        raw = {"other": "data"}
        result = _parse_factors(raw)
        assert result == {"factors": []}

    def test_invalid_factor_filtered(self):
        raw = {
            "factors": [
                {
                    "factor_name": "valid",
                    "formula": "ret_5d",
                    "fields": ["ret_5d"],
                    "direction": "higher_better",
                    "reason": "ok",
                    "risk": "ok",
                    "expected_failure_regime": "ok",
                },
                {
                    "factor_name": "invalid",
                    "formula": "ret_5d",
                    # 缺少 required fields
                },
            ]
        }
        result = _parse_factors(raw)
        assert len(result["factors"]) == 1
        assert result["factors"][0]["factor_name"] == "valid"


class TestRunGenerator:
    """测试 run_generator。"""

    def _make_config(self):
        return AgentConfig(model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test")

    def _make_design_direction(self):
        return {
            "primary_focus": "反转",
            "recommended_features": ["ret_5d"],
            "avoid_features": [],
            "risk_warnings": [],
            "diversification_goal": "",
            "candidate_count": 3,
        }

    def _make_context(self):
        return {
            "feature_names": ["ret_5d"],
            "allowed_operators": ["ts_mean"],
            "llm_summary": {},
            "market_context": {},
        }

    @patch("llm_agents.generator.call_llm_agent")
    def test_success(self, mock_call):
        mock_call.return_value = {
            "factors": [
                {
                    "factor_name": "rev_vol_v1",
                    "formula": "ret_5d / realized_vol_20d",
                    "fields": ["ret_5d", "realized_vol_20d"],
                    "direction": "higher_better",
                    "reason": "反转",
                    "risk": "风险",
                    "expected_failure_regime": "失效",
                }
            ]
        }
        config = self._make_config()
        dd = self._make_design_direction()
        context = self._make_context()
        result = run_generator(config, dd, context)

        assert "factors" in result
        assert len(result["factors"]) == 1
        assert result["factors"][0]["factor_name"] == "rev_vol_v1"
        mock_call.assert_called_once()

    @patch("llm_agents.generator.call_llm_agent")
    def test_failure_raises(self, mock_call):
        mock_call.side_effect = RuntimeError("LLM failed")
        config = self._make_config()
        dd = self._make_design_direction()
        context = self._make_context()
        with pytest.raises(RuntimeError, match="因子生成器执行失败"):
            run_generator(config, dd, context)
