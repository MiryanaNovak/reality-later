"""Settings › Devices: pair devices, allow the ones asking, see and remove paired ones.

On the computer the app runs on:
- "Pair a device" shows a QR code (and its link) for another device to open: it's
  signed in at once. The link works once, for a few minutes.
- Devices asking to join (they show a code like 7KQ-2MX) are listed with Allow and Deny.
  Any page of the app also pops up a notice when one asks.
On a paired device: which one it is, and Sign out.
Everywhere: the paired devices, to rename or remove (which signs them out at once).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from nicegui import ui

from ..access import Access, Who
from ..devices import Device, JoinRequest
from .widgets import confirm, folding_panel, time_ago


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
            ui.label('To pair another device, use the computer the app runs on (or python main.py pair there).') \
                .classes('text-sm muted')
        devices()
        ui.timer(2, lambda: refresh() if access.devices.version != version['seen'] else None)

    def pairing_tools(self) -> None:
        """For the "This computer" page: the devices asking to join, and the Pair a device button."""
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

        requests()
        ui.button('Pair a device', icon='qr_code_2', on_click=lambda: self._pair_dialog(access, refresh)) \
            .props('unelevated').mark('pair-device')
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
        addresses = access.addresses() or ['localhost']
        link = access.devices.new_link()
        state = {'address': addresses[0], 'done': False}

        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes('dialog-card').mark('pair-dialog'):
            with ui.row().classes('w-full items-center no-wrap'):
                ui.label('Pair a device').classes('panel-title')
                ui.space()
                ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label="Close"')

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

            def choose(value: str | None) -> None:
                if value:
                    state['address'] = value.strip()
                    code.refresh()

            ui.select(addresses, value=addresses[0], label='The device reaches this computer at',
                      new_value_mode='add-unique', on_change=lambda e: choose(e.value)) \
                .props('use-input hide-dropdown-icon=false input-debounce=0 dense').classes('w-full') \
                .mark('pair-address')
            ui.label('Its address on your network, usually the first one. Type another (say, a Tailscale name) '
                     'if needed.').classes('text-xs muted -mt-2')
            code()

        def watch() -> None:
            if link.used_by and not state['done']:
                state['done'] = True
                code.refresh()
                refresh()

        timer = ui.timer(1, watch)
        dialog.on_value_change(lambda e: (timer.cancel(), dialog.delete()) if not e.value else None)
        dialog.open()


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
