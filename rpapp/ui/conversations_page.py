"""The Conversations section: a tree of conversations grouped by character, and the chat page.

    /conversations                     the tree; groups (characters) and their conversations,
                                       most recently updated first
    /conversations?character=<id>      the same, with that character's group open and in view
    /conversations/<conversation id>   the chat (see chat_page.py)
    /conversations/<conversation id>/memories   what the AI remembers of it (memories_page.py)

Conversations are started from a character's card (New chat), titled with the date and time. Their
⋮ menu, here and in the chat, can rename them.
"""
from __future__ import annotations

from collections import defaultdict

from nicegui import ui

from ..characters import Character
from ..conversations import TITLE_MAX, Conversation, ConversationStore
from ..personas import Persona
from ..replies import Replies
from .chat_page import ChatPage
from .memories_page import MemoriesPage
from .list_view import Action, Row, plural
from .profile_page import ProfileSection
from .tree_view import TreeGroup, TreeView
from .widgets import ask_name, confirm, not_found, page_bar, time_ago


class ConversationsSection:
    path = '/conversations'

    def __init__(self, store: ConversationStore, characters: ProfileSection[Character],
                 personas: ProfileSection[Persona], replies: Replies) -> None:
        self.store = store
        self.characters = characters
        self.personas = personas
        self.replies = replies
        self._personas: dict[str, Persona] = {}  # filled while building the groups, used by the rows

    # -- the tree --------------------------------------------------------------------

    def list_page(self, character: str = '') -> None:
        """`character` (from ?character=...) is the group to open and bring into view."""
        page_bar('Conversations')

        async def delete(conversation: Conversation) -> None:
            if await confirm(f'Delete "{conversation.title}"?', 'The whole chat is lost. This can\'t be undone.',
                             'Delete'):
                self.replies.stop(conversation.id)
                self.store.delete(conversation.id)
                ui.notify(f'Deleted "{conversation.title}"')
                tree.refresh()

        async def rename(conversation: Conversation) -> None:
            if name := await ask_name('Rename the conversation', conversation.title, max_length=TITLE_MAX):
                try:
                    self.store.rename(conversation.id, name)
                except KeyError:
                    ui.notify('This conversation was deleted meanwhile.', type='warning')
                tree.refresh()

        tree = TreeView(
            groups=self._groups,
            key=lambda conversation: conversation.id,
            row=self._row,
            on_open=lambda conversation: ui.navigate.to(f'{self.path}/{conversation.id}'),
            actions=[Action('Rename', rename), Action('Delete', delete, danger=True)],
            focus=character,
            empty_title='No conversations yet',
            empty_text='Start one from a character: open Characters and press New chat on a card.',
        )
        if character and not self.store.using('character', character):
            ui.notify('This character has no conversations yet.')

    def _groups(self) -> list[TreeGroup[Conversation]]:
        characters = {c.id: c for c in self.characters.store.list()}
        personas = {p.id: p for p in self.personas.store.list()}
        self._personas = personas

        by_character: dict[str, list[Conversation]] = defaultdict(list)
        for conversation in self.store.list():
            self._refresh_titles(conversation, characters, personas)
            by_character[conversation.character.id].append(conversation)

        groups = []
        for character_id, conversations in by_character.items():
            conversations.sort(key=lambda c: c.updated_at, reverse=True)
            character = characters.get(character_id)
            latest = conversations[0]
            groups.append(TreeGroup(key=character_id, items=conversations, row=Row(
                title=character.title if character else latest.character.title,
                image=self.characters.image_url(character.image) if character else '',
                subtitle=f'{plural(len(conversations), "conversation")}, last updated {time_ago(latest.updated_at)}',
                warning='' if character else 'This character was deleted',
            )))
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

    def _row(self, conversation: Conversation) -> Row:
        persona = self._personas.get(conversation.persona.id)
        return Row(
            title=conversation.title,
            subtitle=f'As {conversation.persona.title}. Updated {time_ago(conversation.updated_at)}.',
            thumb=True,
            thumb_label=conversation.persona.title,  # the persona's initials when it has no picture
            image=self.personas.image_url(persona.image) if persona else '',
            warning='' if persona else f'Persona "{conversation.persona.title}" is missing',
        )

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
                 replies=self.replies, back=f'{self.path}?character={conversation.character.id}').build()
