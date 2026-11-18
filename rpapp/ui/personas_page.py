"""The Personas section, built on the shared profile page (profile_page.py).

    /personas               the folders (folders_page.py), each folding open onto its personas' cards,
                            then the personas in none; New folder, New, and a search through them all
    /personas/<persona id>  one persona: picture, identity, description, lore books; each part says
                            whether the AI gets it

Folders are only for keeping personas in order: the AI never knows of them. A folder's ⋮ menu renames or
deletes it (its personas stay, in no folder); a persona's puts it in a folder, or takes it out. The new
chat's persona picker (folders_page.folder_links) shows them the same way.
"""
from __future__ import annotations

from nicegui import ui

from ..conversations import ConversationStore
from ..lorebooks import LoreBook
from ..folders import FolderStore
from ..personas import GENDERS, PRONOUNS, Persona, PersonaStore, new_persona
from ..storage import JsonStore
from .form import Form
from .profile_page import ProfileSection
from .widgets import audience_legend, panel

PRONOUN_LABELS = {key: label for key, (label, _) in PRONOUNS.items()}


class PersonasSection(ProfileSection[Persona]):
    title_hint = 'Only for you: how the persona is listed in this app'
    name_hint = 'Sent to the AI: what it calls you in a chat. {{user}} stands for it.'
    lore_books_caption = ('Background the AI gets only when it matters: an entry is sent when one of its keywords '
                          'comes up in a chat where you use this persona (or every time, for entries set so).')
    lore_books_audience = 'ai'

    def __init__(self, store: PersonaStore, lore_books: JsonStore[LoreBook],
                 conversations: ConversationStore | None = None, *,
                 folders: FolderStore | None = None) -> None:
        super().__init__(
            store, lore_books, title='Personas', noun='persona', path='/personas', create=new_persona,
            title_placeholder='e.g. Mira, wandering healer',
            empty_text='A persona is who you are in a chat: a name, pronouns, a description telling the AI '
                       'who you are, and a picture.',
            conversations=conversations, conversation_field='persona',
        )
        self.folders = folders or FolderStore(store.folder.parent, 'personas', exists=store.exists)

    def card_subtitle(self, persona: Persona) -> str:
        pronouns = PRONOUN_LABELS.get(persona.pronouns, persona.pronouns).lower() if persona.pronouns else ''
        return ', '.join(part for part in (persona.name, pronouns) if part)

    def intro(self, persona: Persona) -> None:
        audience_legend('Name, Gender, Pronouns, Description, Lore books', 'Title, Picture')

    def identity_fields(self, persona: Persona, form: Form) -> None:
        with ui.element('div').classes('field-grid'):
            form.choice('Gender', GENDERS, get=lambda: persona.gender,
                        set=lambda value: setattr(persona, 'gender', value or ''))
            form.choice('Pronouns', PRONOUN_LABELS, get=lambda: persona.pronouns,
                        set=lambda value: setattr(persona, 'pronouns', value or ''))
        ui.label('Sent to the AI: so that it writes about you the right way. In texts, {{sub}}, {{obj}}, {{poss}}, '
                 '{{poss_p}} and {{ref}} stand for your pronouns (she, her, her, hers, herself; they/them when none '
                 'is chosen).').classes('text-xs muted -mt-2 px-3').mark('gender-pronouns-hint')

    def panels(self, persona: Persona, form: Form) -> None:
        with panel('Description', 'Who you are in the story: looks, background, personality, abilities. The AI reads '
                                  'it before every reply, so that it knows who it\'s talking to. Write {{user}} '
                                  'for your name and {{char}} for the character\'s; {{sub}}, {{obj}}, {{poss}}, '
                                  '{{poss_p}} and {{ref}} for your pronouns.', audience='ai'):
            form.long_text(persona, 'description', 'Description', min_height='12rem',
                           placeholder='e.g. {{user}} is a wandering healer in her thirties, calm and stubborn, '
                                       'with a satchel of herbs and a scar across one hand.')
