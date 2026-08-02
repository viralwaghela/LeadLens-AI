"""
Standalone test for the "core" workspace switch — v2.

    streamlit run test_core_switch_v2.py

v1 hid the button under the core using a negative margin and a
`st-key-*` class. That didn't work, so this version starts with a plain
visible button (guaranteed to work on any Streamlit version), and lets
you turn the fancy overlay on from the sidebar to see whether it works
on YOUR version specifically.

Nothing here imports from the LeadLens codebase, writes to any database,
or reads .env. Pure visual sandbox — delete when done.
"""
import streamlit as st

st.set_page_config(page_title="Core switch test v2", layout="wide")

if "mode" not in st.session_state:
    st.session_state.mode = "crm"

is_jarvis = st.session_state.mode == "jarvis"

st.markdown(
    """
<style>
.ll-core-wrap{display:flex;flex-direction:column;align-items:center;
  gap:12px;padding:6px 0 2px;user-select:none}

.ll-rig{position:relative;width:44px;height:44px;display:grid;place-items:center;
  transition:width .45s cubic-bezier(.4,0,.2,1),height .45s cubic-bezier(.4,0,.2,1)}
.ll-rig.on{width:70px;height:70px}

.ll-ring{position:absolute;inset:0;border-radius:50%;border:1px solid #C3CEDE;transition:.5s}
.ll-ring.i2{inset:6px;border-color:#DDE3EC}
.ll-rig.on .ll-ring.i2{inset:9px}

.ll-seg{position:absolute;inset:9px;border-radius:50%;border:2px dashed #C3CEDE;
  animation:ll-spin 15s linear infinite;transition:border-color .5s}
.ll-rig.on .ll-seg{inset:15px}
@keyframes ll-spin{to{transform:rotate(360deg)}}

.ll-well{position:absolute;inset:14px;border-radius:50%;background:#E2E8F0;
  box-shadow:inset 0 0 6px rgba(148,163,184,.4);transition:.5s}
.ll-rig.on .ll-well{inset:23px}

.ll-lit{position:absolute;inset:18px;border-radius:50%;background:#B4C0D2;transition:.5s}
.ll-rig.on .ll-lit{inset:30px}

.ll-label{font-size:9.5px;font-weight:750;letter-spacing:.15em;color:#8494AD;transition:.4s}
.ll-sub{font-size:9.5px;color:#A3AEC0;margin-top:-8px;height:12px}

.ll-rig.on .ll-ring{border-color:#3D6FD0;box-shadow:0 0 18px rgba(61,111,208,.32)}
.ll-rig.on .ll-ring.i2{border-color:#5B87E0}
.ll-rig.on .ll-seg{border-color:#6E9BFF;animation-duration:5s}
.ll-rig.on .ll-well{background:#17325E;
  box-shadow:inset 0 0 12px #0A1830,0 0 22px rgba(110,155,255,.4)}
.ll-rig.on .ll-lit{background:#9CC0FF;
  box-shadow:0 0 20px #6E9BFF,0 0 44px rgba(110,155,255,.62)}
.ll-label.on{color:#B9D0FF;letter-spacing:.19em}
.ll-sub.on{color:#6E9BFF}
</style>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("#### ✦ LeadLens CareOS")
    st.caption("Beyond Pain · Malad")

    on = " on" if is_jarvis else ""
    sub_text = "return to records" if is_jarvis else "click to bring online"

    st.markdown(
        f"""
<div class="ll-core-wrap">
  <div class="ll-rig{on}">
    <div class="ll-ring"></div>
    <div class="ll-ring i2"></div>
    <div class="ll-seg"></div>
    <div class="ll-well"></div>
    <div class="ll-lit"></div>
  </div>
  <div class="ll-label{on}">JARVIS</div>
  <div class="ll-sub{on}">{sub_text}</div>
</div>
""",
        unsafe_allow_html=True,
    )

    # ---- the actual control: plain, visible, guaranteed to work ----
    button_text = "← Back to Records" if is_jarvis else "Bring Jarvis online →"
    if st.button(button_text, key="core_btn", use_container_width=True):
        st.session_state.mode = "crm" if is_jarvis else "jarvis"
        st.rerun()

    st.divider()

    nav = (["Mission Control", "Patient Intelligence", "AI Team",
            "Workflows", "Integrations", "Approvals"] if is_jarvis
           else ["Patients", "Appointments", "Packages", "Therapists", "Leads"])
    for item in nav:
        st.write(f"○ {item}")

    st.divider()
    st.caption(f"Streamlit version: `{st.__version__}`")

    # ---- optional: try the invisible-overlay version ----
    overlay = st.checkbox("Try invisible overlay", value=False,
                          help="Hides the button and puts it on top of the core. "
                               "Only works if your Streamlit version supports "
                               "st-key-* classes.")
    offset = st.slider("Overlay offset (px)", -220, 0, -104, 4,
                       disabled=not overlay,
                       help="How far up to pull the button. Adjust until the "
                            "hover highlight lines up with the core.")

if overlay:
    st.markdown(
        f"""
<style>
.st-key-core_btn{{margin-top:{offset}px;position:relative;z-index:5}}
.st-key-core_btn button{{
  height:104px;background:transparent!important;border:none!important;
  color:transparent!important;box-shadow:none!important;cursor:pointer}}
.st-key-core_btn button:hover{{
  background:rgba(110,155,255,.08)!important;border-radius:12px}}
.st-key-core_btn button:focus-visible{{
  outline:2px solid #6E9BFF!important;outline-offset:2px;border-radius:12px}}
</style>
""",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------- main
if is_jarvis:
    st.title("Good morning, Viral.")
    st.caption("3 things need you today · JARVIS workspace")
else:
    st.title("Patients")
    st.caption("1 active · 0 inactive · 1 renewal due · CRM workspace")

cols = st.columns(4)
labels = (["Active", "At risk", "Approvals", "Utilisation"]
          if is_jarvis else ["Total", "Active", "Sessions", "Due"])
values = ["1", "1", "25", "38%"] if is_jarvis else ["1", "1", "2", "1"]
for col, label, value in zip(cols, labels, values):
    col.metric(label, value)

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.markdown("**AI Team Status**" if is_jarvis else "**Recent patients**")
        st.write("—")
with right:
    with st.container(border=True):
        st.markdown("**Approval Queue**" if is_jarvis else "**Today's schedule**")
        st.write("—")

st.divider()
st.caption(
    "Sandbox only — no database, no .env, nothing shared with the real app. "
    f"Current mode: **{st.session_state.mode}**"
)
