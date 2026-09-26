"""V2 Phase 10 (public demo environment) — Phase 3 tests: the running app.

Runs the REAL app.py headlessly (Streamlit's AppTest) in demo mode, against
a seeded throwaway database, and drives the landing page and every page of
both workspaces. This is the test that proves the guards do not break the
product a visitor actually sees: a "view" page that quietly tried to write
would surface here as a refusal message or an exception.

The environment tripwire (Postgres-only, provider-secret-free) is covered in
tests/test_phase10_demo_guards.py; here it is bypassed only because the test
database is SQLite.

import _bootstrap first, same as every other file in tests/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (must be first — see _bootstrap.py)

import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

import core.auth as core_auth
import core.demo_tripwire as tripwire
import core.memory as business_memory
from core.db.base import Base
import core.db.models  # noqa: F401
from core.db.models.organization import Organization
from core.db.session import make_engine
from core import demo_llm
from core.demo_mode import DEMO_DISABLED_MESSAGE, DEMO_ERROR_MESSAGE, DEMO_ORG_NAME, allow_demo_writes
from demo.seeder import seed_demo, seeding_environment

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = date(2026, 9, 26)
BANNER = "Demo Workspace — Sample Data Only"


@pytest.fixture(scope="module")
def demo_app():
    """A seeded demo database, the app's services bound to it, and demo mode on."""
    mp = pytest.MonkeyPatch()
    tmp = Path(tempfile.mkdtemp())
    seed_dir = tmp / "seed"
    mp.setattr(business_memory, "DATABASE_FOLDER", tmp / "database")
    # A file database (not :memory:) because AppTest runs the script on another thread.
    engine = make_engine(f"sqlite:///{tmp / 'demo.db'}")
    Base.metadata.create_all(engine)
    seed_demo(engine, anchor=ANCHOR, seed_dir=seed_dir)

    env = seeding_environment(engine, seed_dir=seed_dir)
    env.__enter__()
    import services.agent_collaboration_v23 as council
    import services.jarvis_context as context

    mp.setattr(core_auth, "_V2_ENGINE", engine)
    mp.setattr(demo_llm, "_ENGINE", engine)  # production resolves this from the same DATABASE_URL
    mp.setattr(context, "LEARNING_FILE", seed_dir / "learning_memory.json")
    mp.setattr(council, "STORE", seed_dir / "council_sessions.json")
    mp.setattr(tripwire, "environment_problems", lambda env=None: [])
    for name, value in (("LEADLENS_DEMO_MODE", "1"), ("LEADLENS_V2_AUTH_ENABLED", "1"),
                        ("LEADLENS_V2_TENANT_CONTEXT_ENABLED", "1"),
                        ("OPENAI_API_KEY", "sk-test-not-real")):
        mp.setenv(name, value)
    try:
        yield SimpleNamespaceLike(engine=engine, seed_dir=seed_dir)
    finally:
        env.__exit__(None, None, None)
        mp.undo()
        engine.dispose()


class SimpleNamespaceLike:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _new_app() -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90)
    at.run()
    return at


def _enter(at: AppTest) -> AppTest:
    at.button(key="demo_enter_btn").click().run()
    return at


def _texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [i.value for i in at.info] + [t.value for t in at.title]
    return "\n".join(str(p) for p in parts)


def _problems(at: AppTest) -> list[str]:
    found = [f"exception: {e.message[:200]}" for e in at.exception]
    found += [f"refused: {i.value}" for i in at.info if DEMO_DISABLED_MESSAGE in str(i.value)]
    found += [f"page error: {i.value}" for i in at.info if DEMO_ERROR_MESSAGE in str(i.value)]
    return found


# ---------------------------------------------------------------------------
# Entry flow
# ---------------------------------------------------------------------------

def test_landing_page_offers_the_demo_and_shows_no_login_form(demo_app):
    at = _new_app()
    assert not at.exception
    assert at.button(key="demo_enter_btn").label == "Explore LeadLens Demo"
    assert DEMO_ORG_NAME in _texts(at)
    assert not at.text_input  # no email/password form exists in demo mode
    assert BANNER not in _texts(at)  # the workspace banner only appears once inside


def test_entering_the_demo_shows_the_banner_and_the_workspace(demo_app):
    at = _enter(_new_app())
    assert not at.exception, [e.message for e in at.exception]
    assert BANNER in _texts(at)
    stored = at.session_state["v2_auth_session"]
    assert stored["role"] == "DEMO_VIEWER"
    assert set(stored["permissions"]) <= {
        "organization.view", "patients.view", "appointments.view", "treatments.view", "payments.view",
        "finance.view", "leads.view", "automations.view", "jarvis.use", "jarvis.finance",
        "jarvis.operations", "jarvis.marketing",
    }
    assert not _problems(at), _problems(at)


def test_the_banner_stays_on_every_rerun(demo_app):
    at = _enter(_new_app())
    at.radio(key="jarvis_page").set_value("Patient Intelligence").run()
    assert BANNER in _texts(at)


def test_tampering_with_the_stored_session_cannot_escalate_privileges(demo_app):
    """The session in st.session_state is only a pointer: current_authenticated_session()
    re-derives role and permissions from the database on every access."""
    at = _enter(_new_app())
    forged = dict(at.session_state["v2_auth_session"])
    forged["role"] = "OWNER"
    forged["permissions"] = sorted({"organization.manage", "members.manage", "integrations.manage",
                                    "patients.manage", "automations.approve", "audit.view"})
    at.session_state["v2_auth_session"] = forged
    at.run()
    assert not at.exception
    refreshed = at.session_state["v2_auth_session"]
    assert refreshed["role"] == "DEMO_VIEWER"
    assert "organization.manage" not in refreshed["permissions"]
    assert "automations.approve" not in refreshed["permissions"]


def test_a_session_for_another_organization_is_never_honored(demo_app):
    """Even a forged session naming a different organization id resolves to nothing."""
    at = _enter(_new_app())
    forged = dict(at.session_state["v2_auth_session"])
    forged["organization_id"] = 999
    at.session_state["v2_auth_session"] = forged
    at.run()
    assert not at.exception
    assert at.button(key="demo_enter_btn").label == "Explore LeadLens Demo"  # back to the landing page


def test_landing_page_is_generic_when_the_environment_is_unsafe(demo_app, monkeypatch):
    secret = "SECRET-SENTINEL-DO-NOT-LEAK"
    monkeypatch.setattr(tripwire, "environment_problems", lambda env=None: [f"WHATSAPP_ACCESS_TOKEN present {secret}"])
    at = _new_app()
    assert not at.exception
    text = _texts(at)
    assert "temporarily unavailable" in text
    assert secret not in text and "WHATSAPP" not in text
    assert not at.button  # nothing to click


def test_an_unseeded_database_shows_a_holding_message(demo_app, monkeypatch, tmp_path):
    empty = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with allow_demo_writes():  # creating the schema is an operator action; the guard refuses it otherwise
        Base.metadata.create_all(empty)
    monkeypatch.setattr(core_auth, "_V2_ENGINE", empty)
    at = _new_app()
    assert not at.exception
    assert "being prepared" in _texts(at)
    empty.dispose()


# ---------------------------------------------------------------------------
# Every page of both workspaces renders for a demo visitor
# ---------------------------------------------------------------------------

def test_every_jarvis_page_renders_without_exceptions_or_refusals(demo_app):
    from dashboard import JARVIS_PRIMARY_PAGES, JARVIS_SECONDARY_PAGES

    at = _enter(_new_app())
    failures: dict[str, list[str]] = {}
    for page in JARVIS_PRIMARY_PAGES:
        at.radio(key="jarvis_page").set_value(page).run()
        if _problems(at):
            failures[page] = _problems(at)
    for page in JARVIS_SECONDARY_PAGES:
        at.radio(key="jarvis_secondary_page").set_value(page).run()
        if _problems(at):
            failures[page] = _problems(at)
    assert failures == {}, failures


def test_every_crm_page_renders_without_exceptions_or_refusals(demo_app):
    at = _enter(_new_app())
    at.session_state["workspace_mode"] = "CRM"
    at.run()
    pages = list(at.radio(key="crm_page").options)
    assert "Patients" in pages and "Organization Members" not in pages  # member admin is not offered to a demo viewer
    failures: dict[str, list[str]] = {}
    for page in pages:
        at.radio(key="crm_page").set_value(page).run()
        if _problems(at):
            failures[page] = _problems(at)
    assert failures == {}, failures


def test_the_crm_shows_the_seeded_synthetic_clinic(demo_app):
    at = _enter(_new_app())
    at.session_state["workspace_mode"] = "CRM"
    at.run()
    at.radio(key="crm_page").set_value("Patients").run()
    assert not at.exception
    rendered = " ".join(str(getattr(el, "value", "")) for el in at.markdown) + " ".join(
        str(df.value.to_string()) for df in at.dataframe
    )
    assert "example.com" in rendered or len(at.dataframe) > 0


# ---------------------------------------------------------------------------
# Interaction: nothing a visitor clicks can change anything
# ---------------------------------------------------------------------------

class _FakeResponse:
    output_text = "A concise, grounded answer."
    status = "completed"
    incomplete_details = None


def _install_fake_openai(monkeypatch):
    import openai

    calls: list[dict] = []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = self

        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return calls


def _state_fingerprint(engine) -> dict:
    """Everything that could change if a visitor managed to write something."""
    import hashlib
    import json

    from sqlalchemy import func, select

    counts: dict[str, int] = {}
    with Session(engine) as session:
        for table in Base.metadata.sorted_tables:
            if table.name == "demo_usage":
                continue  # the LLM budget counter is SUPPOSED to move
            counts[table.name] = session.execute(select(func.count()).select_from(table)).scalar()
    business_memory._read_cache_at = 0
    legacy = json.dumps(business_memory.load_memory(), sort_keys=True, default=str)
    return {"tables": counts, "legacy": hashlib.sha256(legacy.encode()).hexdigest()}


_SKIP_BUTTONS = ("Log out", "Explore LeadLens Demo", "Switch Clinic")


def _sweep_page(at: AppTest, select_page, failures: dict) -> int:
    """Clicks every button on the current page, one at a time, re-navigating
    between clicks. Returns how many buttons were clicked."""
    select_page()
    at.run()
    buttons = [(b.key, b.label) for b in at.button
               if not str(b.key or "").startswith(("ll_core", "app_logout")) and b.label not in _SKIP_BUTTONS]
    clicked = 0
    for key, label in buttons:
        select_page()
        at.run()
        candidates = [b for b in at.button if b.key == key and b.label == label] if key else \
                     [b for b in at.button if b.label == label]
        if not candidates:
            continue
        candidates[0].click().run()
        clicked += 1
        if at.exception:
            failures[f"{label!r} ({key})"] = [e.message[:200] for e in at.exception]
    return clicked


def test_clicking_every_button_on_every_page_never_crashes_and_persists_nothing(demo_app, monkeypatch):
    from dashboard import JARVIS_PRIMARY_PAGES, JARVIS_SECONDARY_PAGES

    _install_fake_openai(monkeypatch)
    before = _state_fingerprint(demo_app.engine)
    at = _enter(_new_app())
    failures: dict[str, list[str]] = {}
    clicked = 0

    for page in JARVIS_PRIMARY_PAGES:
        clicked += _sweep_page(at, lambda p=page: at.radio(key="jarvis_page").set_value(p), failures)
    for page in JARVIS_SECONDARY_PAGES:
        clicked += _sweep_page(at, lambda p=page: at.radio(key="jarvis_secondary_page").set_value(p), failures)

    at.session_state["workspace_mode"] = "CRM"
    at.run()
    for page in list(at.radio(key="crm_page").options):
        clicked += _sweep_page(at, lambda p=page: at.radio(key="crm_page").set_value(p), failures)

    assert clicked >= 10, f"the sweep barely clicked anything ({clicked}) — the test is not exercising the UI"
    assert failures == {}, failures
    assert _state_fingerprint(demo_app.engine) == before  # no table and no legacy section changed


def test_ask_jarvis_answers_within_the_demo_budget_and_fails_safe_at_the_cap(demo_app, monkeypatch):
    from sqlalchemy import select

    from core.db.models.demo import DemoUsageCounter

    calls = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("DEMO_OPENAI_MODEL", "demo-cheap-model")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.1")
    monkeypatch.setenv("OPENAI_FAST_MODEL", "gpt-5-mini")
    monkeypatch.setenv("DEMO_LLM_MAX_CALLS_PER_SESSION", "3")

    with Session(demo_app.engine) as session:
        used_before = sum(r.count for r in session.execute(select(DemoUsageCounter)).scalars())

    at = _enter(_new_app())
    at.radio(key="jarvis_page").set_value("Mission Control").run()
    at.text_input[0].set_value("What should I focus on today?")
    [b for b in at.button if b.label == "Ask"][0].click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert calls, "Jarvis never called the model"
    assert all(c["model"] == "demo-cheap-model" for c in calls)  # every call pinned to the demo model
    assert all("tools" not in c for c in calls)  # the model can invoke nothing
    assert any("A concise, grounded answer." in m["content"] for m in at.session_state["jarvis_messages"])

    with Session(demo_app.engine) as session:
        used_after = sum(r.count for r in session.execute(select(DemoUsageCounter)).scalars())
    assert used_after > used_before  # the global budget counter moved

    # Ask again until this session's cap is spent: the answer degrades to a safe message, never an error.
    for _ in range(3):
        at.text_input[0].set_value("And what about tomorrow?")
        [b for b in at.button if b.label == "Ask"][0].click().run()
        assert not at.exception, [e.message for e in at.exception]
    transcript = " ".join(str(m["content"]) for m in at.session_state["jarvis_messages"])
    assert "usage limit has been reached" in transcript


# ---------------------------------------------------------------------------
# Secrets never reach the screen
# ---------------------------------------------------------------------------

def _all_text(at: AppTest) -> str:
    out: list[str] = []

    def walk(node) -> None:
        for attr in ("value", "label", "body", "help", "placeholder", "options"):
            try:
                found = getattr(node, attr, None)
            except Exception:  # noqa: BLE001 - e.g. an unkeyed widget's .value raises KeyError
                continue
            if found is not None:
                out.append(str(found))
        for child in getattr(node, "children", {}).values():
            walk(child)

    walk(at.main)
    walk(at.sidebar)
    return "\n".join(out)


def test_no_page_ever_renders_a_provider_secret_even_if_one_is_in_the_environment(demo_app, monkeypatch):
    """Belt and braces: the tripwire refuses to boot with these variables present, but if it
    were bypassed, the adapters drop them and no page may show them."""
    from dashboard import JARVIS_PRIMARY_PAGES, JARVIS_SECONDARY_PAGES

    sentinels = {
        "WHATSAPP_ACCESS_TOKEN": "SENTINEL-WA-TOKEN-7f3a",
        "WHATSAPP_PHONE_NUMBER_ID": "SENTINEL-WA-PHONE-7f3a",
        "GMAIL_DELEGATED_USER": "sentinel-gmail-7f3a@example.com",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "SENTINEL-GOOGLE-FILE-7f3a.json",
        "GOOGLE_CALENDAR_ID": "SENTINEL-CAL-7f3a",
        "LEADLENS_CREDENTIAL_ENCRYPTION_KEY": "SENTINEL-ENC-KEY-7f3a",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    _install_fake_openai(monkeypatch)

    at = _enter(_new_app())
    seen = [_all_text(at)]
    for page in JARVIS_PRIMARY_PAGES:
        at.radio(key="jarvis_page").set_value(page).run()
        seen.append(_all_text(at))
    for page in JARVIS_SECONDARY_PAGES:
        at.radio(key="jarvis_secondary_page").set_value(page).run()
        seen.append(_all_text(at))
    at.session_state["workspace_mode"] = "CRM"
    at.run()
    for page in list(at.radio(key="crm_page").options):
        at.radio(key="crm_page").set_value(page).run()
        seen.append(_all_text(at))

    everything = "\n".join(seen)
    assert len(everything) > 5000, "the sweep rendered almost nothing"
    for name, value in sentinels.items():
        assert value not in everything, f"{name} leaked onto a page"


# ---------------------------------------------------------------------------
# Inertness: a normal deployment is untouched
# ---------------------------------------------------------------------------

def test_without_demo_mode_the_normal_login_gate_is_unchanged(demo_app, monkeypatch):
    monkeypatch.delenv("LEADLENS_DEMO_MODE")
    at = _new_app()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.text_input) == 2  # the ordinary email + password form
    assert not [b for b in at.button if b.key == "demo_enter_btn"]  # no demo entry exists
    text = _texts(at) + _all_text(at)
    assert BANNER not in text and DEMO_ORG_NAME not in text
