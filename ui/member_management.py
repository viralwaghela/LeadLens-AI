"""Phase 7 — minimal organization member management UI.

Only reachable when V2 auth is enabled and the current user has
members.view (dashboard.py only adds this page to the sidebar in that
case) — but the backend (services/member_management.py) is the
authoritative check regardless, per docs/V2_PHASE7_AUTH_CUTOVER.md's
"backend-first authorization" design. No invitation emails, no
self-signup — an authorized admin creates the account directly, using
the same Phase 1 Argon2id password hashing every other account uses.
"""
from __future__ import annotations

import streamlit as st

from core.db.models.identity import MembershipRole
from core.db.session import session_scope
from core.identity.session import get_stored_session


def _engine():
    from services.integration_credentials import _get_engine

    return _get_engine()


def show_member_management() -> None:
    st.subheader("Organization Members")

    session = get_stored_session()
    if session is None:
        st.warning("Sign in to manage organization members.")
        return

    from core.auth import current_authenticated_session
    from core.identity.tenant_context import TenantContext, ActorType
    from services import member_management as mm

    with session_scope(_engine()) as db_session:
        live = current_authenticated_session()
        if live is None:
            st.warning("Your session is no longer valid — please sign in again.")
            return
        tenant_context = TenantContext(
            organization_id=live.organization_id, actor_type=ActorType.USER,
            user_id=live.user_id, membership_id=live.membership_id, role=live.role,
            permissions=live.permissions,
        )

        try:
            members = mm.list_members(db_session, tenant_context)
        except Exception as exc:  # noqa: BLE001 - PermissionDenied or similar
            st.error(f"You do not have access to view members. ({exc})")
            return

        st.table(
            [
                {"Email": m.email, "Role": m.role, "Status": m.status}
                for m in members
            ]
        )

        if "members.manage" not in live.permissions:
            st.caption("Only Owners/Admins can add or change members.")
            return

        st.divider()
        st.markdown("**Add a member**")
        with st.form("add_member_form"):
            email = st.text_input("Email")
            password = st.text_input("Temporary password", type="password")
            role = st.selectbox("Role", [r.value for r in MembershipRole])
            submitted = st.form_submit_button("Add member", type="primary")
        if submitted:
            if not email.strip() or not password.strip():
                st.error("Email and password are required.")
            else:
                try:
                    mm.create_member(
                        db_session, tenant_context, email=email.strip(), password=password,
                        role=MembershipRole(role),
                    )
                    db_session.commit()
                    st.success(f"Added {email.strip()} as {role}.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 - surfaced directly, no sensitive detail involved
                    st.error(str(exc))

        st.divider()
        st.markdown("**Update a member**")
        options = {f"{m.email} ({m.role}, {m.status})": m.membership_id for m in members}
        if options:
            with st.form("update_member_form"):
                chosen = st.selectbox("Member", list(options.keys()))
                new_role = st.selectbox("New role", [r.value for r in MembershipRole], key="new_role_select")
                col_a, col_b = st.columns(2)
                change_role_clicked = col_a.form_submit_button("Change role", use_container_width=True)
                disable_clicked = col_b.form_submit_button("Disable", use_container_width=True)
            membership_id = options[chosen]
            if change_role_clicked:
                try:
                    mm.change_member_role(db_session, tenant_context, membership_id, MembershipRole(new_role))
                    db_session.commit()
                    st.success("Role updated.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
            if disable_clicked:
                try:
                    mm.disable_member(db_session, tenant_context, membership_id)
                    db_session.commit()
                    st.success("Member disabled.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
