from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from llm_agents.agent_runner import AgentConfig, call_llm_agent, _create_http_client


class TestCreateHttpClient:
    """测试 _create_http_client 代理清理逻辑。"""

    def test_removes_proxy_env_vars(self):
        """验证所有代理环境变量被清除。"""
        os.environ["http_proxy"] = "http://proxy.example.com"
        os.environ["HTTP_PROXY"] = "http://proxy.example.com"
        os.environ["https_proxy"] = "https://proxy.example.com"
        os.environ["HTTPS_PROXY"] = "https://proxy.example.com"
        os.environ["all_proxy"] = "socks5://proxy.example.com"
        os.environ["ALL_PROXY"] = "socks5://proxy.example.com"

        _create_http_client()

        assert "http_proxy" not in os.environ
        assert "HTTP_PROXY" not in os.environ
        assert "https_proxy" not in os.environ
        assert "HTTPS_PROXY" not in os.environ
        assert "all_proxy" not in os.environ
        assert "ALL_PROXY" not in os.environ

    def test_does_not_fail_when_vars_absent(self):
        """当环境变量不存在时不应报错。"""
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            os.environ.pop(key, None)
        _create_http_client()


class TestAgentConfig:
    """测试 AgentConfig 数据类。"""

    def test_default_values(self):
        config = AgentConfig(model="gpt-4o", base_url="https://api.openai.com/v1", api_key="sk-test")
        assert config.temperature == 0.2
        assert config.timeout_seconds == 60.0
        assert config.max_retries == 2

    def test_custom_values(self):
        config = AgentConfig(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key="sk-test",
            temperature=0.5,
            timeout_seconds=120.0,
            max_retries=3,
            request_name="测试分析师",
        )
        assert config.temperature == 0.5
        assert config.timeout_seconds == 120.0
        assert config.max_retries == 3
        assert config.request_name == "测试分析师"


class TestCallLlmAgent:
    """测试 call_llm_agent 核心逻辑。"""

    def _make_config(self):
        return AgentConfig(
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            temperature=0.3,
            timeout_seconds=10.0,
            max_retries=1,
        )

    def _make_messages(self):
        return [
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": "Say hello."},
        ]

    def _make_successful_response(self):
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"result": "hello"})
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test"}
        return mock_response

    @patch("llm_agents.agent_runner.OpenAI")
    def test_successful_call(self, mock_openai_cls):
        """验证正常调用返回解析后的 JSON。"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"result": "hello"})
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test", "choices": [{"finish_reason": "stop"}]}
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        messages = self._make_messages()
        result = call_llm_agent(config, messages)

        assert result == {"result": "hello"}
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["messages"] == messages
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["timeout"] == 10.0

    @patch("llm_agents.agent_runner.OpenAI")
    def test_logs_request_name(self, mock_openai_cls, capsys):
        """验证日志中包含调用者名称，便于区分不同分析师。"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({"result": "hello"})
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test", "choices": [{"finish_reason": "stop"}]}
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        config.request_name = "分析师-趋势动量"
        messages = self._make_messages()
        result = call_llm_agent(config, messages)

        assert result == {"result": "hello"}
        output = capsys.readouterr().out
        assert "分析师-趋势动量" in output
        assert "请求成功" in output

    @patch("llm_agents.agent_runner.OpenAI")
    def test_retry_on_exception(self, mock_openai_cls):
        """验证异常时重试机制。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            RuntimeError("Connection error"),
            self._make_successful_response(),
        ]
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        messages = self._make_messages()
        result = call_llm_agent(config, messages)

        assert result == {"result": "hello"}
        assert mock_client.chat.completions.create.call_count == 2

    @patch("llm_agents.agent_runner.OpenAI")
    def test_raises_after_max_retries(self, mock_openai_cls):
        """验证超过最大重试次数后抛出异常。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("Always fails")
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        messages = self._make_messages()
        with pytest.raises(RuntimeError, match="LLM Agent 调用失败"):
            call_llm_agent(config, messages)

    @patch("llm_agents.agent_runner.OpenAI")
    def test_invalid_json_response(self, mock_openai_cls):
        """验证返回非 JSON 时保存错误并抛出异常。"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "not valid json"
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test"}
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        messages = self._make_messages()
        with pytest.raises(RuntimeError, match="LLM Agent 调用失败"):
            call_llm_agent(config, messages)

    @patch("llm_agents.agent_runner.OpenAI")
    def test_truncated_response_reduces_candidate(self, mock_openai_cls):
        """验证截断响应对应 finish_reason=length 时的处理。"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"result": "incomplete'
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "test"}
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        config = self._make_config()
        messages = self._make_messages()
        with pytest.raises(RuntimeError, match="LLM Agent 调用失败"):
            call_llm_agent(config, messages)
