from __future__ import annotations

from urllib.parse import urlencode

import streamlit as st

from core.auth import reload_token


def _force_streamlit_theme(desired: str) -> None:
    """Force Streamlit's OWN resolved theme (Settings > Light/Dark/System)
    to `desired` ("Light" or "Dark"), so widgets the CSS below can't reach
    — st.dataframe in particular renders its grid to a <canvas> via
    glide-data-grid, which does NOT read Streamlit's stored theme
    preference for its own colors; verified in production (2026-08-02)
    that it tracks the viewer's OS/browser light-dark setting directly,
    live, independent of anything set here.

    The only mechanism that actually overrides it is Streamlit's own
    first-class embed theme control: the `embed_options=light_theme` /
    `dark_theme` query params (only valid together with `embed=true`),
    which authoritatively fix the resolved theme for every consumer,
    canvas widgets included. `embed=true` alone would also hide
    Streamlit's own toolbar/header and remove page padding, so
    `show_toolbar` and `show_padding` are included to keep the app's
    normal chrome (Streamlit's embed_options whitelist has no option to
    hide the sidebar, so nothing here touches sidebar behavior).

    `embed`/`embed_options` cannot be set via st.query_params — Streamlit
    explicitly rejects programmatic writes to those two keys — so getting
    there requires an actual URL navigation, not a Streamlit-internal
    rerun. Two approaches were tried and rejected before this one:

    1. A <script> inside components.html()/st.iframe(), navigating
       window.parent.location. Streamlit's own iframe for both of these
       is sandboxed WITHOUT `allow-top-navigation` (verified via the
       rendered iframe's `sandbox` attribute) — location.reload() still
       worked (it doesn't count as cross-document navigation), but
       assigning a new location.search silently did nothing, no console
       error, because changing the *target* URL is exactly what that
       sandbox flag exists to block.
    2. st.link_button, whose rendered <a> lives in the real top document
       (unsandboxed) — but it opens the URL in a new tab with no way to
       target the current one, which would pile up tabs on every switch.

    This renders a plain <meta http-equiv="refresh"> tag via
    st.markdown(unsafe_allow_html=True) instead. That HTML lands directly
    in the top-level document (not a nested iframe), and — verified
    directly — a browser still executes a meta-refresh even when it's
    inserted into the page after the fact via innerHTML, unlike <script>
    content. The "is this already correct" comparison happens here in
    Python against st.query_params, before deciding whether to render the
    tag at all, which is what keeps this from becoming a reload loop.

    Because this is a genuine browser navigation and not a Streamlit
    rerun, it starts a brand-new session — st.session_state, including
    core.auth's login flag, doesn't survive it. Carrying reload_token()
    through the same URL lets require_login() recognize "this session
    already passed the password check a moment ago" without storing
    anything a viewer could forge from the client side (see core/auth.py).
    """
    embed_theme = "dark_theme" if desired == "Dark" else "light_theme"
    workspace_value = "jarvis" if desired == "Dark" else "crm"

    # st.query_params cannot read back "embed" or "embed_options" — Streamlit
    # deliberately filters both out of every read path (get, get_all, items),
    # even though they can still be set via a real URL navigation. Checking
    # already_correct against those two directly always sees them as absent
    # and reloads forever. "_theme" is an ordinary, non-reserved param that
    # rides along in the same URL and IS readable, so it stands in as the
    # signal for "has this redirect already happened."
    params = st.query_params
    already_correct = (
        params.get("_theme") == embed_theme
        and params.get("workspace", "").lower() == workspace_value
    )
    if already_correct:
        return

    target = "?" + urlencode(
        [
            ("workspace", workspace_value),
            ("_theme", embed_theme),
            ("_auth", reload_token()),
            ("embed", "true"),
            ("embed_options", "show_toolbar"),
            ("embed_options", "show_padding"),
            ("embed_options", embed_theme),
        ]
    )
    st.markdown(
        f'<meta http-equiv="refresh" content="0; url={target}">',
        unsafe_allow_html=True,
    )


def apply_workspace_theme(mode: str) -> None:
    is_jarvis = mode == "JARVIS"
    marker = "jarvis-mode-marker" if is_jarvis else "crm-mode-marker"
    _force_streamlit_theme("Dark" if is_jarvis else "Light")
    st.markdown(f'<div class="{marker}"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <style>
        :root {
            --crm-ink:#101828; --crm-muted:#667085; --crm-blue:#1667e8;
            --crm-blue-2:#2f80ed; --crm-line:#e4eaf2; --crm-soft:#f7f9fc;
            --jarvis-bg:#010712; --jarvis-panel:#061322; --jarvis-panel-2:#081a30;
            --jarvis-line:rgba(30,144,255,.30); --jarvis-blue:#0984ff;
            --jarvis-cyan:#35d9ff; --jarvis-text:#eaf7ff; --jarvis-muted:#8fb4d2;
        }
        .block-container {max-width:1540px;padding-top:1.15rem;padding-bottom:5rem;}
        h1,h2,h3 {letter-spacing:-.035em;}
        [data-testid="stSidebar"] {width:19.5rem!important;min-width:19.5rem!important;}
        [data-testid="stSidebar"]>div:first-child {width:19.5rem!important;}
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {padding-bottom:2rem;}
        .sidebar-brand{padding:.35rem .1rem .7rem}.sidebar-brand h2{margin:0;font-size:1.25rem}.sidebar-brand p{margin:.15rem 0 0;font-size:.76rem;opacity:.7}
        .eyebrow{font-size:.68rem;font-weight:850;letter-spacing:.15em;margin-bottom:.28rem}
        [data-testid="stMetric"]{border-radius:14px;padding:1rem 1.05rem;min-height:112px}
        .stButton>button,.stDownloadButton>button,[data-testid="stFormSubmitButton"]>button{border-radius:10px;font-weight:700}
        div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px}
        [data-testid="stHorizontalBlock"]{align-items:flex-start}
        .stChatMessage{border-radius:14px;padding:.35rem .65rem}

        /* The Core — single JARVIS/CRM toggle (see CORE_SWITCH_SPEC.md).
           Dormant in CRM, ignited in JARVIS; clicking toggles. A real
           st.button sits invisibly on top of the custom .ll-core rig,
           because raw HTML in st.markdown cannot trigger a Streamlit rerun
           on its own — the actual click target has to be a genuine
           Streamlit widget. Scoped via st.container(key=...)'s official
           st-key-* class (applied directly to that container's own wrapper
           element by Streamlit itself) rather than a :has() selector
           guessing at internal data-testid DOM nesting.

           Overlaid with CSS Grid stacking (both direct children placed in
           grid cell 1/1) rather than position:absolute + inset:0 — the
           inset/auto-width stretch approach measurably failed to size the
           button to its container in testing (verified via
           getBoundingClientRect: it collapsed to a tiny intrinsic size
           instead of filling the cell, for reasons that didn't match the
           CSS abspos-in-auto-height-flex-container spec on paper — likely
           an interaction with Streamlit's own .stButton width:100% rule
           that inset-stretch loses to in practice). Grid-cell stacking
           sizes both children to the same definite cell dimensions up
           front, which plain percentage sizing (width:100%;height:100%)
           can then safely reference — no stretch-vs-explicit-width fight
           possible. */
        .st-key-ll_core_switch{
            display:grid;justify-items:center;
            padding:.7rem 0 .6rem;margin:.25rem 0 .85rem;
        }
        .st-key-ll_core_switch>div{
            grid-area:1/1;
        }
        /* The button's own wrapping stElementContainer defaults to its
           natural ~40px button height instead of stretching to the grid
           row (Streamlit's own element-container styling caps it) —
           forced explicitly since only THIS child (not the visual one,
           which must keep sizing the row from its own content) needs it. */
        .st-key-ll_core_switch>div:has([data-testid="stButton"]){
            width:100%!important;height:100%!important;
        }
        .st-key-ll_core_switch [data-testid="stButton"]{
            width:100%;height:100%;margin:0;z-index:2;
        }
        .st-key-ll_core_switch [data-testid="stButton"] button{
            width:100%;height:100%;padding:0;margin:0;border:none;background:transparent;
            box-shadow:none;opacity:0;cursor:pointer;
        }
        /* Real keyboard focus lands on the invisible button (opacity, unlike
           visibility:hidden, keeps it in the accessibility tree and
           tabbable) — its own focus outline would be invisible too since
           opacity hides everything painted on that layer, so the visible
           focus ring is drawn on the rig instead via :focus-within. */
        .st-key-ll_core_switch:focus-within .ll-core-rig{
            outline:2px solid #6E9BFF;outline-offset:5px;border-radius:50%;
        }

        /* Reference implementation from CORE_SWITCH_SPEC.md — already
           verified working in-browser; values used exactly as given. */
        .ll-core{display:flex;flex-direction:column;align-items:center;gap:15px;cursor:pointer;user-select:none;padding:6px 0}
        .ll-core-rig{position:relative;width:74px;height:74px;display:grid;place-items:center}
        .ll-ring{position:absolute;inset:0;border-radius:50%;border:1px solid #23324F;transition:.5s}
        .ll-ring:nth-child(2){inset:9px;border-color:#1D2A44}
        .ll-seg{position:absolute;inset:15px;border-radius:50%;border:2px dashed #2A3B5C;animation:ll-spin 15s linear infinite;transition:border-color .5s}
        @keyframes ll-spin{to{transform:rotate(360deg)}}
        .ll-well{position:absolute;inset:24px;border-radius:50%;background:#1A2740;box-shadow:inset 0 0 8px #0A0F1C;transition:.5s}
        .ll-lit{position:absolute;inset:31px;border-radius:50%;background:#2C3E60;transition:.5s}
        .ll-core-label{font-size:10.5px;font-weight:750;letter-spacing:.16em;color:#4E5D75;transition:.4s}
        .ll-core-sub{font-size:10px;color:#3C4A63;margin-top:-9px}

        /* ignited */
        .ll-core[data-state="on"] .ll-ring{border-color:#3D6FD0;box-shadow:0 0 18px rgba(61,111,208,.32)}
        .ll-core[data-state="on"] .ll-ring:nth-child(2){border-color:#5B87E0}
        .ll-core[data-state="on"] .ll-seg{border-color:#6E9BFF;animation-duration:5s}
        .ll-core[data-state="on"] .ll-well{background:#17325E;box-shadow:inset 0 0 12px #0A1830,0 0 22px rgba(110,155,255,.4)}
        .ll-core[data-state="on"] .ll-lit{background:#9CC0FF;box-shadow:0 0 20px #6E9BFF,0 0 44px rgba(110,155,255,.62)}
        .ll-core[data-state="on"] .ll-core-label{color:#B9D0FF;letter-spacing:.2em}

        @media (prefers-reduced-motion: reduce){
            .ll-seg{animation:none}
            .ll-ring,.ll-well,.ll-lit,.ll-core-label{transition:none}
        }

        /* CRM */
        .stApp:has(.crm-mode-marker){background:#f7f9fc;color:var(--crm-ink)}
        .stApp:has(.crm-mode-marker) [data-testid="stHeader"]{background:rgba(247,249,252,.88)}
        .stApp:has(.crm-mode-marker) [data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--crm-line)}
        .stApp:has(.crm-mode-marker) .eyebrow{color:var(--crm-blue)}
        .stApp:has(.crm-mode-marker) [data-testid="stMetric"],.stApp:has(.crm-mode-marker) .stChatMessage,.stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlockBorderWrapper"]{background:#fff;border:1px solid var(--crm-line);box-shadow:0 5px 20px rgba(16,24,40,.035)}
        .crm-hero{padding:1.35rem 1.5rem;border:1px solid var(--crm-line);border-radius:18px;background:linear-gradient(135deg,#fff 0%,#f4f8ff 100%);box-shadow:0 10px 34px rgba(16,24,40,.045);margin-bottom:1rem}
        .crm-hero h1{margin:.12rem 0 .3rem;font-size:2rem}.crm-hero p{margin:0;color:var(--crm-muted)}
        .crm-section-title{font-size:1rem;font-weight:800;color:#172033;margin:.25rem 0 .75rem}
        .crm-card{background:#fff;border:1px solid var(--crm-line);border-radius:14px;padding:1rem 1.05rem;min-height:114px;box-shadow:0 5px 20px rgba(16,24,40,.03)}
        .crm-card-label{font-size:.72rem;color:#667085;margin-bottom:.6rem}.crm-card-value{font-size:1.65rem;font-weight:850;color:#101828}.crm-card-delta{font-size:.7rem;color:#12a150;margin-top:.35rem}
        .jarvis-tip{padding:1rem 1.15rem;border-radius:14px;border:1px solid #cfe0fb;background:linear-gradient(135deg,#edf5ff,#fbfdff);color:#20304e}.jarvis-tip strong{color:#155fc7}
        .stApp:has(.crm-mode-marker) input,.stApp:has(.crm-mode-marker) textarea,.stApp:has(.crm-mode-marker) [data-baseweb="select"]>div{background:#fff!important;color:var(--crm-ink)!important;border-color:var(--crm-line)!important}
        .stApp:has(.crm-mode-marker) .stButton>button,.stApp:has(.crm-mode-marker) .stDownloadButton>button{color:var(--crm-ink);background:#fff;border:1px solid var(--crm-line)}

        /* JARVIS */
        .stApp:has(.jarvis-mode-marker){background:radial-gradient(circle at 82% 3%,rgba(0,115,255,.18),transparent 28%),radial-gradient(circle at 10% 95%,rgba(0,206,255,.10),transparent 32%),linear-gradient(145deg,#010611 0%,#020b18 48%,#05152a 100%);color:var(--jarvis-text)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stHeader"]{background:rgba(1,6,17,.78)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stSidebar"]{background:radial-gradient(circle at 50% 0%,rgba(0,125,255,.13),transparent 28%),linear-gradient(180deg,#020814,#041226);border-right:1px solid var(--jarvis-line)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stSidebar"] *,.stApp:has(.jarvis-mode-marker) h1,.stApp:has(.jarvis-mode-marker) h2,.stApp:has(.jarvis-mode-marker) h3,.stApp:has(.jarvis-mode-marker) p,.stApp:has(.jarvis-mode-marker) label{color:var(--jarvis-text)}
        .stApp:has(.jarvis-mode-marker) .eyebrow{color:var(--jarvis-cyan)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stMetric"],.stApp:has(.jarvis-mode-marker) .stChatMessage,.stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlockBorderWrapper"]{background:linear-gradient(145deg,rgba(5,17,32,.97),rgba(7,29,52,.88));border:1px solid var(--jarvis-line);box-shadow:inset 0 0 32px rgba(0,125,255,.035)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stMetricLabel"],.stApp:has(.jarvis-mode-marker) [data-testid="stMetricValue"],.stApp:has(.jarvis-mode-marker) [data-testid="stMetricDelta"],.stApp:has(.jarvis-mode-marker) [data-testid="stCaptionContainer"]{color:#d5edff}
        .stApp:has(.jarvis-mode-marker) input,.stApp:has(.jarvis-mode-marker) textarea,.stApp:has(.jarvis-mode-marker) [data-baseweb="select"]>div{background:#06172b!important;color:#eaf7ff!important;border-color:rgba(52,173,255,.30)!important}
        .stApp:has(.jarvis-mode-marker) .stButton>button,.stApp:has(.jarvis-mode-marker) .stDownloadButton>button{color:#e3f8ff;background:linear-gradient(135deg,#071f3b,#0a3159);border:1px solid rgba(48,174,255,.40)}
        .jarvis-hero{display:grid;grid-template-columns:88px 1fr;align-items:center;gap:1.15rem;padding:1.45rem;border-radius:16px;border:1px solid rgba(31,145,255,.42);background:radial-gradient(circle at 88% 12%,rgba(0,146,255,.20),transparent 34%),linear-gradient(135deg,rgba(3,15,29,.98),rgba(5,34,62,.90));box-shadow:0 24px 80px rgba(0,0,0,.32),inset 0 0 45px rgba(0,132,255,.06);margin-bottom:1rem}
        .jarvis-hero h1{margin:.1rem 0 .25rem;color:#f2fbff}.jarvis-hero p{margin:0;color:#9fc5e1}.jarvis-status{color:#4edfff;font-size:.66rem;font-weight:900;letter-spacing:.17em}
        .jarvis-orb{width:74px;height:74px;border-radius:50%;display:grid;place-items:center;font-size:1.7rem;color:#e5ffff;background:radial-gradient(circle,#efffff 0 5%,#36dcff 7% 13%,#087bf2 16% 34%,#04172f 37% 52%,#0aa8ee 55% 58%,#031020 62%);border:1px solid #49dfff;box-shadow:0 0 18px #058eff,0 0 48px rgba(13,164,255,.55)}
        .jarvis-panel-title{font-size:.92rem;font-weight:850;color:#eaf7ff;margin-bottom:.65rem}.agent-chip{border:1px solid rgba(31,145,255,.28);background:linear-gradient(145deg,#06162a,#08213a);border-radius:13px;padding:.9rem;text-align:center;min-height:118px}.agent-orb{width:38px;height:38px;border-radius:50%;margin:0 auto .5rem;display:grid;place-items:center;background:radial-gradient(circle,#dffcff 0 7%,#16cfff 9% 20%,#0877eb 25% 48%,#03152b 51%);box-shadow:0 0 18px rgba(0,160,255,.48)}.agent-name{font-size:.73rem;font-weight:800}.agent-state{font-size:.62rem;color:#31e29d;margin-top:.28rem}.jarvis-brief-item{padding:.62rem 0;border-bottom:1px solid rgba(31,145,255,.14);font-size:.76rem;color:#cce9ff}.jarvis-brief-item:last-child{border-bottom:none}
        .stApp:has(.jarvis-mode-marker) [data-testid="stChatInput"]{position:sticky;bottom:.75rem;z-index:100;background:rgba(3,14,29,.94);border:1px solid rgba(31,145,255,.45);border-radius:15px;box-shadow:0 0 32px rgba(0,132,255,.18)}
        @media(max-width:900px){[data-testid="stSidebar"],[data-testid="stSidebar"]>div:first-child{width:18rem!important;min-width:18rem!important}.jarvis-hero{grid-template-columns:1fr}}

        /* ---------- Jarvis Mission Control — extended layout ---------- */
        .jv-topbar{display:flex;align-items:center;justify-content:space-between;padding:.2rem .1rem .95rem}
        .jv-topbar-status{display:flex;align-items:center;gap:.5rem;margin:0 auto;color:#5fe3ff;font-size:.72rem;font-weight:900;letter-spacing:.2em;text-transform:uppercase}
        .jv-topbar-status .jv-dash{width:22px;height:1px;background:linear-gradient(90deg,transparent,#2fc3ff)}
        .jv-topbar-status .jv-dash.right{background:linear-gradient(90deg,#2fc3ff,transparent)}
        .jv-topbar-right{display:flex;align-items:center;gap:.6rem}
        .jv-ai-status{display:inline-flex;align-items:center;gap:.45rem;padding:.4rem .85rem;border-radius:999px;font-size:.68rem;font-weight:800;letter-spacing:.03em;border:1px solid rgba(52,173,255,.3);background:linear-gradient(145deg,#06172c,#081f38);color:#d5edff}
        .jv-ai-status .jv-live-dot{width:7px;height:7px;border-radius:50%}
        .jv-ai-status.ok .jv-live-dot{background:#31e29d;box-shadow:0 0 8px rgba(49,226,157,.85)}
        .jv-ai-status.ok{color:#9ff5cf}
        .jv-ai-status.warn .jv-live-dot{background:#ffb84d;box-shadow:0 0 8px rgba(255,184,77,.85)}
        .jv-ai-status.warn{color:#ffd9a0;border-color:rgba(255,184,77,.4)}
        .jv-icon-btn{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;border:1px solid rgba(52,173,255,.30);background:linear-gradient(145deg,#06172c,#081f38);color:#bfe7ff}
        .jv-avatar-chip{display:flex;align-items:center;gap:.55rem;padding:.3rem .8rem .3rem .3rem;border-radius:999px;border:1px solid rgba(52,173,255,.30);background:linear-gradient(145deg,#06172c,#081f38)}
        .jv-avatar-circle{width:30px;height:30px;border-radius:50%;background:radial-gradient(circle,#dffcff 0 6%,#23d6ff 8% 20%,#087df5 24% 60%,#03152d 64%);display:grid;place-items:center;color:#03152d;font-weight:900;font-size:.72rem}
        .jv-avatar-chip .jv-avatar-name{font-size:.78rem;font-weight:800;color:#eaf7ff;line-height:1.05}
        .jv-avatar-chip .jv-avatar-role{font-size:.63rem;color:#7fa9c8}

        .jv-hero{position:relative;overflow:hidden;padding:1.5rem 1.6rem;border-radius:18px;border:1px solid rgba(31,145,255,.42);background:radial-gradient(circle at 88% 12%,rgba(0,146,255,.20),transparent 34%),linear-gradient(135deg,rgba(3,15,29,.98),rgba(5,34,62,.90));box-shadow:0 24px 80px rgba(0,0,0,.32),inset 0 0 45px rgba(0,132,255,.06);margin-bottom:1.1rem}
        .jv-hero-row{display:grid;grid-template-columns:82px 1fr;gap:1.15rem;align-items:center;position:relative;z-index:2}
        .jv-hero-row h1{margin:.1rem 0 .3rem;color:#f2fbff;font-size:1.85rem}
        .jv-hero-row p{margin:0;color:#9fc5e1;font-size:.92rem;max-width:640px}
        .jv-hero-chips{display:flex;gap:.6rem;margin-top:1rem;flex-wrap:wrap;position:relative;z-index:2}
        .jv-chip{display:inline-flex;align-items:center;gap:.45rem;padding:.5rem .95rem;border-radius:999px;border:1px solid rgba(52,173,255,.35);background:rgba(3,17,33,.72);color:#d5edff;font-size:.78rem;font-weight:650}
        .jv-chip svg{color:#4edfff;flex:none}
        .jv-hero-ask{flex:1;min-width:220px;display:flex;align-items:center;gap:.55rem;padding:.5rem 1rem;border-radius:999px;border:1px solid rgba(52,173,255,.35);background:rgba(3,17,33,.72);color:#7fa9c8;font-size:.8rem}
        .jv-hero-ask svg{color:#4edfff;flex:none}
        .jv-hero-ask .jv-mic{margin-left:auto;color:#4edfff}
        .jv-hero-rings{position:absolute;right:-40px;top:50%;transform:translateY(-50%);width:280px;height:280px;z-index:1;opacity:.9}
        .jv-hero-rings::before,.jv-hero-rings::after{content:"";position:absolute;border-radius:50%;border:1px solid rgba(52,173,255,.28)}
        .jv-hero-rings::before{inset:20px;border-color:rgba(52,173,255,.22)}
        .jv-hero-rings::after{inset:60px;border-color:rgba(52,173,255,.34)}
        .jv-hero-rings .jv-ring-core{position:absolute;inset:100px;border-radius:50%;background:radial-gradient(circle,#dffcff 0 6%,#23d6ff 9% 22%,#087df5 26% 55%,#03152d 60%);box-shadow:0 0 50px rgba(0,146,255,.55)}
        @media(max-width:900px){.jv-hero-rings{display:none}}

        .jv-metric-card{position:relative;border-radius:16px;padding:1.05rem 1.15rem;min-height:132px;border:1px solid var(--jarvis-line);background:linear-gradient(145deg,rgba(5,17,32,.97),rgba(7,29,52,.88));box-shadow:inset 0 0 32px rgba(0,125,255,.035)}
        .jv-metric-top{display:flex;align-items:flex-start;justify-content:space-between}
        .jv-metric-label{font-size:.78rem;color:#9fc5e1;font-weight:650}
        .jv-metric-icon{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);border:1px solid rgba(52,173,255,.35);color:#4edfff;flex:none}
        .jv-metric-value{font-size:1.9rem;font-weight:850;color:#f2fbff;margin-top:.5rem}
        .jv-metric-delta{font-size:.72rem;color:#39e79a;margin-top:.15rem;font-weight:650}
        .jv-metric-delta.flat{color:#8fb4d2}
        .jv-metric-spark{margin-top:.55rem;width:100%;height:34px;display:block}

        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .jv-panel-anchor){padding:1.1rem 1.2rem 1.2rem}
        .jv-panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:.85rem}
        .jv-panel-eyebrow{font-size:.7rem;font-weight:850;letter-spacing:.14em;color:#5fe3ff;text-transform:uppercase}
        .stApp:has(.crm-mode-marker) .jv-panel-eyebrow{color:var(--crm-blue)}

        .jv-agent-card{border:1px solid rgba(31,145,255,.28);background:linear-gradient(145deg,#06162a,#08213a);border-radius:14px;padding:1rem .8rem;text-align:center;display:flex;flex-direction:column;align-items:center;gap:.15rem}
        .jv-agent-icon{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);border:1px solid rgba(52,173,255,.4);color:#4edfff;margin-bottom:.35rem}
        .jv-agent-name{font-size:.8rem;font-weight:800;color:#eaf7ff}
        .jv-agent-state{font-size:.64rem;color:#31e29d;font-weight:700;margin-top:.15rem}
        .jv-agent-task{font-size:.68rem;color:#8fb4d2;margin:.3rem 0 .6rem}

        .jv-brief-item{display:flex;gap:.55rem;padding:.55rem 0;border-bottom:1px solid rgba(31,145,255,.14);font-size:.8rem;color:#cce9ff}
        .jv-brief-item:last-child{border-bottom:none}
        .jv-brief-dot{width:6px;height:6px;border-radius:50%;background:#4edfff;margin-top:.4rem;flex:none;box-shadow:0 0 8px rgba(78,223,255,.8)}

        .jv-approval-item{display:flex;align-items:flex-start;gap:.6rem;padding:.6rem 0;border-bottom:1px solid rgba(31,145,255,.14)}
        .jv-approval-item:last-child{border-bottom:none}
        .jv-approval-icon{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);border:1px solid rgba(52,173,255,.35);color:#4edfff;flex:none}
        .jv-approval-title{font-size:.79rem;font-weight:750;color:#eaf7ff;line-height:1.25}
        .jv-approval-sub{font-size:.66rem;color:#7fa9c8;margin-top:.15rem}

        .jv-activity-row{display:flex;align-items:flex-start;gap:.65rem;padding:.55rem 0;border-bottom:1px solid rgba(31,145,255,.10);font-size:.78rem}
        .jv-activity-row:last-child{border-bottom:none}
        .jv-activity-time{color:#7fa9c8;font-variant-numeric:tabular-nums;min-width:56px}
        .jv-activity-dot{width:6px;height:6px;border-radius:50%;background:#4edfff;margin-top:.42rem;flex:none}
        .jv-activity-agent{color:#5fe3ff;font-weight:800}
        .jv-activity-desc{color:#cce9ff}

        .jv-data-card{display:flex;align-items:center;gap:1.2rem;height:100%}
        .jv-data-icon{width:64px;height:64px;border-radius:16px;display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);border:1px solid rgba(52,173,255,.4);color:#4edfff;flex:none}
        .jv-data-live{display:inline-flex;align-items:center;gap:.4rem;font-size:.68rem;font-weight:800;color:#31e29d;margin-top:.5rem}
        .jv-data-live .jv-live-dot{width:7px;height:7px;border-radius:50%;background:#31e29d;box-shadow:0 0 8px rgba(49,226,157,.85)}
        .jv-data-ring{margin-left:auto;width:96px;height:96px;border-radius:50%;position:relative;flex:none}
        .jv-data-ring::before{content:"";position:absolute;inset:0;border-radius:50%;border:1px solid rgba(52,173,255,.3)}
        .jv-data-ring::after{content:"";position:absolute;inset:26px;border-radius:50%;background:radial-gradient(circle,#dffcff 0 8%,#23d6ff 10% 26%,#087df5 30% 60%,#03152d 64%);box-shadow:0 0 30px rgba(0,146,255,.5)}
        @media(max-width:900px){.jv-data-ring{display:none}}

        .jv-ask-bottom{display:flex;align-items:center;gap:.7rem;padding:.55rem .6rem .55rem 1.1rem;border-radius:999px;border:1px solid rgba(31,145,255,.45);background:rgba(3,14,29,.94);box-shadow:0 0 32px rgba(0,132,255,.18)}
        .jv-ask-bottom svg{color:#4edfff;flex:none}
        .jv-ask-bottom .jv-ask-text{flex:1;color:#7fa9c8;font-size:.85rem}
        .jv-ask-send{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,#0984ff,#35d9ff);color:#031424;flex:none}

        .jv-sidebar-clinic{border:1px solid rgba(52,173,255,.32);border-radius:14px;padding:.85rem .9rem;background:linear-gradient(145deg,#06162a,#08213a);margin-top:.5rem}
        .jv-sidebar-clinic-row{display:flex;align-items:center;gap:.6rem}
        .jv-sidebar-clinic-icon{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:radial-gradient(circle,#0c2138,#081a2d);border:1px solid rgba(52,173,255,.4);color:#4edfff;flex:none}
        .jv-sidebar-clinic-name{font-size:.82rem;font-weight:800;color:#eaf7ff;line-height:1.2}
        .jv-sidebar-clinic-loc{font-size:.68rem;color:#7fa9c8}
        .stApp:has(.crm-mode-marker) .jv-sidebar-clinic{border-color:var(--crm-line);background:#fff}
        .stApp:has(.crm-mode-marker) .jv-sidebar-clinic-name{color:var(--crm-ink)}
        .stApp:has(.crm-mode-marker) .jv-sidebar-clinic-loc{color:var(--crm-muted)}

        [data-testid="stSidebar"] div[role="radiogroup"] label[data-baseweb="radio"]{border-radius:10px;padding:.5rem .6rem;margin-bottom:.1rem}
        .stApp:has(.jarvis-mode-marker) [data-testid="stSidebar"] div[role="radiogroup"] label[data-baseweb="radio"]:has(input:checked){background:linear-gradient(135deg,rgba(9,132,255,.22),rgba(53,217,255,.12));border:1px solid rgba(52,173,255,.4)}
        .stApp:has(.crm-mode-marker) [data-testid="stSidebar"] div[role="radiogroup"] label[data-baseweb="radio"]:has(input:checked){background:#edf3fc;border:1px solid #bad0ef}

        /* ---------- Force each workspace to its own theme regardless of the
           viewer's Streamlit theme setting (System/Light/Dark). The rules
           above only restyle elements this app explicitly wraps in custom
           classes; plenty of native Streamlit widgets (buttons, expanders,
           dataframes, tabs, checkboxes, the chat-input's bottom bar, and
           anything rendered as a BaseWeb popover — selectbox dropdowns,
           date-picker calendars) were never covered and just inherited
           whatever base theme the viewer had picked, which is exactly what
           broke when that base theme didn't match the workspace's own
           design. Popovers/dropdowns render as portals appended straight to
           <body>, not nested inside .stApp, so they need body:has(...)
           scoping instead of .stApp:has(...) — a plain .stApp selector
           silently never matches them. ---------- */

        /* CRM: force light on every remaining native widget. This also
           matches the Core's invisible overlay button (it's a real
           st.button too), but that button is hidden via opacity:0 in its
           own more specific rule above regardless of what background/color
           this generic rule assigns it, so no conflict to worry about. */
        .stApp:has(.crm-mode-marker) [data-testid="stFormSubmitButton"]>button,
        .stApp:has(.crm-mode-marker) [data-testid^="stBaseButton"]{color:var(--crm-ink);background:#fff;border:1px solid var(--crm-line)}
        .stApp:has(.crm-mode-marker) [data-testid="stExpander"]{background:#fff!important;border:1px solid var(--crm-line)!important;border-radius:14px}
        .stApp:has(.crm-mode-marker) [data-testid="stExpander"] summary,
        .stApp:has(.crm-mode-marker) [data-testid="stExpander"] p{color:var(--crm-ink)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stDataFrame"],
        .stApp:has(.crm-mode-marker) [data-testid="stDataFrameResizable"],
        .stApp:has(.crm-mode-marker) [data-testid="stTable"]{background:#fff!important;border:1px solid var(--crm-line)!important;border-radius:10px;color-scheme:light}
        .stApp:has(.crm-mode-marker) [data-testid="stTabs"] [data-baseweb="tab-list"]{background:transparent!important;border-bottom:1px solid var(--crm-line)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stTabs"] button{color:var(--crm-muted)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stTabs"] button[aria-selected="true"]{color:var(--crm-blue)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stCheckbox"] label,
        .stApp:has(.crm-mode-marker) [data-testid="stRadio"] label,
        .stApp:has(.crm-mode-marker) [data-testid="stSlider"] label{color:var(--crm-ink)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stFileUploader"]{background:#fff!important;border:1px solid var(--crm-line)!important;color:var(--crm-ink)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stFileUploaderDropzone"]{background:var(--crm-soft)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stCode"],
        .stApp:has(.crm-mode-marker) [data-testid="stCode"] pre{background:var(--crm-soft)!important;color:var(--crm-ink)!important;border:1px solid var(--crm-line)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stBottom"]{background:var(--crm-soft)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stChatInput"]{background:#fff!important;border:1px solid var(--crm-line)!important}
        .stApp:has(.crm-mode-marker) [data-testid="stChatInputSubmitButton"]{background:var(--crm-blue)!important;color:#fff!important}
        .stApp:has(.crm-mode-marker) [data-testid="stProgress"] > div > div{background:var(--crm-line)!important}
        body:has(.crm-mode-marker) [data-baseweb="popover"],
        body:has(.crm-mode-marker) [data-baseweb="menu"],
        body:has(.crm-mode-marker) [data-baseweb="calendar"],
        body:has(.crm-mode-marker) [data-testid="stSelectboxVirtualDropdown"]{background:#fff!important;color:var(--crm-ink)!important;border:1px solid var(--crm-line)!important;color-scheme:light}
        body:has(.crm-mode-marker) [data-baseweb="popover"] *,
        body:has(.crm-mode-marker) [data-testid="stSelectboxVirtualDropdown"] *{color:var(--crm-ink)}

        /* JARVIS: force dark on every remaining native widget — see the
           matching CRM comment above re: the Core's invisible button. */
        .stApp:has(.jarvis-mode-marker) [data-testid="stFormSubmitButton"]>button,
        .stApp:has(.jarvis-mode-marker) [data-testid^="stBaseButton"]{color:#e3f8ff;background:linear-gradient(135deg,#071f3b,#0a3159);border:1px solid rgba(48,174,255,.40)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stExpander"]{background:linear-gradient(145deg,rgba(5,17,32,.97),rgba(7,29,52,.88))!important;border:1px solid var(--jarvis-line)!important;border-radius:14px}
        .stApp:has(.jarvis-mode-marker) [data-testid="stExpander"] summary,
        .stApp:has(.jarvis-mode-marker) [data-testid="stExpander"] p{color:var(--jarvis-text)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stDataFrame"],
        .stApp:has(.jarvis-mode-marker) [data-testid="stDataFrameResizable"],
        .stApp:has(.jarvis-mode-marker) [data-testid="stTable"]{background:#06172b!important;border:1px solid var(--jarvis-line)!important;border-radius:10px;color-scheme:dark}
        .stApp:has(.jarvis-mode-marker) [data-testid="stTabs"] [data-baseweb="tab-list"]{background:transparent!important;border-bottom:1px solid var(--jarvis-line)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stTabs"] button{color:var(--jarvis-muted)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stTabs"] button[aria-selected="true"]{color:var(--jarvis-cyan)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stCheckbox"] label,
        .stApp:has(.jarvis-mode-marker) [data-testid="stRadio"] label,
        .stApp:has(.jarvis-mode-marker) [data-testid="stSlider"] label{color:var(--jarvis-text)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stFileUploader"]{background:#06172b!important;border:1px solid var(--jarvis-line)!important;color:var(--jarvis-text)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stFileUploaderDropzone"]{background:#081a30!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stCode"],
        .stApp:has(.jarvis-mode-marker) [data-testid="stCode"] pre{background:#020c1a!important;color:var(--jarvis-text)!important;border:1px solid var(--jarvis-line)!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stBottom"]{background:transparent!important}
        .stApp:has(.jarvis-mode-marker) [data-testid="stProgress"] > div > div{background:var(--jarvis-line)!important}
        body:has(.jarvis-mode-marker) [data-baseweb="popover"],
        body:has(.jarvis-mode-marker) [data-baseweb="menu"],
        body:has(.jarvis-mode-marker) [data-baseweb="calendar"],
        body:has(.jarvis-mode-marker) [data-testid="stSelectboxVirtualDropdown"]{background:#06172b!important;color:var(--jarvis-text)!important;border:1px solid var(--jarvis-line)!important;color-scheme:dark}
        body:has(.jarvis-mode-marker) [data-baseweb="popover"] *,
        body:has(.jarvis-mode-marker) [data-testid="stSelectboxVirtualDropdown"] *{color:var(--jarvis-text)}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_workspace_switch() -> None:
    """The Core — see CORE_SWITCH_SPEC.md. A single toggle, not a neutral
    pair of buttons: dormant in CRM, ignited in JARVIS, since Jarvis is the
    product and the CRM is the substrate underneath him.
    """
    current = st.session_state.get("workspace_mode", "CRM")
    is_jarvis = current == "JARVIS"
    state = "on" if is_jarvis else "off"
    sub_label = "return to records" if is_jarvis else "click to bring online"
    # The visible "JARVIS" label never changes — only the ring/well/lit
    # colors and the sub-label do — but the real accessible name has to
    # describe the ACTION this click performs, since a screen reader
    # hitting a static "JARVIS" label on every render couldn't otherwise
    # tell dormant from ignited, or know which way a click would switch it.
    aria_label = "Switch to Records workspace" if is_jarvis else "Switch to Jarvis workspace"

    with st.container(key="ll_core_switch"):
        st.markdown(
            f"""
            <div class="ll-core" data-state="{state}">
              <div class="ll-core-rig">
                <div class="ll-ring"></div>
                <div class="ll-ring"></div>
                <div class="ll-seg"></div>
                <div class="ll-well"></div>
                <div class="ll-lit"></div>
              </div>
              <div class="ll-core-label">JARVIS</div>
              <div class="ll-core-sub">{sub_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(aria_label, key="ll_core_btn"):
            new_mode = "CRM" if is_jarvis else "JARVIS"
            st.session_state["workspace_mode"] = new_mode
            st.query_params["workspace"] = new_mode.lower()
            st.session_state.pop("crm_page", None)
            st.session_state.pop("jarvis_page", None)
            st.session_state.pop("jarvis_secondary_page", None)
            st.rerun()
