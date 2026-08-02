from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


def _force_streamlit_theme(desired: str) -> None:
    """Force Streamlit's OWN resolved theme (Settings > Light/Dark/System)
    to `desired` ("Light" or "Dark"), so widgets the CSS below can't reach
    — st.dataframe in particular renders its grid to a <canvas> via
    glide-data-grid, which reads Streamlit's theme directly in JavaScript
    and has no CSS hook at all, so no amount of CSS here can restyle it.

    Streamlit persists the active theme in
    localStorage["stActiveTheme-/-v2"] as a JSON string ('"Light"' /
    '"Dark"' / '"System"'), read once on load to build the theme every
    widget (including canvas ones) actually renders with. Setting it and
    reloading is the only way to make a canvas-rendered widget follow the
    workspace instead of the viewer's own theme choice — this is why the
    fix can't be pure CSS despite living in this file.

    Runs via a real <script> tag: st.markdown(unsafe_allow_html=True)
    does NOT execute <script> content (browsers never run scripts
    inserted via innerHTML, which is what that path uses under the
    hood) — components.html() renders a real same-origin iframe, whose
    scripts do execute and can reach window.parent.localStorage.

    The comparison against JSON.stringify(desired) (not the bare string)
    is what keeps this from becoming an infinite reload loop: Streamlit
    stores the value JSON-encoded, so comparing against the raw word
    would never match even right after setting it, and every rerun would
    reload the page again forever.
    """
    components.html(
        f"""
        <script>
        (function() {{
            const desired = {desired!r};
            const key = "stActiveTheme-/-v2";
            const desiredStored = JSON.stringify(desired);
            const current = window.parent.localStorage.getItem(key);
            if (current !== desiredStored) {{
                window.parent.localStorage.setItem(key, desiredStored);
                window.parent.location.reload();
            }}
        }})();
        </script>
        """,
        height=0,
        width=0,
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

        /* Approved CRM / JARVIS mode switch — built on real buttons. Scoped to
           the stVerticalBlock carrying our own .workspace-mode-anchor marker:
           newer Streamlit renders border=True containers directly on
           stVerticalBlock (inline border style) instead of the separate
           stVerticalBlockBorderWrapper div older versions used, and
           stVerticalBlock alone isn't a safe hook since every container
           (bordered or not) has that testid. :has(.workspace-mode-anchor)
           on its own isn't safe either — :has() matches ANY ancestor, so it
           also matched the unbordered stVerticalBlock one level further out
           that happens to contain this one, applying the pill/grid layout to
           the wrong (much wider) box and wrapping the button text. Requiring
           a DIRECT child stElementContainer pins this to just the innermost
           block that actually wraps the marker. */
        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor){padding:.7rem .72rem .6rem;margin:.25rem 0 .85rem;overflow:visible;position:relative}
        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"]{display:grid;grid-template-columns:1fr 1fr;gap:0;padding:3px;border-radius:999px;position:relative}
        /* stColumn ships its own flex-basis:calc(50% - 16px) for Streamlit's
           default flex column layout; that's inert once the parent above is
           grid, but grid still sizes an auto-width item to its content
           instead of stretching it to fill the track, so "JARVIS" wraps
           without an explicit width here. */
        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"] [data-testid="stColumn"]{width:100%;min-width:0}
        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"] [data-testid="stButton"]{width:100%}
        div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"] [data-testid="stButton"] button{width:100%;border:none;background:transparent;box-shadow:none;border-radius:999px;padding:.62rem .4rem;margin:0;cursor:pointer;transition:all .28s ease;font-size:.76rem;font-weight:850;letter-spacing:.04em}
        .workspace-switch-caption{text-align:center;font-size:.64rem;margin-top:.5rem;font-weight:750;letter-spacing:.08em;text-transform:uppercase}
        .workspace-switch-title{text-align:center;font-size:.62rem;font-weight:850;letter-spacing:.14em;margin-bottom:.42rem;text-transform:uppercase}

        /* CRM */
        .stApp:has(.crm-mode-marker){background:#f7f9fc;color:var(--crm-ink)}
        .stApp:has(.crm-mode-marker) [data-testid="stHeader"]{background:rgba(247,249,252,.88)}
        .stApp:has(.crm-mode-marker) [data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--crm-line)}
        .stApp:has(.crm-mode-marker) .eyebrow{color:var(--crm-blue)}
        .stApp:has(.crm-mode-marker) [data-testid="stMetric"],.stApp:has(.crm-mode-marker) .stChatMessage,.stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlockBorderWrapper"]{background:#fff;border:1px solid var(--crm-line);box-shadow:0 5px 20px rgba(16,24,40,.035)}
        .stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor){background:#fff;border:1px solid #cddcf2;box-shadow:0 10px 30px rgba(22,103,232,.08)}
        .stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"]{background:#edf3fc;border:1px solid #bad0ef}
        .stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stButton"] button{color:#5e6d82}
        .stApp:has(.crm-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"]>div:nth-child(1) [data-testid="stButton"] button{background:radial-gradient(circle at 50% 50%,#fff 0 8%,#bfe0ff 12% 30%,#1769e8 34% 62%,#0d4fc0 66%);color:#fff;box-shadow:0 4px 14px rgba(23,105,232,.30)}
        .stApp:has(.crm-mode-marker) .workspace-switch-title,.stApp:has(.crm-mode-marker) .workspace-switch-caption{color:#5e6d82}
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
        .stApp:has(.jarvis-mode-marker) [data-testid="stMetric"],.stApp:has(.jarvis-mode-marker) .stChatMessage,.stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlockBorderWrapper"]:not(:has(.workspace-mode-anchor)){background:linear-gradient(145deg,rgba(5,17,32,.97),rgba(7,29,52,.88));border:1px solid var(--jarvis-line);box-shadow:inset 0 0 32px rgba(0,125,255,.035)}
        .stApp:has(.jarvis-mode-marker) [data-testid="stMetricLabel"],.stApp:has(.jarvis-mode-marker) [data-testid="stMetricValue"],.stApp:has(.jarvis-mode-marker) [data-testid="stMetricDelta"],.stApp:has(.jarvis-mode-marker) [data-testid="stCaptionContainer"]{color:#d5edff}
        .stApp:has(.jarvis-mode-marker) input,.stApp:has(.jarvis-mode-marker) textarea,.stApp:has(.jarvis-mode-marker) [data-baseweb="select"]>div{background:#06172b!important;color:#eaf7ff!important;border-color:rgba(52,173,255,.30)!important}
        .stApp:has(.jarvis-mode-marker) .stButton>button,.stApp:has(.jarvis-mode-marker) .stDownloadButton>button{color:#e3f8ff;background:linear-gradient(135deg,#071f3b,#0a3159);border:1px solid rgba(48,174,255,.40)}
        .stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor){background:rgba(2,12,26,.98);border:1px solid rgba(30,144,255,.48);box-shadow:0 0 34px rgba(0,128,255,.18)}
        .stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"]{background:#041326;border:1px solid rgba(30,144,255,.34);box-shadow:inset 0 0 18px rgba(0,128,255,.10)}
        .stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stButton"] button{color:#7fa9c8}
        .stApp:has(.jarvis-mode-marker) div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .workspace-mode-anchor) [data-testid="stHorizontalBlock"]>div:nth-child(2) [data-testid="stButton"] button{background:radial-gradient(circle at 50% 50%,#dffcff 0 5%,#23d6ff 7% 14%,#087df5 18% 38%,#03152d 42% 55%,#0a8cff 58% 61%,#06162a 64%);color:#7fe9ff;box-shadow:0 0 12px #138cff,0 0 30px rgba(0,157,255,.62);text-shadow:0 0 12px #20cfff}
        .stApp:has(.jarvis-mode-marker) .workspace-switch-title,.stApp:has(.jarvis-mode-marker) .workspace-switch-caption{color:#65dfff;text-shadow:0 0 10px rgba(32,207,255,.55)}
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

        [data-testid="stSidebar"] div[role="radiogroup"]:not(:has(.workspace-mode-anchor)) label[data-baseweb="radio"]{border-radius:10px;padding:.5rem .6rem;margin-bottom:.1rem}
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

        /* CRM: force light on every remaining native widget. Button rules
           deliberately have no !important — the workspace-switch pill
           buttons (lines above, inside .workspace-mode-anchor) rely on
           winning this exact conflict via higher selector specificity,
           and !important here would defeat that regardless of specificity. */
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

        /* JARVIS: force dark on every remaining native widget. Button rules
           deliberately have no !important — see the matching CRM comment
           above; the workspace-switch pill buttons need to keep winning
           this conflict via specificity. */
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
    current = st.session_state.get("workspace_mode", "CRM")
    with st.container(border=True):
        st.markdown('<div class="workspace-mode-anchor"></div>', unsafe_allow_html=True)
        st.markdown('<div class="workspace-switch-title">Workspace Mode</div>', unsafe_allow_html=True)
        left, right = st.columns(2, gap="small")
        with left:
            if st.button("CRM", key="ws_btn_crm", use_container_width=True):
                if current != "CRM":
                    st.session_state["workspace_mode"] = "CRM"
                    st.query_params["workspace"] = "crm"
                    st.session_state.pop("crm_page", None)
                    st.session_state.pop("jarvis_page", None)
                    st.session_state.pop("jarvis_secondary_page", None)
                    st.rerun()
        with right:
            if st.button("JARVIS", key="ws_btn_jarvis", use_container_width=True):
                if current != "JARVIS":
                    st.session_state["workspace_mode"] = "JARVIS"
                    st.query_params["workspace"] = "jarvis"
                    st.session_state.pop("crm_page", None)
                    st.session_state.pop("jarvis_page", None)
                    st.session_state.pop("jarvis_secondary_page", None)
                    st.rerun()
        caption = "CRM online" if current == "CRM" else "Jarvis online"
        st.markdown(f'<div class="workspace-switch-caption">● &nbsp;{caption}</div>', unsafe_allow_html=True)
