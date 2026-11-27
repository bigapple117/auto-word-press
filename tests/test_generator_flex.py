from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors as genai_errors, types

from src.ai_generator import ContentGenerator, FLEX_TIMEOUT_MS


def api_error(code):
    return genai_errors.APIError(code, {"error": {"code": code, "message": "error", "status": "X"}})


def make_generator(*, flex=False, side_effect=None):
    generate = Mock(side_effect=side_effect, return_value=SimpleNamespace(text="response"))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    return ContentGenerator(client, "test-model", flex=flex), generate


def test_flex_request_uses_flex_tier_and_timeout():
    gen, generate = make_generator(flex=True)
    assert gen._call("test-model", "prompt") == "response"
    config = generate.call_args.kwargs["config"]
    assert config.service_tier == types.ServiceTier.FLEX
    assert config.http_options.timeout == FLEX_TIMEOUT_MS
    assert generate.call_count == 1


@pytest.mark.parametrize("error", [api_error(503), api_error(429), TimeoutError("timeout")])
def test_flex_transient_error_retries_same_model_without_tier(error):
    gen, generate = make_generator(flex=True, side_effect=[error, SimpleNamespace(text="fallback")])
    assert gen._call("test-model", "prompt") == "fallback"
    assert generate.call_count == 2
    first, second = (call.kwargs for call in generate.call_args_list)
    assert first["model"] == second["model"] == "test-model"
    assert first["contents"] == second["contents"] == "prompt"
    assert first["config"].service_tier == types.ServiceTier.FLEX
    assert second["config"].service_tier is None


def test_flex_nontransient_error_is_propagated_without_retry():
    error = api_error(403)
    gen, generate = make_generator(flex=True, side_effect=error)
    with pytest.raises(genai_errors.APIError) as exc:
        gen._call("test-model", "prompt")
    assert exc.value is error
    assert generate.call_count == 1
    assert generate.call_args.kwargs["config"].service_tier == types.ServiceTier.FLEX


def test_default_request_has_no_service_tier():
    gen, generate = make_generator()
    assert gen._call("test-model", "prompt") == "response"
    assert generate.call_count == 1
    assert generate.call_args.kwargs["config"].service_tier is None


def test_call_can_disable_generator_flex():
    gen, generate = make_generator(flex=True)
    assert gen._call("test-model", "prompt", flex=False) == "response"
    assert generate.call_count == 1
    assert generate.call_args.kwargs["config"].service_tier is None
