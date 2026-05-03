from __future__ import annotations

from unittest.mock import patch

import pytest

from llm_agents.reviewer import (
    _build_reviewer_messages,
    _parse_review_results,
    run_reviewer,
)
from llm_agents.agent_runner import AgentConfig


class TestBuildReviewerMessages:
    """测试 _build_reviewer_messages。"""

    def _make_draft(self):
        return {
            "factors": [
                {
                    "factor_name": "rev_vol_v1",
                    "formula": "ret_5d / realized_vol_20d",
                    "fields": ["ret_5d", "realized_vol_20d"],
                    "direction": "higher_better",
                    "reason": "反转",
                    "risk": "风险",
                    "expected_failure_regime": "高波动",
                },
                {
                    "factor_name": "bad_factor",
                    "formula": "close.shift(-1)",
                    "fields": ["close"],
                    "direction": "higher_better",
                    "reason": "偷看未来",
                    "risk": "风险",
                    "expected_failure_regime": "永远",
                },
            ]
        }

    def _make_context(self):
        return {
            "feature_names": ["ret_5d", "realized_vol_20d", "close"],
            "allowed_operators": ["ts_mean"],
            "design_direction": {"primary_focus": "反转+波动率"},
            "previous_skipped": [{"factor_name": "old_bad", "formula": "close.shift(-1)"}],
        }

    def test_returns_system_and_user_messages(self):
        draft = self._make_draft()
        context = self._make_context()
        messages = _build_reviewer_messages(draft, context)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "因子质量评审员" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "rev_vol_v1" in messages[1]["content"]
        assert "bad_factor" in messages[1]["content"]


class TestParseReviewResults:
    """测试 _parse_review_results。"""

    def test_valid_output(self):
        raw = {
            "review_results": [
                {"factor_name": "f1", "decision": "PASS", "reason": ""},
                {"factor_name": "f2", "decision": "REJECT:未来函数", "reason": "使用shift(-1)"},
            ]
        }
        passed, rejected = _parse_review_results(raw)
        assert len(passed) == 1
        assert passed[0]["factor_name"] == "f1"
        assert len(rejected) == 1
        assert rejected[0]["factor_name"] == "f2"
        assert "REJECT" in rejected[0]["decision"]

    def test_missing_results_returns_empty(self):
        passed, rejected = _parse_review_results({})
        assert passed == []
        assert rejected == []

    def test_unknown_decision_treated_as_reject(self):
        raw = {
            "review_results": [
                {"factor_name": "f1", "decision": "MAYBE", "reason": "不确定"},
            ]
        }
        passed, rejected = _parse_review_results(raw)
        assert len(passed) == 0
        assert len(rejected) == 1
        assert rejected[0]["decision"] == "REJECT:评审结果不明确，按拒绝处理"


class TestRunReviewer:
    """测试 run_reviewer。"""

    def _make_config(self):
        return AgentConfig(
            model="claude-3-haiku",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-test",
        )

    def _make_draft(self):
        return {
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

    def _make_context(self):
        return {
            "feature_names": ["ret_5d"],
            "allowed_operators": ["ts_mean"],
            "design_direction": {"primary_focus": "动量"},
            "previous_skipped": [],
        }

    @patch("llm_agents.reviewer.call_llm_agent")
    def test_success(self, mock_call):
        mock_call.return_value = {
            "review_results": [
                {"factor_name": "f1", "decision": "PASS", "reason": ""},
            ]
        }
        config = self._make_config()
        draft = self._make_draft()
        context = self._make_context()
        passed, rejected = run_reviewer(config, draft, context)

        assert len(passed) == 1
        assert len(rejected) == 0
        assert passed[0]["factor_name"] == "f1"

    @patch("llm_agents.reviewer.call_llm_agent")
    def test_all_rejected(self, mock_call):
        mock_call.return_value = {
            "review_results": [
                {
                    "factor_name": "f1",
                    "decision": "REJECT:未来函数风险",
                    "reason": "使用shift(-1)",
                },
            ]
        }
        config = self._make_config()
        draft = self._make_draft()
        context = self._make_context()
        passed, rejected = run_reviewer(config, draft, context)

        assert len(passed) == 0
        assert len(rejected) == 1
        assert rejected[0]["factor_name"] == "f1"

    @patch("llm_agents.reviewer.call_llm_agent")
    def test_failure_returns_all_passed(self, mock_call):
        """评审员失败时，默认所有因子通过（激进策略）。"""
        mock_call.side_effect = RuntimeError("LLM failed")
        config = self._make_config()
        draft = self._make_draft()
        context = self._make_context()
        passed, rejected = run_reviewer(config, draft, context)

        assert len(passed) == 1
        assert len(rejected) == 0
