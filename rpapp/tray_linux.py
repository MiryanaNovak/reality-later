"""The tray icon on Linux: a StatusNotifierItem, with its menu as a DBusMenu, on the session bus.

That's how trays work on today's Linux desktops: KDE Plasma, Ubuntu's GNOME, Cinnamon, XFCE,
Budgie, LXQt, waybar... under X11 or Wayland, and from a Flatpak (allowed to talk to
org.kde.StatusNotifierWatcher). The tray (the "watcher") is told about the icon; it then reads its
properties (title, picture, menu) and calls it back on a click. Plain GNOME shows no tray icons at
all without its AppIndicator extension: then there's no watcher, and no icon (tray.NoTray says so).

White or black (see tray.py)? GNOME's top bar is dark, even in light mode: white. Elsewhere, panels
follow the desktop's light or dark style, which the desktop portal tells (org.freedesktop.appearance
color-scheme): black for light, white for dark or unknown. When that changes, so does the icon.

Specifications: https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/ and
libdbusmenu's com.canonical.dbusmenu interface.

(No `from __future__ import annotations` here: dbus-fast reads the D-Bus types from the annotations.)
"""
import asyncio
import os
from collections.abc import Callable

from dbus_fast import Message, MessageType, PropertyAccess, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method, signal
from PIL import Image

from . import APP_NAME
from .tray import TRAY_SIZES, WATCH_EVERY, Actions, NoTray, tray_image

WATCHER = 'org.kde.StatusNotifierWatcher'
WATCHER_PATH = '/StatusNotifierWatcher'
ITEM_PATH = '/StatusNotifierItem'
MENU_PATH = '/MenuBar'
PORTAL = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'
SETTINGS = 'org.freedesktop.portal.Settings'
LIGHT = 2  # color-scheme: 0 no preference, 1 dark, 2 light


def pixmaps(variant: str = 'white') -> list:
    """The icon as the specification wants it: (width, height, ARGB32 pixels in network byte order), in each
    size it was made for (the tray picks the one that fits)."""
    return [[size, size, argb(tray_image(size, variant))] for size in TRAY_SIZES]


def argb(image: Image.Image) -> bytes:
    """Pixels as A, R, G, B bytes (Pillow has no packer for that order: put the channels in it)."""
    red, green, blue, alpha = image.convert('RGBA').split()
    return Image.merge('RGBA', (alpha, red, green, blue)).tobytes()


class Item(ServiceInterface):
    def __init__(self, activate: Callable[[], None], variant: str = 'white') -> None:
        super().__init__('org.kde.StatusNotifierItem')
        self._activate = activate
        self.variant = variant
        self._pixmaps = pixmaps(variant)

    def set_variant(self, variant: str) -> None:
        """White or black, and tell the tray to show it again."""
        if variant != self.variant:
            self.variant, self._pixmaps = variant, pixmaps(variant)
            self.NewIcon()

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> 's':
        return 'ApplicationStatus'

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> 's':
        return 'reality-later'

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> 's':
        return APP_NAME

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> 's':
        return 'Active'

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> 'i':
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> 's':
        return ''

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> 's':
        return ''

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> 'a(iiay)':
        return self._pixmaps

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> 's':
        return ''

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> 's':
        return ''

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionMovieName(self) -> 's':
        return ''

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> '(sa(iiay)ss)':
        return ['', [], APP_NAME, 'Click to open it, right-click for its menu']

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> 'b':
        return False  # a click opens the app; the menu is for a right-click

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> 'o':
        return MENU_PATH

    @method()
    def Activate(self, x: 'i', y: 'i'):
        self._activate()

    @method()
    def SecondaryActivate(self, x: 'i', y: 'i'):
        self._activate()

    @method()
    def ContextMenu(self, x: 'i', y: 'i'):
        pass  # (the tray shows the menu itself, from MENU_PATH)

    @method()
    def Scroll(self, delta: 'i', orientation: 's'):
        pass

    @signal()
    def NewIcon(self):
        pass

    @signal()
    def NewTitle(self):
        pass

    @signal()
    def NewStatus(self) -> 's':
        return 'Active'


class Menu(ServiceInterface):
    """The menu: a root (id 0) with one item per entry (ids from 1)."""

    def __init__(self, entries: list, run: Callable[[Callable[[], None]], None]) -> None:
        super().__init__('com.canonical.dbusmenu')
        self.entries = entries
        self._run = run

    def _props(self, item_id: int) -> dict:
        if item_id == 0:
            return {'children-display': Variant('s', 'submenu')}
        entry = self.entries[item_id - 1]
        if entry.label is None:
            return {'type': Variant('s', 'separator')}
        return {'label': Variant('s', entry.label), 'enabled': Variant('b', True), 'visible': Variant('b', True)}

    def _node(self, item_id: int, depth: int) -> list:
        children = []
        if item_id == 0 and depth != 0:
            children = [Variant('(ia{sv}av)', self._node(i, depth - 1)) for i in range(1, len(self.entries) + 1)]
        return [item_id, self._props(item_id), children]

    def _known(self, item_id: int) -> bool:
        return 0 <= item_id <= len(self.entries)

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> 'u':
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> 's':
        return 'ltr'

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> 's':
        return 'normal'

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> 'as':
        return []

    @method()
    def GetLayout(self, parent_id: 'i', recursion_depth: 'i', property_names: 'as') -> 'u(ia{sv}av)':
        return [1, self._node(parent_id if self._known(parent_id) else 0, recursion_depth)]

    @method()
    def GetGroupProperties(self, ids: 'ai', property_names: 'as') -> 'a(ia{sv})':
        ids = [i for i in ids if self._known(i)] or list(range(len(self.entries) + 1))
        return [[i, self._props(i)] for i in ids]

    @method()
    def GetProperty(self, item_id: 'i', name: 's') -> 'v':
        return self._props(item_id).get(name, Variant('s', '')) if self._known(item_id) else Variant('s', '')

    @method()
    def Event(self, item_id: 'i', event_id: 's', data: 'v', timestamp: 'u'):
        self._event(item_id, event_id)

    @method()
    def EventGroup(self, events: 'a(isvu)') -> 'ai':
        for item_id, event_id, _, _ in events:
            self._event(item_id, event_id)
        return [item_id for item_id, *_ in events if not self._known(item_id)]

    @method()
    def AboutToShow(self, item_id: 'i') -> 'b':
        return False  # nothing changed: no need to read the layout again

    @method()
    def AboutToShowGroup(self, ids: 'ai') -> 'aiai':
        return [[], []]

    @signal()
    def LayoutUpdated(self) -> 'ui':
        return [1, 0]

    @signal()
    def ItemsPropertiesUpdated(self) -> 'a(ia{sv})a(ias)':
        return [[], []]

    def _event(self, item_id: int, event_id: str) -> None:
        if event_id == 'clicked' and 1 <= item_id <= len(self.entries) and self.entries[item_id - 1].action:
            self._run(self.entries[item_id - 1].action)


async def show(actions: Actions, *, bus_address: str | None = None, on_shown: Callable[[], None] | None = None,
               stopped: asyncio.Event | None = None, every: float = WATCH_EVERY) -> None:
    """Show the icon until the app stops answering (or `stopped` is set). Raises NoTray if there's no tray."""
    loop = asyncio.get_running_loop()
    stopped = stopped or asyncio.Event()

    def run(action: Callable[[], None]) -> None:  # opening the browser, or quitting: not on the bus' loop
        loop.run_in_executor(None, action)

    try:
        bus = await (MessageBus(bus_address=bus_address) if bus_address else MessageBus()).connect()
    except Exception as e:
        raise NoTray(f'no session bus to reach the desktop\'s tray ({e})') from e
    try:
        default = next((entry.action for entry in actions.menu() if entry.default), None)
        item = Item(lambda: default and run(default), await panel_variant(bus))
        bus.export(ITEM_PATH, item)
        bus.export(MENU_PATH, Menu(actions.menu(), run))
        name = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
        await bus.request_name(name)

        async def register() -> None:
            reply = await bus.call(Message(destination=WATCHER, path=WATCHER_PATH, interface=WATCHER,
                                           member='RegisterStatusNotifierItem', signature='s', body=[name]))
            if reply.message_type == MessageType.ERROR:
                raise NoTray('this desktop shows no tray icons (on GNOME, the "AppIndicator and KStatusNotifierItem '
                             'Support" extension adds them)')

        await register()
        # A tray that restarts (the panel crashed, or was replaced) forgets the icon: tell the new one.
        await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                               interface='org.freedesktop.DBus', member='AddMatch', signature='s',
                               body=[f"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                                     f"arg0='{WATCHER}'"]))

        # The desktop turns light or dark: the icon too
        await bus.call(Message(destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                               interface='org.freedesktop.DBus', member='AddMatch', signature='s',
                               body=[f"type='signal',interface='{SETTINGS}',member='SettingChanged',"
                                     f"arg0='org.freedesktop.appearance'"]))

        def watcher_changed(message: Message) -> None:
            if message.member == 'NameOwnerChanged' and message.body[:1] == [WATCHER] and message.body[2]:
                loop.create_task(_quietly(register()))
            elif message.member == 'SettingChanged' and message.body[:2] == ['org.freedesktop.appearance',
                                                                              'color-scheme']:
                item.set_variant(variant_for(_unwrap(message.body[2])))

        bus.add_message_handler(watcher_changed)
        if on_shown:
            on_shown()
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), every)
            except asyncio.TimeoutError:
                if not await loop.run_in_executor(None, actions.alive):
                    return
    finally:
        bus.disconnect()  # the tray sees the icon's name go, and removes it


def run_sni(actions: Actions) -> None:
    """The tray icon's process on Linux (see tray.run)."""
    asyncio.run(show(actions, on_shown=lambda: print(
        f'Tray icon: in the system tray. Click it to open {APP_NAME}; its menu quits it.', flush=True)))


async def panel_variant(bus: MessageBus) -> str:
    """White or black, for this desktop's panel (see the module's notes)."""
    if 'gnome' in os.environ.get('XDG_CURRENT_DESKTOP', '').lower():
        return 'white'  # GNOME's top bar (Ubuntu's, Pop's, Budgie's panel too) is dark, whatever the style
    return variant_for(await color_scheme(bus))


def variant_for(scheme) -> str:
    return 'black' if scheme == LIGHT else 'white'


async def color_scheme(bus: MessageBus) -> int | None:
    """The desktop's light or dark style, from its portal (None if it has none, or doesn't say)."""
    for member in ('ReadOne', 'Read'):  # (Read: portals before version 2)
        try:
            reply = await asyncio.wait_for(bus.call(Message(
                destination=PORTAL, path=PORTAL_PATH, interface=SETTINGS, member=member, signature='ss',
                body=['org.freedesktop.appearance', 'color-scheme'])), 2)
        except Exception:
            return None
        if reply.message_type != MessageType.ERROR:
            value = _unwrap(reply.body[0]) if reply.body else None
            return value if isinstance(value, int) else None
    return None


def _unwrap(value):
    while isinstance(value, Variant):
        value = value.value
    return value


async def _quietly(task) -> None:
    try:
        await task
    except Exception:
        pass

