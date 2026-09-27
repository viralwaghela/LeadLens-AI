"""core/ops_llm_selftest.py — the operator's one-click live OpenAI self-test for the
client/production deployment. Every branch exercised with a mocked OpenAI client —
no real network call, no real key, ever.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from unittest import mock


def _mock_openai(monkeypatch, side_effect=None):
    fake_client = mock.MagicMock()
    if side_effect is not None:
        fake_client.responses.create.side_effect = side_effect
    monkeypatch.setattr("openai.OpenAI", lambda **kw: fake_client)
    return fake_client


def test_missing_key_is_reported_without_ever_calling_openai(monkeypatch):
    from core.ops_llm_selftest import run_selftest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = run_selftest()
    assert result.authentication == "FAIL" and result.error_category == "no_api_key"


def test_configured_model_matches_services_ai_default(monkeypatch):
    from core.ops_llm_selftest import configured_model

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert configured_model() == "gpt-5.1"
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert configured_model() == "gpt-4o"


def test_a_successful_call_reports_all_three_categories_pass(monkeypatch):
    from core.ops_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    _mock_openai(monkeypatch)
    result = run_selftest(model="gpt-5.1")
    assert (result.authentication, result.billing, result.model_access) == ("PASS", "PASS", "PASS")
    assert result.error_category == ""


def test_403_is_reported_with_exact_status_type_code_message(monkeypatch):
    import httpx
    import openai as real_openai

    from core.ops_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(403, request=request, json={
        "error": {"message": "You do not have access to this model.", "type": "invalid_request_error", "code": "model_not_found"}
    })
    err = real_openai.PermissionDeniedError(
        "You do not have access to this model.", response=response,
        body={"message": "You do not have access to this model.", "type": "invalid_request_error", "code": "model_not_found"},
    )
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "PASS" and result.model_access == "FAIL"
    assert result.error_category == "model_access_denied"
    assert result.http_status == 403
    assert result.error_type == "invalid_request_error"
    assert result.error_code == "model_not_found"
    assert "do not have access" in (result.error_message or "")


def test_authentication_error_fails_closed_and_skips_the_rest(monkeypatch):
    import httpx
    import openai as real_openai

    from core.ops_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(401, request=request, json={"error": {"message": "bad key", "type": "invalid_request_error", "code": None}})
    err = real_openai.AuthenticationError("bad key", response=response, body={"message": "bad key"})
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "FAIL" and result.billing == "SKIPPED" and result.model_access == "SKIPPED"
    assert result.error_category == "authentication_failed"
    assert result.http_status == 401


def test_insufficient_quota_is_a_billing_failure(monkeypatch):
    import httpx
    import openai as real_openai

    from core.ops_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request, json={"error": {"message": "insufficient_quota: no credits", "type": "insufficient_quota", "code": "insufficient_quota"}})
    err = real_openai.RateLimitError("insufficient_quota: no credits", response=response, body={"message": "insufficient_quota: no credits"})
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "PASS" and result.billing == "FAIL" and result.model_access == "SKIPPED"
    assert result.error_category == "insufficient_quota"


def test_result_never_contains_the_api_key(monkeypatch):
    from core.ops_llm_selftest import run_selftest

    secret = "sk-test-SENTINEL-DO-NOT-LEAK"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    _mock_openai(monkeypatch)
    result = run_selftest()
    assert secret not in repr(result)


def test_key_shape_never_reveals_the_key_itself():
    from core.ops_diagnostics import _key_shape

    secret = "sk-proj-SUPER-SECRET-VALUE-1234567890"
    shape = _key_shape(secret)
    assert secret not in shape
    assert "project-scoped" in shape and str(len(secret)) in shape


def test_diagnostics_hidden_by_default(monkeypatch):
    from core.ops_diagnostics import diagnostics_enabled

    monkeypatch.delenv("LEADLENS_OPS_DIAGNOSTICS", raising=False)
    assert not diagnostics_enabled()
