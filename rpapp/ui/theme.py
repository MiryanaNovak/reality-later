"""Colors, fonts and widget defaults for the whole app.

Colors are CSS variables: --ink (text), --muted, --page, --surface (cards), --line, and the
accent: --q-primary, its --tint, and --dialogue for words spoken in chats. The accent is one
of ACCENTS, chosen in Settings › Appearance; accent_css() gives the variables for it, added
to every page by add_head(). Each accent has a light-mode color (white text on it, and it as
text on the light backgrounds, both at least 4.5:1) and a lighter dark-mode one (dark text
on it). --dialogue is the accent, darkened (lightened, in dark mode) only if it needs to be
for that 4.5:1 on both chat bubbles, the character's and your tinted one.

The fonts, Figtree and Literata, are served by the app itself, from fonts/ (see fonts.css).
"""
from pathlib import Path

from nicegui import app, ui

from .. import ICONS

FONTS = Path(__file__).parent / 'fonts'

# name -> (label, light mode, dark mode)
ACCENTS: dict[str, tuple[str, str, str]] = {
    'jade': ('Jade', '#1F7A70', '#4DB6A8'),
    'blue': ('Blue', '#2B5CB8', '#7AA7F0'),
    'violet': ('Violet', '#6A48C2', '#A893F2'),
    'rose': ('Rose', '#B03A5B', '#F08DA8'),
    'amber': ('Amber', '#8E5510', '#F0B456'),
    'slate': ('Slate', '#4A5568', '#A9B6CA'),
}
DEFAULT_ACCENT = 'jade'
PRIMARY, PRIMARY_DARK = ACCENTS[DEFAULT_ACCENT][1:]

# --page and --surface, light and dark; --tint is the accent at this opacity
LIGHT_PAGE, LIGHT_SURFACE, LIGHT_TINT = '#F3F5F7', '#FFFFFF', .10
DARK_PAGE, DARK_SURFACE, DARK_TINT = '#13161D', '#1B1F28', .16

# The browser's icons (rpapp/icons/web/): the tab's (NiceGUI also links favicon.ico, see main.py), the
# iPhone's and iPad's home screen, and Android's (manifest.json)
ICON_LINKS = (
    '<link rel="icon" href="/icons/favicon.svg" type="image/svg+xml">'
    '<link rel="icon" href="/icons/favicon-32.png" sizes="32x32" type="image/png">'
    '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">'
    '<link rel="manifest" href="/icons/manifest.json">'
)

HEAD = f'''
{ICON_LINKS}
<link rel="stylesheet" href="/fonts/fonts.css">
<style>
  :root {{
    --ink: #1D2233; --muted: #5B6376; --page: {LIGHT_PAGE}; --surface: {LIGHT_SURFACE};
    --line: #DDE2E8;
  }}
  .body--dark {{
    --ink: #E7E9EE; --muted: #A2A9B7; --page: {DARK_PAGE}; --surface: {DARK_SURFACE};
    --line: #2C313D;
  }}
  body {{
    font-family: 'Figtree', system-ui, -apple-system, 'Segoe UI', sans-serif;
    background: var(--page) !important; color: var(--ink); font-size: 15px;
  }}
  /* Red, like the jade, is lighter in dark mode to stay readable. */
  .body--dark {{ --q-negative: #F2766B !important; }}
  /* On those lighter colors, filled buttons need dark text. This has to sit in NiceGUI's "overrides"
     layer: Quasar's forced text colors live in a CSS layer, which plain rules can't beat. */
  @layer overrides {{
    .body--dark .q-btn.bg-primary {{ color: #0E1116 !important; }}
    .body--dark .q-btn.bg-negative {{ color: #1A0B09 !important; }}
  }}

  .app-header {{
    background: var(--surface); color: var(--ink); border-bottom: 1px solid var(--line);
    height: 57px;  /* the same with a second line or without: the menu on phones starts right under it */
  }}
  /* Keep the header (and its menu button) above the menu when the menu overlays the page on phones. */
  .q-header {{ z-index: 3001; }}
  .q-drawer--mobile {{ top: 57px !important; }}
  /* The top bar's page part (widgets.page_bar): back arrow, title and second line, buttons, ⋮ menu */
  .app-bar {{ display: flex; align-items: center; gap: 4px; flex: 1; min-width: 0; height: 100%; }}
  .app-bar-titles {{ flex: 1; min-width: 0; gap: 0; padding-left: 4px; }}
  .app-bar-title, .app-bar-subtitle {{
    width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .app-bar-title {{
    font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 1.25rem; line-height: 1.3;
  }}
  .app-bar-subtitle {{ font-size: .8rem; line-height: 1.25; color: var(--muted); }}
  .app-bar-actions {{ flex-wrap: nowrap; gap: 8px; margin-left: 8px; }}
  /* On phones, its buttons show their icon only (their name stays, for screen readers: aria-label) */
  @media (max-width: 599px) {{
    .app-bar-title {{ font-size: 1.1rem; }}
    .app-bar-actions {{ gap: 4px; }}
    .app-bar-actions .q-btn .q-btn__content > .q-icon {{ margin-right: 0; }}
    .app-bar-actions .q-btn {{ padding: 4px 8px; min-width: 40px; }}
    /* (Quasar's .block is display: block !important, in a CSS layer: only this layer beats it) */
    @layer overrides {{ .app-bar-actions .q-btn .q-btn__content > .block {{ display: none !important; }} }}
  }}
  .app-drawer {{ background: var(--surface) !important; border-right: 1px solid var(--line); }}
  .nav-item {{ border-radius: 10px; margin: 2px 8px; color: var(--ink); min-height: 44px; align-self: stretch; }}
  .nav-item.q-item--active {{ background: var(--tint); color: var(--q-primary); font-weight: 600; }}

  .app-content {{ width: 100%; max-width: 780px; margin: 0 auto; padding: 8px 0 72px; gap: 20px; }}
  .page-title {{
    font-family: 'Literata', Georgia, serif; font-size: 1.9rem; font-weight: 600;
    line-height: 1.15; letter-spacing: -0.01em;
  }}
  .muted {{ color: var(--muted); }}
  a.nicegui-link {{ color: var(--q-primary); text-underline-offset: 2px; }}
  .panel {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    padding: 18px; width: 100%;
  }}
  .panel-title {{ font-weight: 600; font-size: 1.05rem; }}
  .section-title {{ font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 1.35rem; }}
  .panel.fold {{ padding: 0; }}
  .panel.fold > .q-expansion-item__container > .q-item {{ padding: 14px 18px; border-radius: 14px; }}
  .panel.fold .q-expansion-item__content {{ padding: 0; }}
  .panel.fold .nicegui-expansion-content {{ padding: 4px 18px 18px; }}
  .panel.fold > .q-expansion-item__container > .q-item .q-item__label {{ font-weight: 600; font-size: 1.05rem; }}
  .panel.fold > .q-expansion-item__container > .q-item .q-item__label--caption {{
    font-weight: 400; font-size: .85rem; color: var(--muted);
  }}
  /* A hint under a field can take two lines on phones: let it push what follows down rather than overlap it. */
  .q-field--with-bottom {{ padding-bottom: 0; }}
  .q-field__bottom--animated {{ position: static; transform: none; }}
  .field-grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; width: 100%; }}
  @media (min-width: 600px) {{ .field-grid {{ grid-template-columns: 1fr 1fr; }} }}
  .item-list {{ padding: 0; overflow: hidden; }}
  .item-list .q-item {{ padding: 12px 16px; min-height: 64px; }}
  .item-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; width: 100%; }}
  .grid-card {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    cursor: pointer; transition: border-color .15s;
  }}
  .grid-card:hover, .grid-card:focus-visible {{ border-color: var(--q-primary); outline: none; }}
  .grid-card-image {{
    aspect-ratio: 1; background: var(--tint); display: flex; align-items: center; justify-content: center;
  }}
  .grid-card-initials {{
    font-family: 'Literata', Georgia, serif; font-size: 2.6rem; font-weight: 600; color: var(--q-primary);
    letter-spacing: .02em;
  }}
  .profile-identity {{ display: grid; grid-template-columns: 1fr; gap: 18px; width: 100%; }}
  @media (min-width: 600px) {{ .profile-identity {{ grid-template-columns: 180px 1fr; }} }}
  .profile-picture {{
    width: 180px; aspect-ratio: 1; border-radius: 14px; overflow: hidden; background: var(--tint);
    display: flex; align-items: center; justify-content: center;
  }}
  .profile-thumb {{
    width: 40px; height: 40px; border-radius: 10px; overflow: hidden; background: var(--tint);
    display: flex; align-items: center; justify-content: center;
    font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: .9rem; color: var(--q-primary);
  }}
  .grid-card {{ display: flex; flex-direction: column; }}
  .grid-card-body {{
    padding: 4px 12px 0; font-size: .875rem; color: var(--muted); white-space: pre-line; overflow-wrap: anywhere;
    flex-grow: 1;
  }}
  .grid-card-button {{ margin: 10px 12px 12px; }}
  .card-link {{
    color: var(--q-primary); font-size: .85rem; font-weight: 500; cursor: pointer; width: fit-content;
    border-radius: 6px;
  }}
  .card-link:hover {{ text-decoration: underline; }}
  .card-link:focus-visible {{ outline: 2px solid var(--q-primary); outline-offset: 2px; }}
  .tree-group-title {{ font-weight: 600; font-size: 1.05rem; }}
  .tree-items {{ border-left: 2px solid var(--line); margin: 0 0 0 19px; padding-left: 4px; align-self: stretch; }}
  .tree-items .q-item {{ border-radius: 10px; }}
  .panel.tree-focus {{ border-color: var(--q-primary); box-shadow: 0 0 0 1px var(--q-primary); }}
  .grid-card-title {{ font-weight: 600; width: 100%; overflow-wrap: anywhere; line-height: 1.3; }}
  .dialog-card {{ width: min(480px, calc(100vw - 32px)); max-width: none; border-radius: 14px; padding: 20px; gap: 14px; }}
  .browse-list {{ max-height: 55vh; overflow-y: auto; }}
  .offscreen {{ position: absolute; width: 1px; height: 1px; overflow: hidden; opacity: 0; pointer-events: none; }}

  /* Chat */
  .app-content:has(.chat-page), .nicegui-content:has(.chat-page) {{ padding-bottom: 0; }}
  .chat-page {{ width: 100%; gap: 16px; }}
  .chat-messages {{ display: flex; flex-direction: column; gap: 14px; width: 100%; }}
  /* Earlier messages go in above what you read without moving it: the chat page sees to it (chat_page.py,
     OLDER_JS); the browser's own way, where there's one, would add to it */
  html:has(.chat-messages), body:has(.chat-messages) {{ overflow-anchor: none; }}
  .chat-older {{ display: flex; align-items: center; justify-content: center; min-height: 40px; }}
  .chat-head .chat-actions {{ margin-left: auto; }}
  .chat-row {{ display: flex; gap: 10px; align-items: flex-start; width: 100%; }}
  .chat-row.mine {{ flex-direction: row-reverse; }}
  .chat-avatar {{ flex-shrink: 0; margin-top: 2px; }}
  .chat-bubble {{
    max-width: min(85%, 620px); min-width: 0; padding: 8px 12px 12px 14px; border-radius: 16px;
    background: var(--surface); border: 1px solid var(--line);
  }}
  .chat-bubble.editing {{ flex-grow: 1; }}
  .chat-row.theirs .chat-bubble {{ border-top-left-radius: 4px; }}
  .chat-row.mine .chat-bubble {{ background: var(--tint); border-color: transparent; border-top-right-radius: 4px; }}
  .chat-name {{ font-weight: 600; font-size: .85rem; }}
  .chat-time {{ font-size: .75rem; color: var(--muted); white-space: nowrap; }}
  /* --chat-text-size: Settings › Appearance, per device (appearance.py) */
  .chat-text {{ font-size: var(--chat-text-size, 1rem); line-height: 1.55; overflow-wrap: anywhere; }}
  .chat-input textarea {{ font-size: var(--chat-text-size, 1rem); }}
  .chat-text p {{ margin: 0 0 .6em; }}
  .chat-text p:last-child {{ margin-bottom: 0; }}
  .chat-text em {{ color: var(--muted); }}
  /* Dialogue: what's in quotation marks, the marks too (dialogue.py) */
  .chat-text .dialogue, .chat-text q {{ color: var(--dialogue, var(--q-primary)); }}
  .chat-text a {{ color: var(--q-primary); text-decoration: underline; text-underline-offset: 2px; }}
  .chat-compose {{
    position: sticky; bottom: 0; z-index: 5; width: 100%; padding: 10px 0 14px;
    background: linear-gradient(to bottom, transparent, var(--page) 14px);
  }}
  .chat-input .q-field__control {{ background: var(--surface); }}
  /* Send with nothing written: what the AI can do instead (chat_page.py) */
  .send-menu {{ max-width: min(360px, calc(100vw - 24px)); }}
  .send-menu .q-item__section--avatar {{ color: var(--q-primary); min-width: 40px; }}
  /* Your bubbles keep clear of the pictures' column on the left, where the memory line runs. */
  .chat-row.mine .chat-bubble {{ margin-left: 50px; }}
  .chat-row {{ position: relative; z-index: 1; }}
  .chat-avatar {{ position: relative; z-index: 1;  /* above the memory line, which runs behind it */
    background: linear-gradient(var(--tint), var(--tint)), var(--page); }}

  /* The dashed line: how far the memories have read. */
  .memory-marker {{
    position: relative; display: flex; align-items: center; gap: 8px; width: 100%;
    color: var(--muted); font-size: .78rem; padding: 2px 0;
  }}
  .memory-marker.hidden {{ display: none; }}
  .memory-dash {{ flex: 1; min-width: 24px; border-top: 2px dashed var(--muted); opacity: .45; }}
  .memory-marker-label {{ text-align: center; }}
  /* The line on the left joining it to the memory bubble at the bottom: one segment per element in
     between, each also covering the gap above it. 19px: the middle of the 40px pictures. */
  .memory-threaded .memory-marker::after, .memory-threaded .memory-marker ~ .chat-box::before,
  .memory-threaded .chat-reply-status::before, .memory-threaded .memory-linked::before {{
    content: ''; position: absolute; left: 19px; border-left: 2px solid var(--q-primary); opacity: .55;
  }}
  .memory-threaded .memory-marker::after {{ top: 50%; bottom: -1px; }}
  .memory-threaded .memory-marker::before {{
    content: ''; position: absolute; left: 14px; top: calc(50% - 6px); width: 12px; height: 12px;
    border-radius: 50%; background: var(--q-primary); z-index: 1;
  }}
  .memory-threaded .memory-marker ~ .chat-box {{ position: relative; }}
  .memory-threaded .memory-marker ~ .chat-box::before {{ top: -14px; bottom: 0; }}
  .chat-reply-status {{ position: relative; }}
  .memory-threaded .chat-reply-status::before {{ top: -16px; bottom: 0; }}
  .memory-threaded .memory-linked::before {{ top: -16px; height: 36px; z-index: 0; }}
  .memory-pulsing .memory-marker::after, .memory-pulsing .memory-marker ~ .chat-box::before,
  .memory-pulsing .chat-reply-status::before, .memory-pulsing .memory-linked::before,
  .memory-pulsing .memory-marker::before {{ animation: memory-pulse 1.6s ease-in-out infinite; }}
  @keyframes memory-pulse {{ 0%, 100% {{ opacity: .2; }} 50% {{ opacity: .8; }} }}
  @media (prefers-reduced-motion: reduce) {{
    .memory-pulsing .memory-marker::after, .memory-pulsing .memory-marker ~ .chat-box::before,
    .memory-pulsing .chat-reply-status::before, .memory-pulsing .memory-linked::before,
    .memory-pulsing .memory-marker::before {{ animation: none; }}
  }}
  .chat-replying {{ color: var(--muted); font-style: italic; }}
  .chat-waiting {{ padding: 10px 16px; }}
  .chat-dots span {{ animation: chat-dot 1.4s infinite both; }}
  .chat-dots span:nth-child(2) {{ animation-delay: .2s; }}
  .chat-dots span:nth-child(3) {{ animation-delay: .4s; }}
  @keyframes chat-dot {{ 0%, 80%, 100% {{ opacity: .15; }} 40% {{ opacity: 1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .chat-dots span {{ animation: none; }} }}
  .chat-status {{ padding: 2px 4px 2px 52px; }}
  .reply-facts {{
    display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; font-size: .875rem; width: 100%;
  }}
  .device-code {{
    font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-weight: 700; font-size: 1.5rem;
    letter-spacing: .06em; color: var(--q-primary);
  }}
  .device-request {{ border-color: var(--q-primary); }}
  .device-name .q-field__control {{ min-height: 28px; height: 28px; padding: 0; }}
  .device-name .q-field__native {{ padding: 0; font-weight: 500; }}
  .device-name .q-field__control:hover, .device-name.q-field--focused .q-field__control {{
    box-shadow: 0 1px 0 var(--q-primary);
  }}
  .pair-qr {{ background: #fff; border-radius: 10px; padding: 6px; line-height: 0; }}
  .pair-qr svg {{ max-width: min(70vw, 260px); height: auto; }}
  .pair-link {{ font-family: ui-monospace, Menlo, monospace; }}

  /* Framing a picture (frame_editor.js) */
  .frame-editor {{ display: flex; flex-direction: column; align-items: center; gap: 10px; }}
  .frame-viewport {{
    position: relative; width: min(320px, 70vw); aspect-ratio: 1; overflow: hidden; border-radius: 14px;
    background: var(--tint); touch-action: none; cursor: grab; user-select: none;
  }}
  .frame-viewport:active {{ cursor: grabbing; }}
  .frame-viewport:focus-visible {{ outline: 2px solid var(--q-primary); outline-offset: 3px; }}
  .frame-viewport img {{
    position: absolute; left: 0; top: 0; max-width: none; transform-origin: 0 0; pointer-events: none;
  }}
  .frame-grid {{  /* thirds, to help placing a face */
    position: absolute; inset: 0; pointer-events: none;
    background:
      linear-gradient(to right, transparent calc(33.33% - .5px), rgba(255,255,255,.45) calc(33.33% - .5px),
        rgba(255,255,255,.45) calc(33.33% + .5px), transparent calc(33.33% + .5px),
        transparent calc(66.66% - .5px), rgba(255,255,255,.45) calc(66.66% - .5px),
        rgba(255,255,255,.45) calc(66.66% + .5px), transparent calc(66.66% + .5px)),
      linear-gradient(to bottom, transparent calc(33.33% - .5px), rgba(255,255,255,.45) calc(33.33% - .5px),
        rgba(255,255,255,.45) calc(33.33% + .5px), transparent calc(33.33% + .5px),
        transparent calc(66.66% - .5px), rgba(255,255,255,.45) calc(66.66% - .5px),
        rgba(255,255,255,.45) calc(66.66% + .5px), transparent calc(66.66% + .5px));
    box-shadow: inset 0 0 0 1px rgba(0,0,0,.15);
  }}
  .frame-zoom {{ display: flex; align-items: center; gap: 8px; width: min(320px, 70vw); color: var(--muted); }}
  .frame-zoom input {{ flex: 1; accent-color: var(--q-primary); }}

  /* Settings › Appearance */
  .accent-swatch {{ width: 40px; height: 40px; box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--line); }}
  @layer overrides {{  /* (as above: to beat the button's own forced "primary" colors) */
    .q-btn.accent-swatch {{ background: var(--swatch-light) !important; color: #fff !important; }}
    .body--dark .q-btn.accent-swatch {{ background: var(--swatch-dark) !important; color: #0E1116 !important; }}
  }}
  .accent-swatch.chosen {{ box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--swatch-light); }}
  .body--dark .accent-swatch.chosen {{ box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--swatch-dark); }}

  /* Home page */
  .home-tip {{
    width: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
    background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--q-primary);
    border-radius: 12px; padding: 10px 12px 10px 14px;
  }}
  .home-tip-icon {{ color: var(--q-primary); font-size: 1.4rem; }}
  .home-tip-text {{ flex: 1 1 240px; min-width: 0; }}
  .home-tip-actions {{ display: flex; gap: 4px; margin-left: auto; }}
  .conversation-image {{ position: relative; }}
  .conversation-persona {{
    position: absolute; right: 8px; bottom: 8px; width: 36%; aspect-ratio: 1; border-radius: 50%;
    overflow: hidden; background: var(--tint); border: 3px solid var(--surface);
    box-shadow: 0 1px 4px rgba(0, 0, 0, .25); display: flex; align-items: center; justify-content: center;
    font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: .95rem; color: var(--q-primary);
  }}
  /* A conversation's title, on its card: two lines at most, then "…" */
  .conversation-title {{ display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
  /* Two rows at most: the cards past the second row of .item-grid are hidden. A row holds
     floor((width + 14) / 164) cards, from .item-grid's minmax(150px, 1fr) and 14px gap: keep in step. */
  .home-grid {{ container: home-grid / inline-size; width: 100%; }}
  @container home-grid (width < 314px) {{ .item-grid > :nth-child(n+3) {{ display: none; }} }}
  @container home-grid (314px <= width < 478px) {{ .item-grid > :nth-child(n+5) {{ display: none; }} }}
  @container home-grid (478px <= width < 642px) {{ .item-grid > :nth-child(n+7) {{ display: none; }} }}
  @container home-grid (642px <= width < 806px) {{ .item-grid > :nth-child(n+9) {{ display: none; }} }}
  @container home-grid (width >= 806px) {{ .item-grid > :nth-child(n+11) {{ display: none; }} }}

  /* "Sent to the AI" / "Only for you" tags */
  .audience {{
    font-size: .75rem; font-weight: 600; line-height: 1.6; border-radius: 999px; padding: 0 9px 0 7px;
    white-space: nowrap; flex: none;
  }}
  .audience .q-icon {{ font-size: 14px; }}
  .audience-ai {{ background: var(--tint); color: var(--q-primary); }}
  .audience-you {{ border: 1px solid var(--line); color: var(--muted); }}
  .audience-legend {{
    display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; align-items: center; justify-items: start;
  }}

  /* The "This computer" page */
  .running-dot {{
    width: 14px; height: 14px; border-radius: 50%; background: #2E9E5B; flex: none;
    animation: running-pulse 2.4s ease-out infinite;
  }}
  @keyframes running-pulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(46, 158, 91, .45); }}
    70%, 100% {{ box-shadow: 0 0 0 11px rgba(46, 158, 91, 0); }}
  }}
  @media (prefers-reduced-motion: reduce) {{ .running-dot {{ animation: none; }} }}
  .address-box {{
    background: var(--tint); border-radius: 10px; padding: 6px 6px 6px 14px; width: 100%;
  }}
  .address {{
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-weight: 600; font-size: 1.05rem;
    overflow-wrap: anywhere; color: var(--ink); text-decoration: none;
  }}
  a.address:hover {{ text-decoration: underline; }}
  .file-path {{
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: .85rem; overflow-wrap: anywhere;
  }}
  .publish-card {{ width: min(560px, calc(100vw - 24px)); max-height: 92vh; overflow-y: auto; }}
  .publish-notice {{
    background: var(--page); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; width: 100%;
  }}
  .fingerprint {{
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: .8rem; overflow-wrap: anywhere;
    background: var(--page); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; width: 100%;
  }}
  .step-title {{ font-weight: 600; margin-top: 4px; }}
  .shared-text {{ white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5; }}
  .browser-steps {{ margin: 0; padding-left: 1.2em; }}
  .browser-steps li {{ margin: 3px 0; }}
  .prompt-card {{ width: min(760px, calc(100vw - 24px)); max-height: 88vh; }}
  .prompt-parts {{ overflow-y: auto; }}
  .prompt-role {{ font-size: .75rem; font-weight: 600; color: var(--q-primary); }}
  .prompt-text {{
    white-space: pre-wrap; overflow-wrap: anywhere; font-size: .875rem; line-height: 1.5; width: 100%;
    background: var(--page); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px;
  }}
</style>
<script>
  // Copy text, also on pages served over plain http (e.g. from a phone on the home network),
  // where the browser's clipboard API isn't available.
  window.rpCopy = (text) => {{
    if (navigator.clipboard && window.isSecureContext) {{ navigator.clipboard.writeText(text); return; }}
    const area = document.createElement('textarea');
    area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.appendChild(area); area.select(); document.execCommand('copy'); area.remove();
  }};
</script>
'''

_defaults_set = False


def setup() -> None:
    """Call once at startup, before ui.run()."""
    global _defaults_set
    if _defaults_set:
        return
    _defaults_set = True
    app.colors(primary=PRIMARY, dark=DARK_SURFACE, dark_page=DARK_PAGE, negative='#C0392B', positive='#2E7D4F')
    app.add_static_files('/fonts', FONTS, max_cache_age=30 * 86400)
    app.add_static_files('/icons', ICONS / 'web', max_cache_age=86400)
    ui.button.default_props('no-caps unelevated')
    for element in (ui.input, ui.number, ui.select, ui.input_chips):
        element.default_props('outlined')


def add_head(accent: str = DEFAULT_ACCENT) -> None:
    """Call at the start of every page."""
    ui.add_head_html(HEAD)
    ui.add_head_html(f'<style id="rp-accent">{accent_css(accent)}</style>')


def accent_css(accent: str) -> str:
    """The CSS variables of an accent (the default one, if it's unknown)."""
    _, light, dark = ACCENTS.get(accent, ACCENTS[DEFAULT_ACCENT])
    light_dialogue = readable(light, LIGHT_SURFACE, tinted(light, LIGHT_TINT, LIGHT_PAGE))
    dark_dialogue = readable(dark, DARK_SURFACE, tinted(dark, DARK_TINT, DARK_PAGE))
    # !important: NiceGUI sets --q-primary on the body itself, from app.colors()
    return (f'body {{ --q-primary: {light} !important; --tint: {_rgba(light, LIGHT_TINT)}; '
            f'--dialogue: {light_dialogue}; }} '
            f'body.body--dark {{ --q-primary: {dark} !important; --tint: {_rgba(dark, DARK_TINT)}; '
            f'--dialogue: {dark_dialogue}; }}')


def readable(color: str, *backgrounds: str, ratio: float = 4.5) -> str:
    """The color, as text that reads on all the backgrounds: darkened (lightened, on dark ones)
    step by step until its contrast with each is at least `ratio` (WCAG's 4.5:1 for text)."""
    target = (0, 0, 0) if _luminance(_rgb(backgrounds[0])) > .18 else (255, 255, 255)
    start = _rgb(color)
    for step in range(101):
        shade = tuple(round(c + (t - c) * step / 100) for c, t in zip(start, target))
        if all(contrast(shade, _rgb(b)) >= ratio for b in backgrounds):
            return '#{:02X}{:02X}{:02X}'.format(*shade)
    return color


def tinted(color: str, alpha: float, under: str) -> str:
    """What the color at that opacity looks like over `under`."""
    mixed = (round(c * alpha + u * (1 - alpha)) for c, u in zip(_rgb(color), _rgb(under)))
    return '#{:02X}{:02X}{:02X}'.format(*mixed)


def contrast(a: tuple[int, ...] | str, b: tuple[int, ...] | str) -> float:
    """WCAG's contrast ratio between two colors: from 1 (the same) to 21 (black on white)."""
    la, lb = sorted((_luminance(_rgb(a) if isinstance(a, str) else a),
                     _luminance(_rgb(b) if isinstance(b, str) else b)), reverse=True)
    return (la + .05) / (lb + .05)


def _luminance(rgb: tuple[int, ...]) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return .2126 * r + .7152 * g + .0722 * b


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _rgba(color: str, alpha: float) -> str:
    r, g, b = _rgb(color)
    return f'rgba({r}, {g}, {b}, {alpha})'
