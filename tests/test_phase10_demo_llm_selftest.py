"""V2 Phase 10 — core/demo_llm_selftest.py: the operator's one-click live OpenAI
self-test, run from inside the deployed demo app. Every branch is exercised with a
mocked OpenAI client — no real network call, no real key, ever.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

from unittest import mock

import pytest


def test_missing_key_is_reported_without_ever_calling_openai(monkeypatch):
    from core.demo_llm_selftest import run_selftest

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = run_selftest()
    assert result.authentication == "FAIL" and result.error_category == "no_api_key"


def _mock_openai(monkeypatch, side_effect=None, create=None):
    import openai as real_openai

    fake_client = mock.MagicMock()
    if side_effect is not None:
        fake_client.responses.create.side_effect = side_effect
    else:
        fake_client.responses.create.return_value = create or mock.MagicMock()
    monkeypatch.setattr("openai.OpenAI", lambda **kw: fake_client)
    return fake_client, real_openai


def test_a_successful_call_reports_all_three_categories_pass(monkeypatch):
    from core.demo_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    _mock_openai(monkeypatch)
    result = run_selftest(model="gpt-5-mini")
    assert (result.authentication, result.billing, result.model_access) == ("PASS", "PASS", "PASS")
    assert result.error_category == ""


def test_authentication_error_fails_closed_and_skips_the_rest(monkeypatch):
    import openai as real_openai

    from core.demo_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    err = real_openai.AuthenticationError("bad key", response=mock.MagicMock(status_code=401), body=None)
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "FAIL" and result.billing == "SKIPPED" and result.model_access == "SKIPPED"
    assert result.error_category == "authentication_failed"


def test_permission_denied_is_a_model_access_failure_not_an_auth_failure(monkeypatch):
    import openai as real_openai

    from core.demo_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    err = real_openai.PermissionDeniedError("denied", response=mock.MagicMock(status_code=403), body=None)
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "PASS" and result.model_access == "FAIL"
    assert result.error_category == "model_access_denied"


def test_insufficient_quota_is_a_billing_failure(monkeypatch):
    import openai as real_openai

    from core.demo_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    resp = mock.MagicMock(status_code=429)
    err = real_openai.RateLimitError("insufficient_quota: no credits", response=resp, body=None)
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.authentication == "PASS" and result.billing == "FAIL" and result.model_access == "SKIPPED"
    assert result.error_category == "insufficient_quota"


def test_ordinary_rate_limiting_is_not_reported_as_a_billing_failure(monkeypatch):
    import openai as real_openai

    from core.demo_llm_selftest import run_selftest

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    resp = mock.MagicMock(status_code=429)
    err = real_openai.RateLimitError("rate limited, slow down", response=resp, body=None)
    _mock_openai(monkeypatch, side_effect=err)
    result = run_selftest()
    assert result.billing == "PASS" and result.error_category == "rate_limited"


def test_result_never_contains_the_api_key(monkeypatch):
    from core.demo_llm_selftest import run_selftest

    secret = "sk-test-SENTINEL-DO-NOT-LEAK"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    _mock_openai(monkeypatch)
    result = run_selftest()
    assert secret not in repr(result)


def test_diagnostics_button_is_present_only_when_diagnostics_are_enabled(monkeypatch):
    import core.demo_diagnostics as diag

    monkeypatch.delenv("LEADLENS_DEMO_DIAGNOSTICS", raising=False)
    monkeypatch.setattr(diag, "_top_level_secrets", lambda: {})
    monkeypatch.setattr(diag, "_secret_sections_containing", lambda name: [])
    assert not diag.diagnostics_enabled()
