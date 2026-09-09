"""Explicit child failover must override a pin without changing default isolation."""
import copy
import threading
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tools.delegate_tool import _build_child_agent, _load_config


def parent():
    p = MagicMock()
    p.provider = "openrouter"
    p.model = "parent-model"
    p.base_url = "https://openrouter.ai/api/v1"
    p.api_key = "test-key"
    p.api_mode = "chat_completions"
    p.platform = "cli"
    p._delegate_depth = 0
    p._active_children = []
    p._active_children_lock = threading.Lock()
    p._session_db = None
    p.enabled_toolsets = []
    p._fallback_chain = [{"provider": "openrouter", "model": "parent-fallback"}]
    return p


def build(p, provider="anthropic"):
    return _build_child_agent(
        task_index=0, goal="test", context=None, toolsets=None,
        model="child-model", max_iterations=5, task_count=1, parent_agent=p,
        override_provider=provider,
    )


@pytest.mark.parametrize("pinned", [True, False])
@pytest.mark.parametrize("chain", [[], [{"provider": "openrouter", "model": "backup"}]])
def test_explicit_chain_overrides_pin_and_parent_without_aliasing(chain, pinned):
    cfg = {"fallback_providers": copy.deepcopy(chain)}
    with patch("tools.delegate_tool._load_config", return_value=cfg), patch("run_agent.AIAgent") as agent:
        build(parent(), "anthropic" if pinned else None)
    actual = agent.call_args.kwargs["fallback_model"]
    assert actual == chain
    actual.append({"provider": "other", "model": "mutated"})
    assert cfg["fallback_providers"] == chain


@pytest.mark.parametrize("bad", [False, {}, "inherit", ["bad"], [{"model": "x"}], [{"provider": "x", "model": " "}]])
def test_bad_explicit_chain_fails_before_child_instantiation(bad):
    with patch("tools.delegate_tool._load_config", return_value={"fallback_providers": bad}), patch("run_agent.AIAgent") as agent:
        with pytest.raises(ValueError, match="delegation.fallback_providers"):
            build(parent())
        agent.assert_not_called()


@pytest.mark.parametrize("pinned", [True, False])
def test_null_retains_existing_provider_pin_policy(pinned):
    p = parent()
    with patch("tools.delegate_tool._load_config", return_value={"fallback_providers": None}), patch("run_agent.AIAgent") as agent:
        build(p, "anthropic" if pinned else None)
    assert agent.call_args.kwargs["fallback_model"] == (None if pinned else p._fallback_chain)


def test_profile_yaml_reaches_actual_delegation_loader(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_IGNORE_USER_CONFIG", raising=False)
    chain = [{"provider": "anthropic", "model": "backup-model"}]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"delegation": {"provider": "anthropic", "fallback_providers": chain}}))
    assert _load_config()["fallback_providers"] == chain
    with patch("run_agent.AIAgent") as agent:
        build(parent())
    assert agent.call_args.kwargs["fallback_model"] == chain
