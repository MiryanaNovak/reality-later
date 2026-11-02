"""Quick manual check: save an OpenRouter connection, reload it, stream a reply.

    pip install nicegui          # brings pydantic and httpx along
    export OPENROUTER_API_KEY=sk-or-...
    export OPENROUTER_MODEL=some/model-id   # optional, copy an id from openrouter.ai/models
    python try_connection.py
"""
import asyncio
import os

from rpapp.connections import GenerationParams, Message, ModelError, OpenRouterConnection, connection_store


async def main() -> None:
    store = connection_store('data')

    conn = OpenRouterConnection(
        name='OpenRouter test',
        api_key=os.environ['OPENROUTER_API_KEY'],
        model=os.environ.get('OPENROUTER_MODEL', 'openrouter/auto'),
        params=GenerationParams(temperature=0.9, max_tokens=300),
    )
    conn.provider.data_collection = 'deny'
    path = store.save(conn)
    print(f'Saved to {path}')

    conn = store.load(conn.id)  # comes back as an OpenRouterConnection
    print(conn)                 # note: no API key in the output

    messages = [
        Message(role='system', content='You are Mira, a sarcastic tavern keeper in a fantasy town.'),
        Message(role='user', content='*walks in, soaked from the rain* Got a room for the night?'),
    ]
    try:
        async for text in conn.stream(messages):
            print(text, end='', flush=True)
        print()
    except ModelError as e:
        print(f'\nError: {e}')


asyncio.run(main())
