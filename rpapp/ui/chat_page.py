"""The chat page: the conversation's messages as chat bubbles, and a writing bar at the bottom.

    /conversations/<conversation id>

- The character's messages are on the left, yours on the right, each with its picture.
- Messages are shown with Markdown: *text in asterisks* is in italics (actions, in roleplay),
  and what's "in quotation marks" is in the accent's color (dialogue, see dialogue.py).
- Each message has a ⋯ menu: Copy, Edit (in place), Delete (with every message after it;
  not the first message); for your messages, See prompt; for the AI's, Details.
- While the character's first message is the only one, < and > under it switch between
  the character's first messages.
- Under the AI's last reply (until you answer it), < and > switch between the versions of
  that reply; > on the last version asks the AI for another one. "2 / 3" says where you are.
- In the writing field, Return sends the message or makes a new line: Settings › Chat says which, for
  computers (sends, by default) and for phones and tablets (a new line). Phones and tablets are the
  devices used by touch: (pointer: coarse), in the browser. Either way, Shift+Return makes a new line,
  Ctrl+Return (Cmd+Return on Macs) sends, and so does the Send button.
- Send with nothing written (or only spaces) sends nothing: a menu asks what the AI should do instead.
  "Extend the last reply" (when the AI's reply is the last message: say, it stopped mid-sentence)
  makes a new version of it: the version shown, plus what the AI adds (see replies.py). "Extending…"
  shows under it meanwhile; < goes back to the version before. "Continue the scene": the AI writes its
  next message without one from you, as a message of its own (a hidden instruction stands in for yours,
  see prompts.py). It can then be extended, or get other versions, like any reply. If your message is
  the last one, it's simply the reply to it.

Sending saves your message and asks for the AI's reply (see replies.py). Until it
arrives, the writing field is locked, "Replying…" shows under the messages and
the Send button becomes Stop. On phones, the on-screen keyboard closes. If the
reply fails or is stopped, the reason shows instead, with a Retry button.

Memories (see memory.py): when old messages have left the prompt, they're written
right after the reply, with "Generating memories…" at the bottom and the field
locked the same way. Then a bubble says what's new, with a Review button (the
Memories page); the update is applied when you post your next message. The ⋮
menu at the top can generate memories now, and open the Memories page.

The ⋮ menu in the top bar can also rename the conversation, or delete it.

A dashed line among the messages shows how far the memories have read:
everything above it, nothing below. While the bubble is there, a line on the
left links it to that point (gently pulsing while memories are generated), and
its Show button scrolls up to it.

The reply is written in the background: leave the page and come back, and it's
there (or still on its way).

Messages are drawn one by one into their own box, so sending or editing redraws
only what changed, even in a long chat.

A long chat opens with its last messages only (FIRST_MESSAGES); earlier ones are drawn as you scroll
up, MORE_MESSAGES at a time, starting while they're still two screens away (OLDER_JS). What you're
reading stays in place as they go in above it. What's drawn is always the end of the chat, from
`shown_from` on: new messages are added at the bottom, and deleting removes the end. Each message's
⋯ menu is made when it's opened: made ahead for every message, those menus would be most of a long
chat's page (Copy carries the whole message).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from nicegui import ui
from nicegui.events import GenericEventArguments

from .. import APP_NAME
from ..conversations import TITLE_MAX, ChatMessage, Conversation, ConversationStore, now
from ..dialogue import mark_dialogue
from ..replies import Job, Replies
from ..safe_html import chat_html
from .list_view import plural, thumbnail
from .prompt_view import show_prompt
from .reply_view import show_reply_details
from .widgets import ask_name, confirm, page_header

MEMORY_ICON = 'psychology'

if TYPE_CHECKING:
    from ..characters import Character
    from ..personas import Persona
    from .profile_page import ProfileSection

MARKDOWN = ['break-on-newline']  # a line break in a message is a line break on screen
BUSY_TEXT = 'Wait for the reply, or stop it first.'

# Return in the writing field: send, unless it makes a new line on this kind of device (see the notes at
# the top). Not while an input method is composing a word: there, Return picks it. What's sent is the
# field's text as the key was pressed, in case its latest change hasn't reached the server yet.
RETURN_KEY_JS = '''(e) => {
  if (e.key !== "Enter" || e.isComposing || e.keyCode === 229 || e.shiftKey) return;
  const touch = window.matchMedia("(pointer: coarse)").matches;
  if (!(e.ctrlKey || e.metaKey || (touch ? MOBILE : DESKTOP))) return;
  e.preventDefault();
  emit(e.target.value);
}'''

# Close the on-screen keyboard (phones), and give the focus back on computers only (it would reopen it).
BLUR_JS = 'document.activeElement && document.activeElement.blur()'
FOCUS_JS = 'if (window.matchMedia("(pointer: fine)").matches) runMethod({id}, "focus", [])'

FIRST_MESSAGES = 30  # drawn when the chat opens: the last ones
MORE_MESSAGES = 30   # drawn each time you scroll up near the first one drawn

# The earlier messages, as you scroll up. The "Earlier messages" control above the first message drawn
# is clicked by itself once it's within two screens of view (the server then draws MORE_MESSAGES above
# it, and calls OLDER_DONE_JS, which looks again: still near?).
#
# What you're reading stays in place as they go in above it: the first message drawn is noted with its
# place in the page (not on the screen: scrolling doesn't change that, only what's added above it). When
# the list's size changes, the page is scrolled by as much as that place moved, before it's shown (a
# ResizeObserver runs after the browser's layout, before it paints): whatever the change, the messages
# going in, or their text appearing a moment later. Changes below it (a reply, an edit) don't move it.
# The browser's own way, overflow-anchor, isn't in every browser: it's off on this page (theme.py), for
# the two not to add up.
OLDER_JS = '''setTimeout(() => {
  const control = getHtmlElement(%(control)s), list = getHtmlElement(%(list)s);  // (NiceGUI's: its ids vary)
  if (!control || !list || control.rpWatch) return;
  const place = el => el.getBoundingClientRect().top + window.scrollY;
  const anchor = {};
  const note = () => { anchor.box = list.querySelector(".chat-box") || control; anchor.at = place(anchor.box); };
  note();
  new ResizeObserver(() => {
    if (!anchor.box.isConnected) return note();  // (deleted: from the next change on, the next one)
    const moved = place(anchor.box) - anchor.at;
    if (moved) window.scrollBy(0, moved);
    anchor.at = place(anchor.box);
  }).observe(list);
  control.addEventListener("click", () => { control.rpLoading = true; note(); }, true);  // (also by hand)
  control.rpWatch = new IntersectionObserver(entries => {
    const button = control.querySelector("button");
    if (!entries.some(e => e.isIntersecting) || control.rpLoading || !button || !button.offsetParent) return;
    button.click();
  }, {rootMargin: "200%% 0px 0px 0px"});
  control.rpWatch.observe(control);
}, 400)'''  # (once the chat is scrolled to its end, as it opens: not from the top, where it starts)
# Drawn (once the browser has made the changes): ready for more, and look again
OLDER_DONE_JS = '''requestAnimationFrame(() => requestAnimationFrame(() => {
  const control = getHtmlElement(%(control)s);
  if (!control || !control.rpWatch) return;
  control.rpLoading = false;
  control.rpWatch.unobserve(control);
  control.rpWatch.observe(control);
}))'''


@lru_cache(maxsize=1000)
def message_html(html: str) -> str:
    """A message's HTML (its Markdown, rendered), cleaned (see safe_html.py), with its dialogue marked."""
    return mark_dialogue(chat_html(html))


def _menu_choice(icon: str, title: str, caption: str, on_click: Callable[[], Any], *,
                 enabled: bool = True) -> ui.menu_item:
    """A menu item with an icon, a title, and a line under it saying what it does (or why it can't)."""
    with ui.menu_item(on_click=on_click) as item:
        with ui.item_section().props('avatar'):
            ui.icon(icon)
        with ui.item_section():
            ui.item_label(title)
            ui.item_label(caption).props('caption')
    item.set_enabled(enabled)
    return item


def short_time(when: datetime) -> str:
    local = when.astimezone()
    today = now().astimezone().date()
    return f'{local:%H:%M}' if local.date() == today else f'{local.day} {local:%b}, {local:%H:%M}'


def copy_js(text: str) -> str:
    """A browser-side click handler copying `text` (it must run during the click itself)."""
    return f'() => window.rpCopy({json.dumps(text)})'


class ChatPage:
    def __init__(self, conversation: Conversation, *, store: ConversationStore,
                 characters: ProfileSection[Character], personas: ProfileSection[Persona],
                 replies: Replies, back: str) -> None:
        self.conversation = conversation
        self.store = store
        self.characters = characters
        self.personas = personas
        self.replies = replies
        self.back = back
        self.editing: str | None = None              # id of the message being edited
        self.boxes: dict[str, ui.element] = {}       # message id -> the box it's drawn in
        self.shown_from = 0                          # the first message drawn: those after it are too
        self.marker_after: str | None = None         # the message the dashed line follows (drawn or not)
        self.link_wanted = (False, False)            # the line joining it to the memory bubble (see _link)
        self.busy = False                            # a reply is on its way
        self.rewriting: str | None = None            # id of the reply getting another version
        self.extending = False                       # that version: the one shown, extended
        self.client = ui.context.client

        character = characters.store.get(conversation.character.id)
        persona = personas.store.get(conversation.persona.id)
        self.char_name = (character.name or character.title) if character else conversation.character.title
        self.user_name = (persona.name or persona.title) if persona else conversation.persona.title
        self.char_image = characters.image_url(character.image) if character else ''
        self.user_image = personas.image_url(persona.image) if persona else ''

    # -- the page --------------------------------------------------------------------

    def build(self) -> None:
        c = self.conversation

        async def delete_conversation() -> None:
            if await confirm(f'Delete "{c.title}"?', 'The whole chat is lost. This can\'t be undone.', 'Delete'):
                self.replies.stop(c.id)
                self.store.delete(c.id)
                ui.notify(f'Deleted "{c.title}"')
                ui.navigate.to(self.back)

        menu = [('Rename', self._rename, False), ('Delete conversation', delete_conversation, True)]
        settings = self.replies.current_settings()
        self.memory_on = settings.memory.enabled
        self.return_sends = settings.chat.return_key_desktop == 'send'  # on computers: for the tooltip
        if self.memory_on:
            menu = [menu[0], ('Generate memories now', self._remember_now, False),
                    ('Memories', lambda: ui.navigate.to(f'{self.path}/memories'), False), *menu[1:]]
        with ui.column().classes('chat-page') as self.page_column:
            self.title_label = page_header(c.title, subtitle=f'{c.character.title}, with you as {c.persona.title}',
                                           back=self.back, menu=menu)

            self.empty_hint = ui.label(f'{self.char_name} has no first message. Write the first one below.') \
                .classes('muted chat-empty')
            self.list = ui.element('div').classes('chat-messages')
            self.shown_from = max(0, len(c.messages) - FIRST_MESSAGES)
            self.had_older = self.shown_from > 0  # (then, once all are drawn: "The start of the chat")
            with self.list:
                with ui.element('div').classes('chat-older').mark('chat-older') as self.older:
                    self.older_button = ui.button('Earlier messages', icon='expand_less',
                                                  on_click=lambda: self._load_older()) \
                        .props('flat dense no-caps').classes('muted').mark('load-older')
                    self.older_start = ui.label('The start of the chat').classes('text-sm muted')
                for message in c.messages[self.shown_from:]:
                    self._add_box(message)
                # How far the memories have read: moved after that message's box (see _place_marker).
                with ui.element('div').classes('memory-marker').mark('memory-marker') as self.marker:
                    ui.element('div').classes('memory-dash')
                    ui.icon(MEMORY_ICON, size='xs')
                    self.marker_label = ui.label().classes('memory-marker-label')
                    ui.element('div').classes('memory-dash')
                self.marker.set_visibility(False)
            self.status = ui.element('div').classes('w-full chat-reply-status')  # "Replying…", or why not
            self.memory_box = ui.element('div').classes('w-full')  # "Writing memories…", or what's new
            self._update_empty_hint()
            self._update_older()

            with ui.element('div').classes('chat-compose'):
                with ui.row().classes('w-full items-end no-wrap gap-2'):
                    phone_sends = settings.chat.return_key_mobile == 'send'
                    self.input = ui.textarea(placeholder=f'Write as {self.user_name}…') \
                        .props('autogrow outlined dense rounded input-style="max-height: 35vh"') \
                        .classes('grow chat-input').mark('chat-input')
                    # On-screen keyboards label their Return key "Send" (computers' keyboards ignore this)
                    self.input.props(f'enterkeyhint={"send" if phone_sends else "enter"}')
                    self.input.on('keydown', self._return_key, js_handler=RETURN_KEY_JS
                                  .replace('MOBILE', json.dumps(phone_sends))
                                  .replace('DESKTOP', json.dumps(self.return_sends)))
                    with ui.button(icon='send', on_click=self._send_or_stop) \
                            .props('round unelevated aria-label="Send"').mark('send') as self.button:
                        self.tooltip = ui.tooltip(self._send_tooltip)
                        # Opened by _send_or_stop, with nothing written, not by every click on the button
                        self.send_menu = ui.menu() \
                            .props('no-parent-event anchor="top right" self="bottom right" :offset="[0, 8]"') \
                            .classes('send-menu').mark('send-menu')

        if job := self.replies.running(c.id):
            self._show_marker()
            self._wait_for(job)  # it was started before this page was opened
        else:
            self._show_status()
            self._show_memory_box()
        self._scroll_to_bottom()
        self._js(OLDER_JS % {'control': self.older.id, 'list': self.list.id})

    async def _rename(self) -> None:
        name = await ask_name('Rename the conversation', self.conversation.title, max_length=TITLE_MAX)
        if not name:
            return
        try:
            self.store.rename(self.conversation.id, name)
        except KeyError:
            ui.notify('This conversation was deleted.', type='warning')
            return
        self.conversation.title = self.title_label.text = name
        ui.page_title(f'{name} · {APP_NAME}')

    def _save(self) -> None:
        """Save the chat as this page has it, but with the title in the file: it may have been renamed
        elsewhere (the list, another device) since this page was opened."""
        try:
            stored = self.store.get(self.conversation.id)
        except ValueError:
            stored = None
        if stored is not None:
            self.conversation.title = stored.title
        self.store.save(self.conversation)

    @property
    def path(self) -> str:
        return f'/conversations/{self.conversation.id}'

    def _js(self, code: str) -> None:
        """Run JavaScript in this page's tab (whatever element the current event came from)."""
        self.client.run_javascript(code)

    def _scroll_to_bottom(self) -> None:
        self._js('setTimeout(() => window.scrollTo({top: document.body.scrollHeight}), 150)')

    def _update_empty_hint(self) -> None:
        self.empty_hint.set_visibility(not self.conversation.messages)

    # -- earlier messages --------------------------------------------------------------

    def _update_older(self) -> None:
        """Above the first message drawn: "Earlier messages" while there are some, then "The start of the
        chat" (as high: what's below doesn't move). Nothing for a chat that was drawn whole from the start."""
        more = self.shown_from > 0
        self.older.set_visibility(more or self.had_older)
        self.older_button.set_visibility(more)
        self.older_start.set_visibility(not more)
        self.had_older = self.had_older or more

    def _load_older(self, down_to: int | None = None) -> None:
        """Draw the messages before those drawn: MORE_MESSAGES of them, or all of them from `down_to` on."""
        start = max(0, self.shown_from - MORE_MESSAGES) if down_to is None else max(0, min(down_to, self.shown_from))
        older = self.conversation.messages[start:self.shown_from]
        for i, message in enumerate(older):
            self._add_box(message)
            self.boxes[message.id].move(self.list, target_index=1 + i)  # (right after the control)
        self.shown_from = start
        self._update_older()
        if older and self.marker_after and self.marker_after in {m.id for m in older}:
            self._show_marker()  # its message is here now
            on, pulsing = self.link_wanted
            self._link(on, pulsing=pulsing)
        self._js(OLDER_DONE_JS % {'control': self.older.id})

    # -- messages ----------------------------------------------------------------------

    def _add_box(self, message: ChatMessage) -> None:
        with self.list:
            box = ui.element('div').classes('w-full chat-box').mark(f'message-{message.id}')
        self.boxes[message.id] = box
        self._draw(message)

    def _redraw(self, message_id: str) -> None:
        message = self.conversation.message(message_id)
        if message and message_id in self.boxes:
            self._draw(message)

    def _draw(self, message: ChatMessage) -> None:
        box = self.boxes[message.id]
        box.clear()
        mine = message.role == 'user'
        name, image = (self.user_name, self.user_image) if mine else (self.char_name, self.char_image)
        editing = self.editing == message.id
        with box, ui.element('div').classes('chat-row ' + ('mine' if mine else 'theirs')):
            thumbnail(name, image).classes('chat-avatar')
            with ui.element('div').classes('chat-bubble' + (' editing' if editing else '')):
                with ui.row().classes('w-full items-center no-wrap gap-2 chat-head'):
                    ui.label(name).classes('chat-name')
                    ui.label(short_time(message.created_at) + (', edited' if message.edited_at else '')) \
                        .classes('chat-time')
                    if not editing:
                        self._menu(message)
                if editing:
                    self._editor(message)
                elif self.rewriting == message.id and not self.extending:
                    self._replying_text()
                else:
                    # Cleaned here (message_html), and again in the browser by DOMPurify ('sanitize')
                    ui.markdown(message.content, extras=MARKDOWN, sanitize=message_html).props('sanitize') \
                        .classes('chat-text')
                    if self.rewriting == message.id:  # extending it
                        self._replying_text('Extending').classes('mt-1').mark('extending')
                if editing or self.rewriting == message.id:
                    pass
                elif message.greeting and self.conversation.can_choose_greeting:
                    self._greeting_picker(message)
                elif self.conversation.can_change_reply and message is self.conversation.messages[-1]:
                    self._reply_picker(message)

    @staticmethod
    def _replying_text(word: str = 'Replying') -> ui.html:
        return ui.html(f'{word}<span class="chat-dots" aria-hidden="true">'
                       '<span>.</span><span>.</span><span>.</span></span>', sanitize=False).classes('chat-replying')

    def _menu(self, message: ChatMessage) -> None:
        """The ⋯ button. Its menu is filled when it's opened (see the notes at the top)."""
        if self.rewriting == message.id:
            return
        with ui.button(icon='more_horiz', on_click=lambda: self._open_menu(menu, message.id)) \
                .props('flat round dense size=sm aria-label="Message actions"').classes('chat-actions') \
                .mark('actions'):
            menu = ui.menu().props('no-parent-event')

    def _open_menu(self, menu: ui.menu, message_id: str) -> None:
        message = self.conversation.message(message_id)
        if message is None:
            return
        menu.clear()
        with menu:
            ui.menu_item('Copy', lambda: ui.notify('Copied')) \
                .on('click', js_handler=copy_js(message.content)).mark('menu-copy')
            ui.menu_item('Edit', lambda: self._start_edit(message.id)).mark('menu-edit')
            if message.role == 'user':
                ui.menu_item('See prompt', lambda: self._show_prompt(message.id)).mark('menu-prompt')
            elif message.role == 'assistant' and not message.greeting:
                ui.menu_item('Details', lambda: self._show_details(message)).mark('menu-details')
            if not message.greeting:
                ui.menu_item('Delete', lambda: self._delete(message.id)) \
                    .classes('text-negative').mark('menu-delete')
        menu.open()

    def _greeting_picker(self, message: ChatMessage) -> None:
        def step(direction: int) -> None:
            self.conversation.choose_greeting(direction)
            self._save()
            self._redraw(message.id)

        with ui.row().classes('w-full items-center justify-between no-wrap mt-2 chat-pick'):
            ui.button(icon='chevron_left', on_click=lambda: step(-1)) \
                .props('flat round dense aria-label="Previous first message"').mark('greeting-previous')
            ui.label(f'{message.alternative + 1} / {len(message.alternatives)}').classes('text-sm muted')
            ui.button(icon='chevron_right', on_click=lambda: step(1)) \
                .props('flat round dense aria-label="Next first message"').mark('greeting-next')

    def _reply_picker(self, message: ChatMessage) -> None:
        index, count = (message.alternative if message.alternatives else 0), message.versions

        def show(i: int) -> None:
            if self.busy:
                ui.notify(BUSY_TEXT)
                return
            self.conversation.choose_reply(i)
            self._save()
            self.status.clear()
            self._redraw(message.id)

        def forward() -> None:
            if index + 1 < count:
                show(index + 1)
            elif self.busy:
                ui.notify(BUSY_TEXT)
            else:
                self._ask_for_version(message.id)

        with ui.row().classes('w-full items-center justify-between no-wrap mt-2 chat-pick'):
            ui.button(icon='chevron_left', on_click=lambda: show(index - 1)) \
                .props('flat round dense aria-label="Previous reply"').mark('reply-previous') \
                .set_enabled(index > 0)
            ui.label(f'{index + 1} / {count}').classes('text-sm muted').mark('reply-count')
            last = index + 1 == count
            with ui.button(icon='chevron_right', on_click=forward) \
                    .props(f'flat round dense aria-label="{"Another reply" if last else "Next reply"}"') \
                    .mark('reply-next'):
                ui.tooltip('Ask for another reply' if last else 'Next reply')

    # -- editing -----------------------------------------------------------------------

    def _start_edit(self, message_id: str) -> None:
        if self.busy:
            ui.notify(BUSY_TEXT)
            return
        previous, self.editing = self.editing, message_id
        if previous:
            self._redraw(previous)
        self._redraw(message_id)

    def _editor(self, message: ChatMessage) -> None:
        def save() -> None:
            text = (editor.value or '').strip()
            if not text:
                ui.notify('A message can\'t be empty. Delete it instead.', type='warning')
                return
            if self.busy:
                ui.notify(BUSY_TEXT)
                return
            if text != message.content:
                self.conversation.edit(message.id, text)
                self._save()
            stop()

        def stop() -> None:
            self.editing = None
            self._redraw(message.id)

        editor = ui.textarea(value=message.content) \
            .props('autogrow outlined dense autofocus input-style="max-height: 60vh"').classes('w-full mt-1') \
            .mark('edit-input')
        editor.on('keydown.ctrl.enter', save).on('keydown.meta.enter', save).on('keydown.esc', stop)
        with ui.row().classes('w-full justify-end gap-2 mt-2'):
            ui.button('Cancel', on_click=stop).props('flat dense').mark('edit-cancel')
            ui.button('Save', on_click=save).props('dense').classes('px-3').mark('edit-save')

    # -- deleting ----------------------------------------------------------------------

    async def _delete(self, message_id: str) -> None:
        if self.busy:
            ui.notify(BUSY_TEXT)
            return
        c = self.conversation
        message = c.message(message_id)
        if message is None:
            return
        after = len(c.messages) - c.messages.index(message) - 1
        text = 'This message will be deleted.' if not after else \
            f'This message and the {plural(after, "message")} after it will be deleted.'
        if not await confirm('Delete this message?', f'{text} This can\'t be undone.', 'Delete'):
            return
        if self.busy:  # a reply started while the dialog was open (from another tab)
            ui.notify(BUSY_TEXT)
            return
        deleted = c.delete_from(message_id)
        self.store.delete_message_files(c.id, [m.id for m in deleted])
        self._save()
        for m in deleted:
            box = self.boxes.pop(m.id, None)
            if box is not None:
                box.delete()
        self.shown_from = min(self.shown_from, len(c.messages))  # (the first drawn may be gone: earlier ones
        self._update_older()                                       # come as the control is in view again)
        if self.editing in {m.id for m in deleted}:
            self.editing = None
        if c.messages:
            self._redraw(c.messages[-1].id)  # now the last one: its < and > may be back
        self._update_empty_hint()
        self._show_status()
        self._show_memory_box()  # memories learnt from the deleted messages are undone

    # -- sending and replies -----------------------------------------------------------

    def _send_or_stop(self) -> None:
        if self.busy:
            self.replies.stop(self.conversation.id)
        elif not (self.input.value or '').strip():
            self._open_send_menu()
        else:
            self.send()

    def _open_send_menu(self) -> None:
        """With nothing written: what the AI can do without a message from you."""
        c = self.conversation
        if c.can_change_reply:
            extend = 'The AI goes on with its last message: say, if it stopped mid-sentence'
        elif c.messages and c.messages[-1].role == 'user':
            extend = 'The last message is yours'
        else:
            extend = 'There\'s no reply from the AI yet'
        if not c.messages:
            go_on = f'{self.char_name} writes the first message'
        elif c.messages[-1].role == 'user':
            go_on = f'{self.char_name} replies to your last message'
        else:
            go_on = f'{self.char_name} goes on, without a message from you'
        self.send_menu.clear()
        with self.send_menu:
            _menu_choice('read_more', 'Extend the last reply', extend, self._extend_reply,
                         enabled=c.can_change_reply).mark('extend-reply')
            _menu_choice('double_arrow', 'Continue the scene', go_on, self._continue_scene).mark('continue-scene')
        self.tooltip.run_method('hide')  # it would cover the menu
        self.send_menu.open()

    def _extend_reply(self) -> None:
        c = self.conversation
        if self.busy:
            ui.notify(BUSY_TEXT)
        elif c.can_change_reply:
            self._js(BLUR_JS)
            self._wait_for(self.replies.start(c.id, version_of=c.messages[-1].id, extend=True))

    def _continue_scene(self) -> None:
        if self.busy:
            ui.notify(BUSY_TEXT)
            return
        self._js(BLUR_JS)
        self._wait_for(self.replies.start(self.conversation.id, continue_scene=True))

    @property
    def _send_tooltip(self) -> str:
        return 'Send (Return)' if self.return_sends else 'Send (Ctrl+Return)'

    def _return_key(self, e: GenericEventArguments) -> None:
        text = e.args[0] if isinstance(e.args, list) and e.args else e.args
        if isinstance(text, str) and not self.busy:
            self.input.value = text
        self.send()

    def send(self) -> None:
        text = (self.input.value or '').strip()
        if self.busy or not text:
            return
        c = self.conversation
        self.input.value = ''
        self.store.memories.apply_pending(c.id)  # the memories written since your last message
        self.memory_box.clear()
        self._link(False)
        self._show_marker()  # (same place, "Remembered up to here")
        message = c.add('user', text)
        self._save()
        if len(c.messages) >= 2:
            self._redraw(c.messages[-2].id)  # no longer the last message: its < and > go
        self._add_box(message)
        self._update_empty_hint()
        self._ask_for_reply()

    def _ask_for_reply(self) -> None:
        self._js(BLUR_JS)  # closes the keyboard on phones
        self._wait_for(self.replies.start(self.conversation.id))

    def _ask_for_version(self, message_id: str) -> None:
        self._js(BLUR_JS)
        self._wait_for(self.replies.start(self.conversation.id, version_of=message_id))

    def _wait_for(self, job: Job) -> None:
        if job.kind == 'memory':
            self._wait_for_memories(job)
            return
        self._set_busy(True)
        self.status.clear()
        if job.version_of:  # "Replying…" in the reply itself ("Extending…" under it)
            self.rewriting, self.extending = job.version_of, job.extend
            self._redraw(job.version_of)
        else:
            with self.status, ui.element('div').classes('chat-row theirs').mark('replying'):
                thumbnail(self.char_name, self.char_image).classes('chat-avatar')
                with ui.element('div').classes('chat-bubble chat-waiting'):
                    self._replying_text()
        self._scroll_to_bottom()
        job.on_done(self._reply_done)

    def _reply_done(self, job: Job) -> None:
        if self.client.is_deleted or self.list.is_deleted:  # the page was closed or left: nothing to show
            return
        with self.status:
            fresh = self.store.get(self.conversation.id)
            if fresh is not None:
                self.conversation = fresh
            self.rewriting, self.extending = None, False
            messages = self.conversation.messages
            self.shown_from = min(self.shown_from, len(messages))
            added = [m for m in messages[self.shown_from:] if m.id not in self.boxes]  # (not the earlier ones)
            for message in added:
                self._add_box(message)
            if added:
                if (index := messages.index(added[0])) > 0:
                    self._redraw(messages[index - 1].id)  # no longer the last: its < and > go
                self._update_empty_hint()
            if job.version_of:
                self._redraw(job.version_of)  # the new version, or the one shown before if it failed
            self._set_busy(False)
            self._show_status(job)
            if job.details and job.details.finish_reason == 'length':
                ui.notify('The reply was cut off at the max reply length. See its ⋯ menu, Details.',
                          type='warning')
            if (following := self.replies.running(self.conversation.id)) and following.kind == 'memory':
                self._wait_for_memories(following)  # old messages left the prompt: memories come next
                return
            self._js(FOCUS_JS.format(id=self.input.id))
            self._scroll_to_bottom()

    # -- memories ----------------------------------------------------------------------

    def _remember_now(self) -> None:
        if self.busy:
            ui.notify(BUSY_TEXT)
            return
        job = self.replies.start_memory(self.conversation.id, manual=True)
        if job is None:
            ui.notify('Nothing new to remember: the memories have read every message so far'
                      + (', except the last reply, while you can still change it.' if
                         self.conversation.can_change_reply else '.'))
            return
        self._js(BLUR_JS)
        self._wait_for_memories(job)

    def _memory_bubble(self, marker: str, *, linked: bool = False) -> ui.element:
        """A bubble at the bottom of the chat, with the memory icon; returns its inside.

        `linked`: it's about the point the dashed line marks, so a line on the left joins them.
        """
        self.memory_box.clear()
        with self.memory_box, ui.element('div').classes('chat-row theirs' + (' memory-linked' if linked else '')) \
                .mark(marker):
            with ui.element('div').classes('profile-thumb chat-avatar'):
                ui.icon(MEMORY_ICON, size='sm')
            bubble = ui.element('div').classes('chat-bubble chat-waiting chat-memory')
        self._link(linked)
        return bubble

    # -- the dashed line ---------------------------------------------------------------

    def _place_marker(self, message_id: str, text: str) -> None:
        """Put the dashed line right after this message (or hide it: '' or a message that's not here, or not
        drawn yet: an earlier one, see _load_older)."""
        self.marker_after = message_id or None
        box = self.boxes.get(message_id)
        if not self.memory_on or box is None:
            self.marker.set_visibility(False)
            return
        boxes = [child for child in self.list.default_slot.children if child is not self.marker]
        self.marker.move(self.list, target_index=boxes.index(box) + 1)
        self.marker_label.set_text(text)
        self.marker.set_visibility(True)

    def _show_marker(self) -> None:
        """Place the dashed line for the memories as they are, or for those being generated."""
        job = self.replies.running(self.conversation.id)
        if job and job.kind == 'memory' and job.reading:
            self._place_marker(job.reading[-1], 'Generating memories up to here…')
            return
        memories = self.store.memories.get(self.conversation.id)
        if memories.pending:
            self._place_marker(memories.pending.last, 'Memories generated up to here: added with your next message')
        else:
            self._place_marker(memories.read_until, 'Remembered up to here')

    def _link(self, on: bool, *, pulsing: bool = False) -> None:
        """Show (or hide) the line on the left joining the dashed line to the memory bubble. (Asked for while
        the dashed line isn't drawn, it's shown once it is: see _load_older.)"""
        self.link_wanted = (on, pulsing)
        on = on and self.marker.visible
        self.page_column.classes(add='memory-threaded' if on else None, remove=None if on else 'memory-threaded')
        pulse = on and pulsing
        self.page_column.classes(add='memory-pulsing' if pulse else None, remove=None if pulse else 'memory-pulsing')

    def _scroll_to_marker(self) -> None:
        c, after = self.conversation, self.marker_after
        if after and after not in self.boxes and (message := c.message(after)) is not None:
            self._load_older(down_to=max(0, c.messages.index(message) - 1))  # (and the one before it)
        self._js(f'setTimeout(() => getHtmlElement({self.marker.id})'
                 '?.scrollIntoView({behavior: "smooth", block: "center"}), 150)')  # (once drawn)

    def _read_count(self, memories) -> int:
        """How many messages the pending update read."""
        ids = [m.id for m in self.conversation.messages]
        p = memories.pending
        if p is None or p.first not in ids or p.last not in ids:
            return 0
        return ids.index(p.last) - ids.index(p.first) + 1

    def _wait_for_memories(self, job: Job) -> None:
        self._set_busy(True, stopping='Stop writing memories')
        self._show_marker()
        count = f' from {plural(len(job.reading), "message")}' if job.reading else ''
        with self._memory_bubble('memory-writing', linked=True):
            with ui.row().classes('items-baseline gap-2 no-wrap'):
                ui.html(f'Generating memories{count}<span class="chat-dots" aria-hidden="true">'
                        '<span>.</span><span>.</span><span>.</span></span>', sanitize=False).classes('chat-replying')
                progress = ui.label(job.progress).classes('text-sm muted')
            with ui.row().classes('w-full justify-end mt-1'):
                ui.button('Show', icon='north', on_click=self._scroll_to_marker) \
                    .props('flat dense no-caps').mark('memory-show')
        self._link(True, pulsing=True)
        job.on_progress(lambda j: None if progress.is_deleted else progress.set_text(j.progress))
        self._scroll_to_bottom()
        job.on_done(self._memories_done)

    def _memories_done(self, job: Job) -> None:
        if self.client.is_deleted or self.list.is_deleted:
            return
        with self.memory_box:
            self._set_busy(False)
            self._show_memory_box(job)
            self._js(FOCUS_JS.format(id=self.input.id))
            self._scroll_to_bottom()

    def _show_memory_box(self, job: Job | None = None) -> None:
        """At the bottom: the memories written since your last message, or why they weren't.

        Places the dashed line too.
        """
        self.memory_box.clear()
        self._link(False)
        self._show_marker()
        c = self.conversation
        if job is None:  # after opening the page: a failure is mentioned while the chat is as it was then
            last = self.replies.last(c.id)
            if last and last.kind == 'memory' and last.finished and (last.error or last.stopped) \
                    and c.messages and last.reply_to == c.messages[-1].id:
                job = last
        if job and (job.error or job.stopped):
            text = 'You stopped the memories.' if job.stopped else f'Memories not generated: {job.error}'
            with self._memory_bubble('memory-status'):
                ui.label(text).classes('text-sm ' + ('muted' if job.stopped else 'text-negative'))
                with ui.row().classes('w-full justify-end gap-2 mt-1'):
                    if job.error and 'Settings' in job.error:
                        ui.button('Settings', on_click=lambda: ui.navigate.to('/settings')).props('flat dense no-caps')
                    ui.button('Retry', icon='refresh', on_click=lambda: self._retry_memories(job.manual)) \
                        .props('flat dense no-caps').mark('memory-retry')
            return
        memories = self.store.memories.get(c.id)
        if memories.pending is None:
            return
        summary = memories.changes().summary
        count = self._read_count(memories)
        source = f' from {plural(count, "message")}' if count else ''
        with self._memory_bubble('memory-status', linked=True):
            if not summary:
                ui.label(f'Memories checked{source}: nothing new to remember.').classes('text-sm muted')
            else:
                ui.label(f'Memories generated{source}: {summary}.').classes('text-sm text-weight-medium')
                ui.label('They\'re added when you post your next message.').classes('text-sm muted')
            with ui.row().classes('w-full justify-end gap-1 mt-1'):
                ui.button('Show', icon='north', on_click=self._scroll_to_marker) \
                    .props('flat dense no-caps').mark('memory-show')
                if summary:
                    ui.button('Review', icon='visibility',
                              on_click=lambda: ui.navigate.to(f'{self.path}/memories')) \
                        .props('flat dense no-caps').mark('memory-review')

    def _retry_memories(self, manual: bool) -> None:
        job = self.replies.start_memory(self.conversation.id, manual=manual)
        if job is None:
            self._show_memory_box()
            ui.notify('Nothing new to remember.')
            return
        self._js(BLUR_JS)
        self._wait_for_memories(job)

    def _set_busy(self, busy: bool, *, stopping: str = 'Stop the reply') -> None:
        self.busy = busy
        self.input.set_enabled(not busy)
        label = 'Stop' if busy else 'Send'
        self.button.props(f'icon={"stop" if busy else "send"} aria-label="{label}"')
        self.tooltip.text = stopping if busy else self._send_tooltip

    def _show_status(self, job: Job | None = None) -> None:
        """Under the messages: why there's no (new) reply, with a Retry button.

        Without `job`, after opening the page: only about your last message, if it has no reply.
        """
        self.status.clear()
        c = self.conversation
        if self.busy:
            return
        last = c.messages[-1] if c.messages else None
        if job is None and (job := self.replies.last(c.id)) and (job.version_of or job.kind == 'memory'):
            job = None  # a failed other version is only mentioned right after it happened
        if job and job.continue_scene and not job.version_of:
            if (last.id if last else '') != job.reply_to or not (job.stopped or job.error):
                return
            text, error = ('You stopped the new message.', False) if job.stopped else \
                (f'No new message: {job.error}', True)
            retry, label = self._continue_scene, 'Retry'
        elif last is None:
            return
        elif job and job.version_of:
            if last.id != job.version_of or not (job.stopped or job.error):
                return
            if job.extend:
                text, error = ('You stopped the extension.', False) if job.stopped else \
                    (f'Not extended: {job.error}', True)
                retry, label = self._extend_reply, 'Retry'
            else:
                text, error = ('You stopped the new reply.', False) if job.stopped else \
                    (f'No new reply: {job.error}', True)
                retry, label = (lambda: self._ask_for_version(last.id)), 'Retry'
        else:
            if last.role != 'user':
                return
            if job and job.reply_to != last.id:
                job = None  # about another message
            if job and job.stopped:
                text, error = 'You stopped the reply.', False
            elif job and job.error:
                text, error = f'No reply: {job.error}', True
            else:
                text, error = f'{self.char_name} hasn\'t replied to your last message.', False
            retry, label = self._ask_for_reply, 'Retry' if job else 'Get a reply'
        with self.status, ui.row().classes('w-full items-center no-wrap gap-2 chat-status').mark('reply-status'):
            ui.icon('error_outline' if error else 'info', size='sm') \
                .classes('text-negative' if error else 'muted')
            ui.label(text).classes('text-sm grow ' + ('text-negative' if error else 'muted'))
            if error and 'Settings' in text:
                ui.link('Settings', '/settings').classes('text-sm')
            ui.button(label, icon='refresh', on_click=retry).props('flat dense no-caps').mark('retry')

    # -- prompts and reply details -----------------------------------------------------

    # (`with self.list`: the element the click came from may be gone, like a closed dialog's button)

    def _show_prompt(self, message_id: str) -> None:
        with self.list:
            show_prompt(self.store.prompts.get(self.conversation.id, message_id),
                        self.conversation.message(message_id), time=short_time, copy_js=copy_js)

    def _show_details(self, message: ChatMessage) -> None:
        details = self.replies.details(self.conversation.id, message)
        version = f'Version {message.alternative + 1} of {message.versions}' if message.alternatives else ''
        if details and details.prompt:  # an extension, or a continued scene: its prompt is kept with it
            def open_prompt() -> None:
                with self.list:
                    show_prompt(details.prompt, None, time=short_time, copy_js=copy_js,
                                title='Prompt for this extension' if details.extends is not None else
                                'Prompt for this message')
        elif details and details.reply_to and self.conversation.message(details.reply_to):
            def open_prompt() -> None:
                self._show_prompt(details.reply_to)
        else:
            open_prompt = None
        with self.list:
            show_reply_details(details, message, time=short_time, version=version, open_prompt=open_prompt)
