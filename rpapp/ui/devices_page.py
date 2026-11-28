"""Settings › Devices: pair devices, allow the ones asking, see and remove paired ones.

On the computer the app runs on:
- "Pair a device" asks first which address the device reaches this computer at (each one saying what
  it is: Wi-Fi, Ethernet, a VPN...; Wi-Fi first), then shows a QR code (and its link) for it to open:
  it's signed in at once. The link works once, for a few minutes.
- Devices asking to join (they show a code like 7KQ-2MX) are listed with Allow and Deny.
  Any page of the app also pops up a notice when one asks.
On a paired device: which one it is, and Sign out.
Everywhere: the paired devices, to rename or remove (which signs them out at once).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from collections.abc import Callable, Sequence
from typing import Any

from nicegui import ui

from ..access import Access, NetworkAddress, Who
from ..devices import Device, JoinRequest
from ..paths import command
from .widgets import Dialog, confirm, folding_panel, time_ago


def _who(access: Access) -> Who:
    return access.who_request(ui.context.client.request)


class DevicesSection:
    def __init__(self, access: Access | None) -> None:
        self.access = access

    def section(self) -> None:
        with folding_panel('Devices', 'Which browsers may use the app'):
            self._body()

    def _body(self) -> None:
        access = self.access
        if access is None or not access.enabled:
            ui.label('Pairing is off (pairing = false in config.toml): any browser that allowed_networks lets '
                     'in can use the app.').classes('text-sm muted').mark('pairing-off')
            return
        who = _who(access)
        if who.local:
            ui.label('You\'re on the computer the app runs on: no pairing needed here.') \
                .classes('text-sm muted').mark('devices-local')
        elif who.device:
            with ui.row().classes('w-full items-center no-wrap gap-2').mark('devices-this'):
                ui.label(f'This browser is paired as "{who.device.name}".').classes('text-sm grow')
                ui.button('Sign out', icon='logout', on_click=lambda: ui.navigate.to('/devices/sign-out')) \
                    .props('flat dense no-caps').mark('sign-out')

        version = {'seen': access.devices.version}

        @ui.refreshable
        def requests() -> None:
            self._requests(access, refresh)

        @ui.refreshable
        def devices() -> None:
            self._devices(access, who, refresh)

        def refresh() -> None:
            version['seen'] = access.devices.version
            requests.refresh()
            devices.refresh()

        if who.local:
            requests()
            ui.button('Pair a device', icon='qr_code_2', on_click=lambda: self._pair_dialog(access, refresh)) \
                .props('unelevated').mark('pair-device')
        else:
            ui.label(f'To pair another device, use the computer the app runs on (or {command()} pair there).') \
                .classes('text-sm muted')
        devices()
        ui.timer(2, lambda: refresh() if access.devices.version != version['seen'] else None)

    def pair_button(self) -> None:
        """For the "This computer" page: the Pair a device button (the QR code)."""
        access = self.access
        if access is None or not access.enabled:
            return
        ui.button('Pair a device', icon='qr_code_2', on_click=lambda: self._pair_dialog(access, lambda: None)) \
            .props('unelevated').mark('pair-device')

    def join_requests(self) -> None:
        """For the "This computer" page: the devices asking to join, as they come."""
        access = self.access
        if access is None or not access.enabled:
            return
        version = {'seen': access.devices.version}

        @ui.refreshable
        def requests() -> None:
            self._requests(access, refresh)

        def refresh() -> None:
            version['seen'] = access.devices.version
            requests.refresh()

        with ui.column().classes('w-full gap-2').mark('join-requests'):
            requests()
        ui.timer(2, lambda: refresh() if access.devices.version != version['seen'] else None)

    # -- waiting requests ------------------------------------------------------------------

    def _requests(self, access: Access, refresh) -> None:
        waiting = access.devices.pending()
        if not waiting:
            return
        ui.label('Asking to join').classes('panel-title mt-1')
        for r in waiting:
            self._request_card(access, r, refresh)

    def _request_card(self, access: Access, r: JoinRequest, refresh) -> None:
        def answer(allow: bool) -> None:
            answered = access.devices.answer(r.code, allow=allow)
            if answered:
                ui.notify(f'{"Allowed" if allow else "Refused"}: {answered.name}.')
            refresh()

        with ui.column().classes('panel gap-2 device-request').mark(f'request-{r.code}'):
            with ui.row().classes('w-full items-center no-wrap gap-3'):
                ui.label(r.code).classes('device-code')
                with ui.column().classes('gap-0 grow min-w-0'):
                    ui.label(r.name).classes('text-weight-medium')
                    ui.label(f'From {r.address}, {time_ago(r.created)}').classes('text-sm muted')
            ui.label('Allow it only if this is the code shown on your device.').classes('text-sm muted')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Deny', on_click=lambda: answer(False)).props('flat no-caps').mark('request-deny')
                ui.button('Allow', icon='check', on_click=lambda: answer(True)).props('unelevated no-caps') \
                    .mark('request-allow')

    # -- paired devices --------------------------------------------------------------------

    def _devices(self, access: Access, who: Who, refresh) -> None:
        paired = sorted(access.devices.devices, key=lambda d: d.last_seen, reverse=True)
        ui.label('Paired devices').classes('panel-title mt-2')
        if not paired:
            ui.label('None yet.').classes('text-sm muted').mark('no-devices')
            return
        for device in paired:
            self._device_row(access, device, who.device is not None and who.device.id == device.id, refresh)

    def _device_row(self, access: Access, device: Device, this: bool, refresh) -> None:
        async def remove() -> None:
            if await confirm(f'Remove "{device.name}"?', 'It\'s signed out at once, and must be paired again to '
                             'use the app.', 'Remove'):
                removed = access.devices.remove(device.id)
                if removed:
                    access.kick(removed)
                refresh()

        def rename(e) -> None:
            access.devices.rename(device.id, e.sender.value or '')

        with ui.row().classes('w-full items-center no-wrap gap-2 device-row').mark(f'device-{device.id}'):
            ui.icon(_icon(device), size='sm').classes('muted')
            with ui.column().classes('gap-0 grow min-w-0'):
                ui.input(value=device.name).props(remove='outlined').props('dense borderless') \
                    .classes('w-full device-name').on('blur', rename).mark('device-name') \
                    .tooltip('Click to rename')
                seen = 'now' if this else time_ago(device.last_seen)
                ui.label(f'Paired {_date(device.paired_at)}, seen {seen}'
                         + (f' from {device.address}' if device.address else '')).classes('text-xs muted')
            if this:
                ui.badge('This device').props('outline color=primary')
            ui.button(icon='delete_outline', on_click=remove) \
                .props('flat round dense aria-label="Remove this device"').mark('device-remove')

    # -- pairing with a QR code ------------------------------------------------------------

    def _pair_dialog(self, access: Access, refresh) -> None:
        """First the address the device reaches this computer at, chosen in a list saying what each one is
        (Wi-Fi, Ethernet, a VPN...); then the QR code for it. Nothing is chosen ahead: the address used last time
        is only marked, since a wrong one taken for granted is the easiest mistake to make."""
        choices = access.choices() or [NetworkAddress('localhost', 'name', 'This computer only')]
        last = access.devices.pair_address
        link = access.devices.new_link()
        state: dict[str, Any] = {'address': '', 'done': False}

        with ui.context.client.layout, Dialog() as dialog, ui.card().classes('dialog-card').mark('pair-dialog'):
            with ui.row().classes('w-full items-center no-wrap'):
                ui.label('Pair a device').classes('panel-title')
                ui.space()
                ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')

            def choose(address: str) -> None:
                address = address.strip()
                if not address:
                    return
                state['address'] = address
                access.devices.chose_address(address)
                body.refresh()

            def change() -> None:
                state['address'] = ''
                body.refresh()

            @ui.refreshable
            def body() -> None:
                if not state['address']:
                    _address_list(choices, last, choose)
                    return
                chosen = next((c for c in choices if c.address == state['address']), None)
                with ui.row().classes('w-full items-center no-wrap gap-3 pair-chosen').mark('pair-chosen'):
                    ui.icon(ICONS[chosen.kind] if chosen else 'edit').classes('text-primary')
                    with ui.column().classes('gap-0 min-w-0 grow'):
                        ui.label(chosen.title if chosen else 'Typed by you').classes('text-sm text-weight-medium')
                        ui.label(state['address']).classes('text-sm pair-link break-all')
                    ui.button('Change', on_click=change).props('flat dense no-caps').mark('pair-change')
                code()

            @ui.refreshable
            def code() -> None:
                url = access.link_url(link.code, state['address'])
                if link.used_by:
                    with ui.column().classes('w-full items-center gap-2 py-6').mark('pair-done'):
                        ui.icon('check_circle', size='xl').classes('text-positive')
                        ui.label(f'Paired: {link.used_by}').classes('panel-title')
                    return
                if link.expires <= datetime.now(timezone.utc):
                    with ui.column().classes('w-full items-center gap-2 py-4').mark('pair-expired'):
                        ui.label('This link expired.').classes('muted')
                        ui.button('Make a new one', on_click=renew).props('unelevated no-caps')
                    return
                ui.label('Scan this code with the device\'s camera, or open the link on it. It works once, until '
                         f'{link.expires.astimezone():%H:%M}.').classes('text-sm muted')
                ui.html(_qr_svg(url), sanitize=False).classes('pair-qr self-center').mark('pair-qr')
                with ui.row().classes('w-full items-center no-wrap gap-1'):
                    ui.label(url).classes('text-xs break-all grow pair-link').mark('pair-link')
                    ui.button(icon='content_copy', on_click=lambda: ui.notify('Copied')) \
                        .props('flat round dense aria-label="Copy the link"') \
                        .on('click', js_handler=f'() => window.rpCopy({json.dumps(url)})')

            def renew() -> None:
                nonlocal link
                link = access.devices.new_link()
                code.refresh()

            body()

        def watch() -> None:
            if link.used_by and not state['done']:
                state['done'] = True
                if state['address']:
                    code.refresh()
                else:
                    body.refresh()
                refresh()

        timer = ui.timer(1, watch)
        dialog.on_value_change(lambda e: (timer.cancel(), dialog.delete()) if not e.value else None)
        dialog.open()


ICONS = {'wifi': 'wifi', 'ethernet': 'settings_ethernet', 'network': 'lan', 'vpn': 'vpn_lock', 'internet': 'public',
         'name': 'computer', 'virtual': 'layers'}


def _address_list(choices: list[NetworkAddress], last: str, choose) -> None:
    """The addresses to choose from (address_rows), and a field for another one (a Tailscale name...)."""
    ui.label('Which network is the device on? Choose the address it reaches this computer at: the QR code comes '
             'next.').classes('text-sm').mark('pair-question')
    address_rows(choices, lambda c: address_row(
        c, shown=c.address, last=c.address == last, on_click=lambda c=c: choose(c.address),
        markers=['pair-choice', f'pair-{c.kind}', f'pair-address-{c.address}']), marker='pair-other')
    with ui.row().classes('w-full items-center no-wrap gap-2'):
        typed = ui.input('Another address', placeholder='e.g. desk.tailnet.ts.net').props('dense outlined') \
            .classes('grow').mark('pair-typed')
        ui.button('Use', on_click=lambda: choose(typed.value or '')).props('flat no-caps').mark('pair-use-typed')
        typed.on('keydown.enter', lambda: choose(typed.value or ''))


def address_rows(choices: Sequence[NetworkAddress], row: Callable[[NetworkAddress], None], *, marker: str) -> None:
    """This computer's addresses, likeliest first, each drawn by `row`; those of containers and virtual machines
    folded away under "Other addresses" (`marker`), open if there are no others."""
    usual = [c for c in choices if c.kind != 'virtual']
    other = [c for c in choices if c.kind == 'virtual']
    with ui.list().props('separator').classes('w-full address-choices'):
        for choice in usual:
            row(choice)
    if other:
        with ui.expansion(f'Other addresses ({len(other)})', value=not usual).classes('w-full address-other') \
                .props('dense').mark(marker):
            ui.label('Containers\' and virtual machines\': only they can reach these.').classes('text-xs muted')
            with ui.list().props('separator').classes('w-full'):
                for choice in other:
                    row(choice)


def address_row(choice: NetworkAddress, *, shown: str, markers: Sequence[str], last: bool = False,
                on_click: Callable[[], Any] | None = None, copy: str = '') -> None:
    """One address: what it is (Wi-Fi · wlp2s0), `shown` (the address, or the link with it), who reaches it; marked
    if it was `last` chosen; chosen by a click (`on_click`), or copied (`copy`: its button)."""
    with ui.item(on_click=on_click).classes('address-choice px-2').props('clickable' if on_click else '') \
            .mark(*markers):
        with ui.item_section().props('avatar'):
            ui.icon(ICONS[choice.kind]).classes('text-primary' if choice.kind != 'virtual' else 'muted')
        with ui.item_section().classes('min-w-0'):
            with ui.row().classes('items-center gap-2'):
                ui.label(choice.title).classes('text-weight-medium')
                if last:
                    ui.badge('Used last time').props('outline color=primary').mark('pair-last')
            ui.label(shown).classes('pair-link text-sm break-all')
            ui.label(choice.note).classes('text-xs muted')
        if copy:
            with ui.item_section().props('side'):
                ui.button(icon='content_copy', on_click=lambda: ui.notify('Copied')) \
                    .props('flat round dense aria-label="Copy the address"').mark('address-copy') \
                    .on('click', js_handler=f'() => window.rpCopy({json.dumps(copy)})')


def watch_requests(access: Access | None) -> None:
    """On the computer itself: a notice on any page when a device asks to join."""
    if access is None or not access.enabled or not _who(access).local:
        return
    seen = {r.id for r in access.devices.pending()}

    def check() -> None:
        for r in access.devices.pending():
            if r.id not in seen:
                seen.add(r.id)
                ui.notify(f'{r.name} ({r.address}) asks to use the app, with the code {r.code}. '
                          'Allow it on the This computer page, or in Settings › Devices.',
                          type='info', timeout=15_000, close_button=True)

    ui.timer(2, check)


def _qr_svg(url: str) -> str:
    import segno
    return segno.make(url, error='m').svg_inline(scale=5, dark='#111', light='#fff', border=3)


def _icon(device: Device) -> str:
    ua = device.user_agent
    if any(k in ua for k in ('Android', 'iPhone', 'Mobile')):
        return 'smartphone'
    if 'iPad' in ua or 'Tablet' in ua:
        return 'tablet'
    return 'computer'


def _date(when: datetime) -> str:
    local = when.astimezone()
    return f'{local.day} {local:%b %Y}'
