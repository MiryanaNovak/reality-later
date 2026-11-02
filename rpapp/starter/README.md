# The starter set

The examples a new install starts with: prompts, personas, characters and lore books, in the same
files as a data folder. On the very first start, the app copies them into its data folder (and
never again: they're then ordinary data, to change or delete).

Don't edit these files by hand. Write the examples with the app, in a data folder of their own
(keep it out of git), then freeze them here:

    RPAPP_DATA=examples-data python main.py
    python tools/freeze_starter.py examples-data
    git diff rpapp/starter

Only the examples and their pictures come here, and which prompt chats use: never the
connections (they hold API keys), conversations or paired devices. See rpapp/starter.py.
