"""The app's version, as rpapp/__init__.py writes it (its __version__: the only place it's written).

    python packaging/check_version.py           # prints it: 0.2.0
    python packaging/check_version.py v0.2.0    # checks that a release's tag is v + it (fails otherwise)

The release workflows check the tag before building: the app compares its own version with the latest
release's tag to tell its users when a newer one is out (rpapp/updates.py), so a release tagged v0.3.0 that
calls itself 0.2.0 would tell them to update to itself, forever. Only Python's own modules: it runs with
any Python, before anything is installed.
"""
import ast
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / 'rpapp' / '__init__.py'


def app_version(init: Path = INIT) -> str:
    for node in ast.parse(init.read_text(encoding='utf-8')).body:
        names = [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else []
        if '__version__' in names:
            return ast.literal_eval(node.value)
    raise SystemExit(f'No __version__ in {init}')


def main(argv: list[str]) -> int:
    version = app_version()
    if not argv:
        print(version)
        return 0
    tag = argv[0]
    if tag != f'v{version}':
        print(f'The tag {tag} isn\'t this version of the app (v{version}, in rpapp/__init__.py). Either set '
              f'__version__ = \'{tag.removeprefix("v")}\' there and tag that commit, or tag this one v{version}.')
        return 1
    print(f'The tag {tag} is the app\'s version.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
