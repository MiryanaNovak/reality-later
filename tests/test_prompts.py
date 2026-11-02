"""Building prompts: the order of the parts, lore placement, fitting the context, and how it's sent."""
from rpapp.characters import new_character
from rpapp.connections import OpenRouterConnection
from rpapp.conversations import Conversation, ProfileRef
from rpapp.lorebooks import lore_book_store, new_lore_book
from rpapp.personas import new_persona
from rpapp.profiles import LoreBookRef
from rpapp.prompts import Prompt, PromptPart, build_prompt, first_kept, lore_books_for, to_messages
from rpapp.settings import Settings


def cast():
    character = new_character('Seraphina')
    character.scenario = 'Guardian of the glade.'
    character.description = 'Only for the user.'  # never sent
    persona = new_persona('Mira')
    return character, persona


def chat(*texts: str, greeting: bool = True) -> Conversation:
    """A conversation alternating Seraphina / Mira (Seraphina first, if `greeting`)."""
    character, persona = cast()
    c = Conversation(title='t', character=ProfileRef.to(character), persona=ProfileRef.to(persona))
    for i, text in enumerate(texts):
        c.add('assistant' if (i % 2 == 0) == greeting else 'user', text)
    return c


def test_lore_placement():
    character, persona = cast()
    book = new_lore_book('Eldoria')
    book.add_entry(comment='Forest', key=['forest'], content='The forest is old.', position=1)
    book.add_entry(comment='Rule', constant=True, content='Magic has a price.', position=0, order=50)
    book.add_entry(comment='Mood', key=['forest'], content='[It is getting dark.]', position=4, depth=1, role=0)
    book.add_entry(comment='Voice', key=['forest'], content='*rustling*', position=4, depth=0, role=2)
    c = chat('Welcome.', 'Is this forest dangerous?')

    prompt = build_prompt(c, character=character, persona=persona, settings=Settings(), books=[book])
    assert [(p.kind, p.source) for p in prompt.parts] == [
        ('scenario', 'Scenario'),
        ('lore', 'Lore: Eldoria › Rule'),     # before the character: first of the lore
        ('lore', 'Lore: Eldoria › Forest'),   # after the character
        ('start', 'Placeholder: some models need the chat to start with you'),
        ('message', 'Seraphina'),
        ('lore', 'Lore: Eldoria › Mood'),     # depth 1: before the last message
        ('message', 'Mira'),
        ('lore', 'Lore: Eldoria › Voice'),    # depth 0: after it, as the assistant
    ]
    assert prompt.parts[-1].role == 'assistant'
    report = prompt.report
    assert report.books == ['Eldoria'] and len(report.lore) == 4
    placements = {u.entry: u.placement for u in report.lore}
    assert placements == {
        'Rule': 'with the background', 'Forest': 'with the background',
        'Mood': 'in the chat, 1 message from the end, as system',
        'Voice': 'in the chat, after the last message, as assistant',
    }

    # Sent: the system parts at the top make one message; the depth-1 system part stays apart.
    assert [m.role for m in prompt.messages()] == ['system', 'user', 'assistant', 'system', 'user', 'assistant']
    assert prompt.messages()[0].content == 'About Seraphina and the story:\nGuardian of the glade.\n\n' \
                                           'Magic has a price.\n\nThe forest is old.'


def test_sending_system_parts_as_user():
    parts = [PromptPart(role='system', content='Rules.'), PromptPart(role='system', content='About.'),
             PromptPart(role='user', content='Hi.'), PromptPart(role='system', content='Be brief.')]
    assert [(m.role, m.content) for m in to_messages(parts)] == [
        ('system', 'Rules.\n\nAbout.'), ('user', 'Hi.'), ('system', 'Be brief.')]
    assert [(m.role, m.content) for m in to_messages(parts, 'user')] == [
        ('system', 'Rules.\n\nAbout.'), ('user', 'Hi.\n\nBe brief.')]  # inside the chat: joins your message


def test_space_and_connection():
    character, persona = cast()
    conn = OpenRouterConnection(name='DeepSeek', model='deepseek/deepseek-chat', context_window=64_000,
                                system_in_chat='user')
    conn.params.max_tokens = 600
    prompt = build_prompt(chat('Hello.', 'Hi.'), character=character, persona=persona, settings=Settings(),
                          connection=conn)
    r = prompt.report
    assert (r.context_size, r.context_known, r.reply_tokens, r.reply_known) == (64_000, True, 600, True)
    assert r.available == 64_000 - 600 - 6_400
    assert r.lore_budget == r.available // 4
    assert prompt.connection == 'DeepSeek (deepseek/deepseek-chat)' and prompt.system_in_chat == 'user'

    r = build_prompt(chat('Hello.'), character=character, persona=persona, settings=Settings()).report
    assert (r.context_size, r.context_known, r.reply_tokens, r.reply_known) == (16_384, False, 1_024, False)


def test_trimming_moves_in_steps():
    # 10 tokens a message, room for 100: a step is 20 tokens (2 messages).
    assert first_kept([10] * 10, 100) == 0
    assert first_kept([10] * 11, 100) == 2   # 10 over: leave out a whole step
    assert first_kept([10] * 12, 100) == 2   # still the same cut
    assert first_kept([10] * 13, 100) == 4   # the next step
    assert first_kept([500, 10], 100) == 1
    assert first_kept([10, 500], 100) == 1   # the last message always goes in


def test_long_chat_is_trimmed():
    character, persona = cast()
    conn = OpenRouterConnection(name='Tiny', model='m', context_window=2_000)
    conn.params.max_tokens = 200
    texts = [f'Message {i}. ' + 'blah ' * 40 for i in range(40)]  # about 64 tokens each
    c = chat(*texts, greeting=False)  # Mira starts
    prompt = build_prompt(c, character=character, persona=persona, settings=Settings(), connection=conn)
    r = prompt.report
    assert r.messages_total == 40 and 0 < r.messages_left_out < 40 and not r.over_limit
    kept = [p for p in prompt.parts if p.kind == 'message']
    assert len(kept) == 40 - r.messages_left_out and kept[-1].content == texts[-1]
    assert r.tokens <= r.available

    # The same chat, Seraphina starting: one of the two now opens with her after the cut,
    # and gets the placeholder, which says messages are left out.
    other = build_prompt(chat(*texts, greeting=True), character=character, persona=persona, settings=Settings(),
                         connection=conn)
    for p in (prompt, other):
        chat_parts = [part for part in p.parts if part.kind in ('start', 'message')]
        if chat_parts[0].kind == 'start':
            assert chat_parts[0].content == '[Earlier messages are left out]' and chat_parts[1].role == 'assistant'
        else:
            assert chat_parts[0].role == 'user'
    assert {prompt.parts[1].kind, other.parts[1].kind} == {'start', 'message'}


def test_lore_books_for(tmp_path):
    store = lore_book_store(tmp_path)
    eldoria, shared = new_lore_book('Eldoria'), new_lore_book('Shared')
    store.save(eldoria)
    store.save(shared)
    character, persona = cast()
    character.lorebooks = [LoreBookRef(id=eldoria.id, name='Eldoria'), LoreBookRef(id=shared.id, name='Shared')]
    persona.lorebooks = [LoreBookRef(id=shared.id, name='Shared'), LoreBookRef(id='gone-123456', name='Old notes')]
    books, missing = lore_books_for(character, persona, store)
    assert [b.name for b in books] == ['Eldoria', 'Shared'] and missing == ['Old notes']
    assert lore_books_for(None, None, store) == ([], [])


def test_first_version_prompts_still_load():
    old = Prompt.model_validate({
        'id': 'abc', 'format_version': 1, 'created_at': '2026-10-30T21:50:12Z', 'connection': '',
        'messages': [{'role': 'system', 'content': 'You are Seraphina.'}, {'role': 'user', 'content': 'Hi.'}],
    })
    assert [(p.role, p.content, p.source) for p in old.parts] == [
        ('system', 'You are Seraphina.', ''), ('user', 'Hi.', '')]
    assert old.report is None and [m.role for m in old.messages()] == ['system', 'user']


# --- example dialogues ---------------------------------------------------------------------------

def test_example_dialogues_and_the_lore_around_them():
    character, persona = cast()
    character.example_dialogues = ['{{user}}: Who are you?\n{{char}}: The guardian.', '  ', '{{char}}: Rest now.']
    book = new_lore_book('Eldoria')
    book.add_entry(comment='Before', constant=True, content='Speak softly.', position=5)
    book.add_entry(comment='After', constant=True, content='Never break character.', position=6)
    book.add_entry(comment='Rule', constant=True, content='Magic has a price.', position=0)
    prompt = build_prompt(chat('Welcome.', 'Hello.'), character=character, persona=persona, settings=Settings(),
                          books=[book])
    assert [(p.kind, p.source) for p in prompt.parts][:5] == [
        ('scenario', 'Scenario'), ('lore', 'Lore: Eldoria › Rule'),
        ('lore', 'Lore: Eldoria › Before'), ('examples', 'Example dialogues'), ('lore', 'Lore: Eldoria › After')]
    examples = prompt.parts[3].content
    assert examples == ('Examples of how Seraphina speaks and acts. They show the style only: they aren\'t part '
                        'of the story.\n\n[Example 1]\nMira: Who are you?\nSeraphina: The guardian.\n\n'
                        '[Example 2]\nSeraphina: Rest now.')  # the empty one is skipped
    r = prompt.report
    assert (r.examples_total, r.examples_sent, r.examples_tokens) == (2, 2, prompt.parts[3].tokens)
    placements = {u.entry: u.placement for u in r.lore}
    assert placements['Before'] == 'before the example dialogues'
    assert placements['After'] == 'after the example dialogues'

    character.example_dialogues = ['{{char}}: Rest now.']  # just one: no numbering
    one = build_prompt(chat('Welcome.'), character=character, persona=persona, settings=Settings())
    assert one.parts[1].content.endswith('aren\'t part of the story.\n\nSeraphina: Rest now.')


def test_example_dialogues_make_room_as_the_chat_grows():
    character, persona = cast()
    character.example_dialogues = [f'{{{{char}}}}: Example {i}. ' + 'words ' * 60 for i in range(3)]  # ~110 each
    conn = OpenRouterConnection(name='Tiny', model='m', context_window=1_500)
    conn.params.max_tokens = 200
    text = 'Message. ' + 'blah ' * 40  # about 64 tokens each

    def sent(length: int, settings=None):
        r = build_prompt(chat(*[text] * length), character=character, persona=persona,
                         settings=settings or Settings(), connection=conn).report
        return r.examples_sent, r.messages_left_out

    progress = [sent(n) for n in range(0, 30)]
    counts = [examples for examples, _ in progress]
    assert counts[0] == 3 and counts[-1] == 0
    assert counts == sorted(counts, reverse=True)  # they leave for good, never coming back as the chat grows
    assert {left_out for examples, left_out in progress if examples} == {0}  # only while the whole chat fits
    assert any(0 < c < 3 for c in counts)  # one at a time: the last ones first

    always = Settings()
    always.chat.example_dialogues = 'always'
    assert sent(29, always)[0] == 3 and sent(29, always)[1] > sent(29)[1]  # the chat makes room instead
    # When none are sent, lore placed around them stays with the background, and says so.
    book = new_lore_book('Eldoria')
    book.add_entry(comment='Before', constant=True, content='Speak softly.', position=5)
    r = build_prompt(chat(*[text] * 29), character=character, persona=persona, settings=Settings(),
                     connection=conn, books=[book]).report
    assert r.examples_sent == 0 and r.lore[0].placement == 'with the background (no example dialogues are sent)'
