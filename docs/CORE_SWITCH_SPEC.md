# Workspace switch — "The Core" implementation spec

Replaces the current CRM/JARVIS pill switch in the sidebar with a power-core
control: dormant when in CRM mode, ignited when in JARVIS mode.

## Concept

Not a neutral 50/50 toggle. The core is **Jarvis's presence**. Dormant, the
outer ring turns slowly and the centre is cold. Clicking brings him online:
the ring speeds up, the centre ignites, everything lights blue. Clicking
again returns to Records (CRM).

This deliberately makes the two modes unequal — Jarvis is the product, the
CRM is the substrate. The control should say that.

## Reference implementation (verified working CSS)

The animation and states below are already tested in a browser. Use these
exact values rather than re-deriving them.

```html
<div class="ll-core" data-state="off">
  <div class="ll-core-rig">
    <div class="ll-ring"></div>
    <div class="ll-ring"></div>
    <div class="ll-seg"></div>
    <div class="ll-well"></div>
    <div class="ll-lit"></div>
  </div>
  <div class="ll-core-label">JARVIS</div>
  <div class="ll-core-sub">click to bring online</div>
</div>
```

When active, `data-state="on"`, and the sub-label text becomes
`return to records`.

```css
.ll-core{
  display:flex; flex-direction:column; align-items:center; gap:15px;
  cursor:pointer; user-select:none; padding:6px 0;
}
.ll-core-rig{position:relative; width:74px; height:74px; display:grid; place-items:center}
.ll-ring{position:absolute; inset:0; border-radius:50%; border:1px solid #23324F;
  transition:.5s}
.ll-ring:nth-child(2){inset:9px; border-color:#1D2A44}
.ll-seg{position:absolute; inset:15px; border-radius:50%; border:2px dashed #2A3B5C;
  animation:ll-spin 15s linear infinite; transition:border-color .5s}
@keyframes ll-spin{to{transform:rotate(360deg)}}
.ll-well{position:absolute; inset:24px; border-radius:50%; background:#1A2740;
  box-shadow:inset 0 0 8px #0A0F1C; transition:.5s}
.ll-lit{position:absolute; inset:31px; border-radius:50%; background:#2C3E60; transition:.5s}
.ll-core-label{font-size:10.5px; font-weight:750; letter-spacing:.16em;
  color:#4E5D75; transition:.4s}
.ll-core-sub{font-size:10px; color:#3C4A63; margin-top:-9px}

/* ignited */
.ll-core[data-state="on"] .ll-ring{
  border-color:#3D6FD0; box-shadow:0 0 18px rgba(61,111,208,.32)}
.ll-core[data-state="on"] .ll-ring:nth-child(2){border-color:#5B87E0}
.ll-core[data-state="on"] .ll-seg{border-color:#6E9BFF; animation-duration:5s}
.ll-core[data-state="on"] .ll-well{
  background:#17325E; box-shadow:inset 0 0 12px #0A1830, 0 0 22px rgba(110,155,255,.4)}
.ll-core[data-state="on"] .ll-lit{
  background:#9CC0FF; box-shadow:0 0 20px #6E9BFF, 0 0 44px rgba(110,155,255,.62)}
.ll-core[data-state="on"] .ll-core-label{color:#B9D0FF; letter-spacing:.2em}
```

## Streamlit-specific constraints — read before implementing

This project has already lost time to three separate Streamlit-version CSS
traps. Avoid repeating them:

1. **It must be a real `st.button` underneath**, styled via stable
   `data-testid` selectors — not a CSS-only fake control, and not anything
   relying on `data-baseweb` internals (those broke the original pill
   switch when Streamlit changed its DOM). Raw HTML in `st.markdown` cannot
   trigger a Streamlit rerun on its own.
2. **Do not rely on `stVerticalBlockBorderWrapper`.** The installed
   Streamlit version renders `border=True` containers directly on
   `stVerticalBlock`, which silently killed the old pill's active-state
   glow. Anchor to `stElementContainer` and verify in the browser that the
   selector actually matches.
3. **The theme switch does a hard page reload**, which starts a fresh
   session and wipes `st.session_state`. Workspace mode is currently
   persisted in the URL query string (`?workspace=jarvis`) and recovered in
   `dashboard.py` from `st.query_params`. The core must work with that
   existing flow, not around it.
4. **CSS animations restart on every Streamlit rerun.** The slow ring
   rotation will visibly jump back to 0deg each time the script re-runs.
   Check how noticeable this is in practice; if it's distracting, consider
   whether the rotation is worth keeping or should be slowed further.

## Accessibility / quality floor

- The underlying button needs a real accessible label (e.g. "Switch to
  Jarvis workspace" / "Switch to Records workspace"), since the visible
  text alone won't convey the action to a screen reader.
- Visible keyboard focus state on the core.
- Respect `prefers-reduced-motion`: disable the ring rotation and the
  breathing/ignite transitions for users who've asked for reduced motion.

## Scope

Only the sidebar switch changes. Do not restyle other Jarvis screens in the
same pass — if the core makes the surrounding UI look plain by comparison,
that's a separate design conversation, not something to fix implicitly here.

Test both directions (CRM → Jarvis → CRM) with full page reloads in
between, and confirm the sidebar still looks right in both light and dark
workspace themes.
