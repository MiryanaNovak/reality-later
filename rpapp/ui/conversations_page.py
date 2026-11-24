"""The Conversations section: a tree of conversations grouped by character or by persona, and the chat page.

    /conversations                     the tree; groups and their conversations, most recently updated first
    /conversations?character=<id>      the same, with that character's group open and in view
        &persona=<id>                  (and that persona's, when they're grouped by persona)
    /conversations/<conversation id>   the chat (see chat_page.py)
    /conversations/<conversation id>/memories   what the AI remembers of it (memories_page.py)

At the top, By character / By persona says how they're grouped (kept for the device, in a cookie: opened.py):
by character, each character's group lists its conversations with your persona's picture, "As <persona>";
by persona, each persona's lists them with the character's picture, "With <character>". A character's picture
opens their page (to look at); a persona's, theirs (its editor): their back arrow comes back here, the same group
open. Under it, the search
(once there are 8 conversations, as on the Characters page) finds them by title, character or persona: only
those found show, in their groups.

Conversations are started from a character's card (New chat), titled with the date and time. Their
⋮ menu, here and in the chat, can rename them. A character's page lists its own the same way
(character_list()).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from urllib.parse import quote

from nicegui import ui

from ..characters import Character
from ..conversations import TITLE_MAX, Conversation, ConversationStore
from ..personas import Persona
from ..replies import Replies
from .chat_page import ChatPage
from .memories_page import MemoriesPage
from .list_view import Action, ListView, Row, plural
from .opened import Chosen
from .profile_page import ProfileSection
from .tree_view import TreeGroup, TreeView
from .widgets import ask_name, confirm, not_found, page_bar, time_ago


GROUPINGS = {'character': 'By character', 'persona': 'By persona'}


class ConversationsSection:
    path = '/conversations'

    def __init__(self, store: ConversationStore, characters: ProfileSection[Character],
                 personas: ProfileSection[Persona], replies: Replies) -> None:
        self.store = store
        self.characters = characters
        self.personas = personas
        self.replies = replies
        # Filled while building the groups, used by the rows
        self._characters: dict[str, Character] = {}
        self._personas: dict[str, Persona] = {}
        self.grouping = Chosen('rp_conversations_by', tuple(GROUPINGS))

    # -- the tree --------------------------------------------------------------------

    def list_page(self, character: str = '', persona: str = '') -> None:
        """`character` and `persona` (from ?character=...&persona=...): the group to open and bring into view, the
        one of the grouping chosen."""
        page_bar('Conversations')
        wanted = {'character': character, 'persona': persona}

        def regroup(by: str) -> None:
            self.grouping.set(by)
            tree.focus(wanted[by])
            tree.refresh()

        with ui.row().classes('w-full items-center gap-2'):
            ui.toggle(GROUPINGS, value=self.grouping.get(), on_change=lambda e: regroup(e.value)) \
                .props('dense no-caps unelevated toggle-color=primary').mark('conversations-by')
        tree = TreeView(
            groups=self._groups,
            key=lambda conversation: conversation.id,
            row=lambda conversation: self._row(conversation, by=self.grouping.get(), linked=True),
            on_open=lambda conversation: ui.navigate.to(f'{self.path}/{conversation.id}'),
            actions=self._actions(lambda: tree.refresh()),
            focus=wanted[self.grouping.get()],
            empty_title='No conversations yet',
            empty_text='Start one from a character: open Characters and press New chat on a card.',
            search=self._search_text,
            noun='conversation',
        )
        if character and not self.store.using('character', character):
            ui.notify('This character has no conversations yet.')

    @staticmethod
    def _search_text(conversation: Conversation) -> str:
        return f'{conversation.title} {conversation.character.title} {conversation.persona.title}'

    def character_list(self, character: Character) -> ListView[Conversation]:
        """A character's conversations, most recently updated first, as the tree shows them (for its page)."""
        def items() -> list[Conversation]:
            self._personas = {p.id: p for p in self.personas.store.list()}
            found = self.store.using('character', character.id)
            for conversation in found:
                self._refresh_titles(conversation, {character.id: character}, self._personas)
            return found

        view = ListView(
            items=items,
            key=lambda conversation: conversation.id,
            row=self._row,
            on_open=lambda conversation: ui.navigate.to(f'{self.path}/{conversation.id}'),
            actions=self._actions(lambda: view.refresh()),
            search=self._search_text,
            empty_title='No conversations yet',
            empty_text='Start one with New chat.',
        )
        return view

    def _actions(self, refresh: Callable[[], None]) -> list[Action[Conversation]]:
        """A conversation's ⋮ menu: Rename, Delete. `refresh` redraws the list after either."""
        async def delete(conversation: Conversation) -> None:
            if await confirm(f'Delete "{conversation.title}"?', 'The whole chat is lost. This can\'t be undone.',
                             'Delete'):
                self.replies.stop(conversation.id)
                self.store.delete(conversation.id)
                ui.notify(f'Deleted "{conversation.title}"')
                refresh()

        async def rename(conversation: Conversation) -> None:
            if name := await ask_name('Rename the conversation', conversation.title, max_length=TITLE_MAX):
                try:
                    self.store.rename(conversation.id, name)
                except KeyError:
                    ui.notify('This conversation was deleted meanwhile.', type='warning')
                refresh()

        return [Action('Rename', rename), Action('Delete', delete, danger=True)]

    def _groups(self) -> list[TreeGroup[Conversation]]:
        """The groups of the grouping chosen (characters or personas), the most recently updated first."""
        by = self.grouping.get()
        characters = {c.id: c for c in self.characters.store.list()}
        personas = {p.id: p for p in self.personas.store.list()}
        self._characters, self._personas = characters, personas

        grouped: dict[str, list[Conversation]] = defaultdict(list)
        for conversation in self.store.list():
            self._refresh_titles(conversation, characters, personas)
            grouped[getattr(conversation, by).id].append(conversation)

        section = self.characters if by == 'character' else self.personas
        profiles = characters if by == 'character' else personas
        groups = []
        for key, conversations in grouped.items():
            conversations.sort(key=lambda c: c.updated_at, reverse=True)
            profile, latest = profiles.get(key), conversations[0]
            row = Row(
                title=profile.title if profile else getattr(latest, by).title,
                image=section.image_url(profile.image) if profile else '',
                subtitle=f'{plural(len(conversations), "conversation")}, last updated {time_ago(latest.updated_at)}',
                warning='' if profile else f'This {by} was deleted',
            )
            if profile is not None:  # (its picture: its page, back here with the group open)
                self._link_picture(row, by, profile.id, back_to=key)
            groups.append(TreeGroup(key=key, items=conversations, row=row))
        groups.sort(key=lambda g: g.items[0].updated_at, reverse=True)
        return groups

    def _refresh_titles(self, conversation: Conversation, characters: dict, personas: dict) -> None:
        """Keep the remembered titles up to date while the character and persona exist."""
        changed = False
        for ref, found in ((conversation.character, characters.get(conversation.character.id)),
                           (conversation.persona, personas.get(conversation.persona.id))):
            if found and found.title != ref.title:
                ref.title, changed = found.title, True
        if changed:
            self.store.save(conversation)

    def _link_picture(self, row: Row, whose: str, profile_id: str, *, back_to: str) -> None:
        """The row's picture, a character's (`whose`) or a persona's: their page (a character's, to look at; a
        persona's, its editor), whose back arrow comes back to this page with the group `back_to` open."""
        grouping = self.grouping.get()
        back = quote(f'{self.path}?{grouping}={back_to}', safe='')
        page = f'/characters/{profile_id}' if whose == 'character' else f'/personas/{profile_id}'
        row.thumb_link = lambda: ui.navigate.to(f'{page}?back={back}')
        row.thumb_link_label = 'Character description' if whose == 'character' else 'Persona'

    def _row(self, conversation: Conversation, *, by: str = 'character', linked: bool = False) -> Row:
        """A conversation in a group of characters (`by`): with your persona's picture, "As <persona>"; in a group
        of personas, with the character's, "With <character>". `linked`: the picture opens its owner's page (see
        _link_picture)."""
        if by == 'persona':
            ref, profile, section, words = (conversation.character, self._characters.get(conversation.character.id),
                                            self.characters, ('With', 'Character'))
        else:
            ref, profile, section, words = (conversation.persona, self._personas.get(conversation.persona.id),
                                            self.personas, ('As', 'Persona'))
        row = Row(
            title=conversation.title,
            subtitle=f'{words[0]} {ref.title}. Updated {time_ago(conversation.updated_at)}.',
            thumb=True,
            thumb_label=ref.title,  # their initials when there's no picture
            image=section.image_url(profile.image) if profile else '',
            warning='' if profile else f'{words[1]} "{ref.title}" is missing',
        )
        if linked and profile is not None:
            self._link_picture(row, 'character' if by == 'persona' else 'persona', profile.id,
                               back_to=getattr(conversation, by).id)
        return row

    # -- the chat ----------------------------------------------------------------------

    def _conversation(self, conversation_id: str) -> Conversation | None:
        try:
            conversation = self.store.get(conversation_id)
        except ValueError:
            conversation = None
        if conversation is None:
            not_found('Conversation', self.path)
        return conversation

    def memories_page(self, conversation_id: str) -> None:
        if conversation := self._conversation(conversation_id):
            character = self.characters.store.get(conversation.character.id)
            name = (character.name or character.title) if character else conversation.character.title
            MemoriesPage(conversation, store=self.store, replies=self.replies, char_name=name).build()

    def chat_page(self, conversation_id: str) -> None:
        conversation = self._conversation(conversation_id)
        if conversation is None:
            return
        ChatPage(conversation, store=self.store, characters=self.characters, personas=self.personas,
                 replies=self.replies,
                 back=f'{self.path}?character={conversation.character.id}&persona={conversation.persona.id}').build()
