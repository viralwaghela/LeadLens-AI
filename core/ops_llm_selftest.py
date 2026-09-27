"""Operator self-test: one minimal live OpenAI request, run from inside this
deployment. Production/client equivalent of core/demo_llm_selftest.py's demo-only
tool — same idea, but resolves the model the way services/ai.py actually resolves it
for a real call (OPENAI_MODEL, default "gpt-5.1"), not the demo's fixed pin.

Never triggered automatically and shown nowhere by default: app.py only renders the
button when the operator sets LEADLENS_OPS_DIAGNOSTICS, which defaults unset. Reports
three independent PASS/FAIL categories - authentication, billing/quota, model access -
plus the exact HTTP status / error.type / error.code / error.message OpenAI's SDK
attaches to any APIStatusError. Never returns or logs the key, the model's answer
text, or any other response content.
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
    http_status: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def configured_model() -> str:
    """The exact model name services/ai.py::generate_ai_response() resolves to when
    no per-call override is given — i.e. what this deployment actually calls."""
    return os.getenv("OPENAI_MODEL", "gpt-5.1").strip() or "gpt-5.1"


def run_selftest(model: str | None = None) -> SelfTestResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "no_api_key",
                               "OPENAI_API_KEY is not set in this deployment's secrets.")

    resolved_model = (model or "").strip() or configured_model()

    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            OpenAI,
            RateLimitError,
        )
    except ImportError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "sdk_not_installed",
                               "The openai package is not installed in this deployment.")

    client = OpenAI(api_key=api_key, timeout=30.0, max_retries=0)

    def _from_status_error(error, *, auth: str, billing: str, model_access: str, category: str, detail: str) -> SelfTestResult:
        return SelfTestResult(
            auth, billing, model_access, category, detail,
            http_status=getattr(error, "status_code", None),
            error_type=getattr(error, "type", None),
            error_code=getattr(error, "code", None),
            error_message=getattr(error, "message", None) or str(error),
        )

    try:
        # The smallest possible round trip: no system prompt, a one-word input, and a
        # tiny output cap. This still counts as one billed call against this project.
        client.responses.create(model=resolved_model, input="ping", max_output_tokens=16, store=False)
        return SelfTestResult("PASS", "PASS", "PASS", "", f"Live call to '{resolved_model}' succeeded.")
    except AuthenticationError as error:
        return _from_status_error(error, auth="FAIL", billing="SKIPPED", model_access="SKIPPED",
                                   category="authentication_failed",
                                   detail="The API key was rejected. It is invalid, revoked, or malformed.")
    except RateLimitError as error:
        message = (getattr(error, "message", None) or str(error)).lower()
        if "insufficient_quota" in message or "quota" in message or "billing" in message:
            return _from_status_error(error, auth="PASS", billing="FAIL", model_access="SKIPPED",
                                       category="insufficient_quota",
                                       detail="Authenticated, but the project has no spend budget/credits available.")
        return _from_status_error(error, auth="PASS", billing="PASS", model_access="PASS",
                                   category="rate_limited", detail="Authenticated and billing is active, but rate-limited.")
    except APIStatusError as error:
        # Covers PermissionDeniedError (403), NotFoundError (404), and anything else —
        # reported uniformly with the exact fields OpenAI sent, per the request format.
        status = getattr(error, "status_code", None)
        if status == 403:
            category, detail = "model_access_denied", (
                f"Authenticated, but this key/project cannot use '{resolved_model}'. "
                "Check the project's model allowlist and organization access."
            )
        elif status == 404:
            category, detail = "model_not_found", f"'{resolved_model}' does not exist or is unavailable to this account."
        else:
            category, detail = f"api_error_{status}", "OpenAI returned an unexpected error status."
        return _from_status_error(error, auth="PASS", billing="SKIPPED", model_access="FAIL",
                                   category=category, detail=detail)
    except APITimeoutError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "timeout", "The request to OpenAI timed out.")
    except APIConnectionError:
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", "connection_error",
                               "Could not reach the OpenAI API from this deployment (network issue).")
    except Exception as error:  # noqa: BLE001 - report, never crash the diagnostics panel
        return SelfTestResult("FAIL", "SKIPPED", "SKIPPED", type(error).__name__,
                               "An unexpected error occurred. See the deployment's own logs for detail.")
