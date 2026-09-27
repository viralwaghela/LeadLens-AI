"""Operator self-test: one minimal live OpenAI request, run from the deployed demo app.

Exists because the demo's OPENAI_API_KEY lives only in that deployment's Streamlit
Secrets - nobody outside the running app (including this codebase's own tooling) has
it, so the only way to verify it actually works is to make the app itself try it and
report the outcome. Never triggered automatically: the diagnostics panel only shows a
button for it, so it costs nothing unless an operator explicitly clicks it.

Reports three independent PASS/FAIL categories - authentication, billing/quota, model
access - plus a short error-category string. Never returns or logs the key, the model's
answer text, or any other response content.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SelfTestResult:
    authentication: str  # "PASS" | "FAIL" | "SKIPPED"
    billing: str
    model_access: str
    error_category: str = ""  # empty on full PASS
    detail: str = ""  # short, secret-free, safe to show


def run_selftest(model: str | None = None) -> SelfTestResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "no_api_key",
                               "OPENAI_API_KEY is not set in this deployment's secrets.")

    from core.demo_llm import demo_model

    resolved_model = (model or "").strip() or demo_model()

    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            NotFoundError,
            OpenAI,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "sdk_not_installed",
                               "The openai package is not installed in this deployment.")

    client = OpenAI(api_key=api_key, timeout=30.0, max_retries=0)

    try:
        # The smallest possible round trip: no system prompt, a one-word input, and a
        # tiny output cap. This still counts as one billed call against the demo project.
        client.responses.create(model=resolved_model, input="ping", max_output_tokens=16, store=False)
        return SelfTestResult("PASS", "PASS", "PASS", "", f"Live call to '{resolved_model}' succeeded.")
    except AuthenticationError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "authentication_failed",
                               "The API key was rejected. It is invalid, revoked, or malformed.")
    except PermissionDeniedError as error:
        return SelfTestResult("PASS", "SKIPPED", "FAIL", "model_access_denied",
                               f"Authenticated, but this key/project cannot use '{resolved_model}'. "
                               "Check the project's model allowlist.")
    except NotFoundError:
        return SelfTestResult("PASS", "SKIPPED", "FAIL", "model_not_found",
                               f"Authenticated, but '{resolved_model}' does not exist or is unavailable "
                               "to this account.")
    except RateLimitError as error:
        message = str(error).lower()
        if "insufficient_quota" in message or "quota" in message or "billing" in message:
            return SelfTestResult("PASS", "FAIL", "SKIPPED", "insufficient_quota",
                                   "Authenticated, but the project has no spend budget/credits available. "
                                   "Check the OpenAI project's usage limits and billing status.")
        return SelfTestResult("PASS", "PASS", "PASS", "rate_limited",
                               "Authenticated and billing is active, but the request was rate-limited. "
                               "Try again shortly, or lower the demo's per-hour/per-day call limits.")
    except APIConnectionError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "connection_error",
                               "Could not reach the OpenAI API from this deployment (network issue).")
    except APITimeoutError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "timeout",
                               "The request to OpenAI timed out.")
    except APIStatusError as error:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", f"api_error_{error.status_code}",
                               "OpenAI returned an unexpected error status.")
    except Exception as error:  # noqa: BLE001 - report, never crash the diagnostics panel
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", type(error).__name__,
                               "An unexpected error occurred. See the deployment's own logs for detail.")
