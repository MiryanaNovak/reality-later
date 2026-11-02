"""Make the starter set (the examples a new install starts with) from a data folder.

    python tools/freeze_starter.py examples-data

Write the examples with the app itself, in a data folder of their own (keep it out of git):

    RPAPP_DATA=examples-data python main.py

then freeze them into rpapp/starter/, check what changed (git diff rpapp/starter), and commit.
Their prompts, personas, characters, lore books and pictures go in, and which prompt chats use;
nothing else (not the connections, which hold API keys). See rpapp/starter.py.

    python tools/freeze_starter.py examples-data --to some/other/folder
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the project, when run from anywhere

from rpapp.starter import STARTER_DIR, freeze  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Make the starter set from a data folder.')
    parser.add_argument('source', type=Path, help='the data folder the examples were written in')
    parser.add_argument('--to', type=Path, default=STARTER_DIR, help=f'where the starter set goes ({STARTER_DIR})')
    args = parser.parse_args(argv)
    try:
        report = freeze(args.source, args.to)
    except ValueError as e:
        print(f'Not frozen: {e}', file=sys.stderr)
        return 1
    print(f'Frozen into {args.to}: {report.summary()}.')
    for folder, names in report.items.items():
        print(f'  {folder}: {", ".join(names)}')
    print(f'  chats use the prompt "{report.preset}"' if report.preset else '  no prompt chosen for chats')
    for warning in report.warnings:
        print(f'Warning: {warning}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
