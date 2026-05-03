from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class TestMultiAgentOutputCompatibility:
    """验证多 Agent 模式的输出与 step06 兼容。"""

    def _make_raw_response(self):
        """构造符合现有 schema 的 raw_response.json。"""
        return {
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "messages": [],
            "content": json.dumps(
                {
                    "factors": [
                        {
                            "factor_name": "momentum_vol_v1",
                            "formula": "ret_20d / (1 + realized_vol_20d)",
                            "fields": ["ret_20d", "realized_vol_20d"],
                            "direction": "higher_better",
                            "reason": "动量经波动率调整",
                            "risk": "高波动环境失效",
                            "expected_failure_regime": "高波动单边逼空",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "raw": {"multi_agent": True},
        }

    def test_raw_response_schema_matches_single_prompt(self):
        """验证多 Agent 输出的 raw_response.json 与单 Prompt 模式 schema 一致。"""
        from step06_validate_factor import FactorPayload
        from common import parse_json_text

        raw_response = self._make_raw_response()
        content = raw_response.get("content", "")
        payload = parse_json_text(content)

        # step06 使用 FactorPayload.model_validate(payload)
        factor_payload = FactorPayload.model_validate(payload)
        assert len(factor_payload.factors) == 1
        assert factor_payload.factors[0].factor_name == "momentum_vol_v1"
        assert factor_payload.factors[0].direction in ("higher_better", "lower_better")


class TestSinglePromptFallback:
    """验证 enable_multi_agent=false 时原有逻辑不受影响。"""

    @patch("step05_call_llm.run_multi_agent")
    @patch("step05_call_llm.OpenAI")
    def test_does_not_call_multi_agent_when_disabled(self, mock_openai, mock_multi):
        """验证 enable_multi_agent=false 时不调用 run_multi_agent。"""
        import step05_call_llm

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"factors": []})
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test"}
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        # 构造测试用的 env_config
        test_config = {
            "enable_multi_agent": False,
            "llm_model": "gpt-4o",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "sk-test",
            "llm_candidate_count": 10,
            "llm_request_timeout_seconds": 120,
            "llm_max_retries": 2,
            "llm_retry_wait_seconds": 3,
            "llm_min_candidate_count": 3,
            "workflow_state": {"stage": "discovery"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "health").mkdir(parents=True)
            (output_dir / "llm").mkdir(parents=True)
            (output_dir / "health" / "llm_summary.json").write_text(
                json.dumps({}), encoding="utf-8"
            )

            orig_output_dir = step05_call_llm.OUTPUT_DIR
            orig_env_config = step05_call_llm.env_config
            step05_call_llm.OUTPUT_DIR = output_dir
            step05_call_llm.env_config = lambda: test_config
            try:
                step05_call_llm.run()
                mock_multi.assert_not_called()
                assert (output_dir / "llm" / "raw_response.json").exists()
            finally:
                step05_call_llm.OUTPUT_DIR = orig_output_dir
                step05_call_llm.env_config = orig_env_config


class TestAgentOutputDirectoryStructure:
    """验证中间产物目录结构正确。"""

    def test_agent_outputs_dir_created(self):
        """验证 agent_outputs 目录被正确创建。"""
        import step05_call_llm

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_output_dir = step05_call_llm.OUTPUT_DIR
            step05_call_llm.OUTPUT_DIR = Path(tmpdir)
            try:
                outputs = [
                    {"agent_id": "trend_momentum", "recommendation_score": 0.8},
                    {"agent_id": "reversal_mean_reversion", "recommendation_score": 0.6},
                ]
                step05_call_llm._write_agent_outputs(outputs)

                agent_dir = Path(tmpdir) / "llm" / "agent_outputs"
                assert agent_dir.exists()
                assert (agent_dir / "trend_momentum.json").exists()
                assert (agent_dir / "reversal_mean_reversion.json").exists()
            finally:
                step05_call_llm.OUTPUT_DIR = orig_output_dir
