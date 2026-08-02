"""Small inline SVG icon set shared across the Jarvis UI.

Kept dependency-free (no icon font / package) so it works anywhere
Streamlit's markdown+unsafe_allow_html renders. Every icon inherits its
color from `currentColor`, so wrapping HTML can recolor them with CSS.
"""
from __future__ import annotations


def _svg(paths: str, view_box: str = "0 0 24 24") -> str:
    return (
        f'<svg viewBox="{view_box}" fill="none" xmlns="http://www.w3.org/2000/svg" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'{paths}</svg>'
    )


ICONS: dict[str, str] = {
    "heart-pulse": _svg(
        '<path d="M3.5 12.5h3.6l1.7-3.4 2.4 6.8 1.9-4.8h1.3l1.6 2h4.5"/>'
        '<path d="M12 20.2s-7.4-4.36-9.1-8.9C1.7 7.9 3.9 5 6.9 5c1.8 0 3.2 1 4.1 2.2C11.9 6 13.3 5 15.1 5c3 0 5.2 2.9 4 6.3-1.7 4.54-9.1 8.9-9.1 8.9Z"/>'
    ),
    "rupee": _svg(
        '<path d="M6 4.5h12"/><path d="M6 9h12"/>'
        '<path d="M6 4.5c4.2 0 7.2 1.6 7.2 4.5s-3 4.5-7.2 4.5"/>'
        '<path d="M6 13.5h5.2L17 20"/>'
    ),
    "list-check": _svg(
        '<path d="M4.5 6.5 6 8l2.5-2.7"/><path d="M4.5 13.5 6 15l2.5-2.7"/>'
        '<path d="M12.5 6.5h7"/><path d="M12.5 14h7"/><path d="M5.5 20h13"/>'
    ),
    "shield-check": _svg(
        '<path d="M12 3.3 5 5.9v5.4c0 4.4 2.9 7.5 7 8.4 4.1-.9 7-4 7-8.4V5.9L12 3.3Z"/>'
        '<path d="M9.2 12.1l1.9 1.9 3.7-3.9"/>'
    ),
    "megaphone": _svg(
        '<path d="M3.5 10.2v3.6c0 .8.6 1.4 1.4 1.4h1.3l1 4.5"/>'
        '<path d="M6.2 9.4 15 6c1.5-.6 3.1.6 3.1 2.2v6.6c0 1.6-1.6 2.8-3.1 2.2L6.2 13.7"/>'
        '<path d="M6.2 9.4v4.3"/>'
    ),
    "gear": _svg(
        '<path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z"/>'
        '<path d="M19.4 13.6a7.6 7.6 0 0 0 0-3.2l1.9-1.3-1.5-2.6-2.2.8a7.6 7.6 0 0 0-2.8-1.6L14.4 3h-3l-.4 2.7a7.6 7.6 0 0 0-2.8 1.6l-2.2-.8-1.5 2.6 1.9 1.3a7.6 7.6 0 0 0 0 3.2l-1.9 1.3 1.5 2.6 2.2-.8c.8.7 1.8 1.3 2.8 1.6l.4 2.7h3l.4-2.7c1-.3 2-.9 2.8-1.6l2.2.8 1.5-2.6-1.9-1.3Z"/>'
    ),
    "users": _svg(
        '<path d="M8.5 11.3a3.15 3.15 0 1 0 0-6.3 3.15 3.15 0 0 0 0 6.3Z"/>'
        '<path d="M2.7 19c.5-3 2.9-5 5.8-5s5.3 2 5.8 5"/>'
        '<path d="M15.6 5.4a3.1 3.1 0 0 1 0 5.9"/>'
        '<path d="M16.4 14.2c2.4.4 4.2 2.2 4.6 4.7"/>'
    ),
    "bar-chart": _svg(
        '<path d="M4.5 19.5v-6.2"/><path d="M9.8 19.5V7.4"/>'
        '<path d="M15.1 19.5v-9.6"/><path d="M20.4 19.5V4.5"/>'
    ),
    "search": _svg(
        '<path d="M11 18.2a7.2 7.2 0 1 0 0-14.4 7.2 7.2 0 0 0 0 14.4Z"/><path d="M20.2 20.2l-4.3-4.3"/>'
    ),
    "bell": _svg(
        '<path d="M6 9.8a6 6 0 0 1 12 0c0 4.1 1.2 5.6 1.9 6.4H4.1c.7-.8 1.9-2.3 1.9-6.4Z"/>'
        '<path d="M10.3 19.6a1.9 1.9 0 0 0 3.4 0"/>'
    ),
    "mic": _svg(
        '<path d="M12 14.2a2.9 2.9 0 0 0 2.9-2.9V6.7a2.9 2.9 0 1 0-5.8 0v4.6a2.9 2.9 0 0 0 2.9 2.9Z"/>'
        '<path d="M6.4 11.3a5.6 5.6 0 0 0 11.2 0"/><path d="M12 16.9v2.9"/><path d="M9.3 19.8h5.4"/>'
    ),
    "arrow-up": _svg('<path d="M12 19V6"/><path d="M6.2 11.8 12 6l5.8 5.8"/>'),
    "database": _svg(
        '<path d="M4.5 6.2c0-1.5 3.4-2.7 7.5-2.7s7.5 1.2 7.5 2.7-3.4 2.7-7.5 2.7-7.5-1.2-7.5-2.7Z"/>'
        '<path d="M4.5 6.2V17.8c0 1.5 3.4 2.7 7.5 2.7s7.5-1.2 7.5-2.7V6.2"/>'
        '<path d="M4.5 12c0 1.5 3.4 2.7 7.5 2.7s7.5-1.2 7.5-2.7"/>'
    ),
    "home": _svg('<path d="M4 11.4 12 4l8 7.4"/><path d="M6 10v9h12v-9"/><path d="M10 19v-5h4v5"/>'),
    "user": _svg(
        '<path d="M12 12.4a4.1 4.1 0 1 0 0-8.2 4.1 4.1 0 0 0 0 8.2Z"/>'
        '<path d="M4.6 20c.9-3.8 3.8-6.1 7.4-6.1s6.5 2.3 7.4 6.1"/>'
    ),
    "workflow": _svg(
        '<circle cx="5" cy="6" r="2.2"/><circle cx="19" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/>'
        '<path d="M6.9 7.3 11 16"/><path d="M17.1 7.3 13 16"/>'
    ),
    "plug": _svg(
        '<path d="M9 3.5v5"/><path d="M15 3.5v5"/>'
        '<path d="M6.2 8.5h11.6v3a5.8 5.8 0 0 1-11.6 0v-3Z"/><path d="M12 17.3v3.2"/>'
    ),
    "clipboard": _svg(
        '<path d="M7.5 5.2h9a1 1 0 0 1 1 1V19a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V6.2a1 1 0 0 1 1-1Z"/>'
        '<path d="M9.5 4h5a1 1 0 0 1 1 1v1h-7V5a1 1 0 0 1 1-1Z"/><path d="M9 10.5h6"/><path d="M9 14h6"/>'
    ),
    "folder": _svg(
        '<path d="M3.8 6.6c0-.9.7-1.6 1.6-1.6h3.4l1.6 1.9h8.2c.9 0 1.6.7 1.6 1.6v8.9c0 .9-.7 1.6-1.6 1.6H5.4c-.9 0-1.6-.7-1.6-1.6V6.6Z"/>'
    ),
    "chevron-down": _svg('<path d="M6 9.5 12 15l6-5.5"/>'),
    "switch": _svg(
        '<path d="M4 8h13.5"/><path d="M14 4.5 17.5 8 14 11.5"/>'
        '<path d="M20 16H6.5"/><path d="M10 12.5 6.5 16 10 19.5"/>'
    ),
    "clinic": _svg(
        '<path d="M12 3.3 5 5.9v5.4c0 4.4 2.9 7.5 7 8.4 4.1-.9 7-4 7-8.4V5.9L12 3.3Z"/>'
        '<path d="M12 8.4v5.2"/><path d="M9.4 11h5.2"/>'
    ),
    "sparkle": _svg(
        '<path d="M12 3.5c.5 3 2 5.4 4.9 5.9-2.9.5-4.4 2.9-4.9 5.9-.5-3-2-5.4-4.9-5.9 2.9-.5 4.4-2.9 4.9-5.9Z"/>'
        '<path d="M18.5 15c.3 1.6 1.1 2.9 2.6 3.2-1.5.3-2.3 1.6-2.6 3.2-.3-1.6-1.1-2.9-2.6-3.2 1.5-.3 2.3-1.6 2.6-3.2Z"/>'
    ),
}


def icon(name: str, size: int = 18) -> str:
    """Return an inline SVG icon markup, sized to `size` px."""
    svg = ICONS.get(name, ICONS["sparkle"])
    return svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
