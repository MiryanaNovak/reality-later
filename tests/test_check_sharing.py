"""`reality-later check-sharing` (rpapp/publishing/selfcheck.py): what the packaged programs' smoke test runs
to know that card sharing works there, its libraries and BitTorrent included."""
import pytest

from rpapp import cli

pytest.importorskip('coincurve', reason='coincurve (sharing cards) is installed for Python 3.13 and earlier')
pytest.importorskip('libtorrent', reason='libtorrent (sharing files) is installed for Python 3.13 and earlier')

from rpapp.publishing import selfcheck  # noqa: E402


def test_it_works_here(capsys):
    assert cli.run(['check-sharing'], None) == 0  # (no settings needed, nor the app running)
    said = capsys.readouterr().out
    for step in ('its libraries load: BitTorrent (libtorrent)', 'a card is published: signed, and sent to a relay',
                 'it\'s read back from the relay', 'its signature is checked (added)',
                 'its file is downloaded by BitTorrent, checked and added'):
        assert f'ok: {step}' in said
    assert said.rstrip().endswith('Card sharing works.') and 'FAILED' not in said


def test_a_library_missing(capsys, monkeypatch):
    monkeypatch.setattr('rpapp.publishing.seeder.torrent_problem', lambda: 'a library it needs is missing (libtorrent)')
    assert cli.run(['check-sharing'], None) == 1
    said = capsys.readouterr().out
    assert 'FAILED: its libraries load: BitTorrent (libtorrent): a library it needs is missing' in said
    assert 'Card sharing works.' not in said


def test_too_long(monkeypatch):
    async def forever(report):
        import asyncio
        await asyncio.sleep(10)
    monkeypatch.setattr(selfcheck, 'check', forever)
    monkeypatch.setattr(selfcheck, 'TIMEOUT', 0.1)
    said = []
    assert selfcheck.run(lambda ok, what: said.append((ok, what))) is False
    assert said == [(False, 'it took more than 0 seconds')]


def test_what_a_missing_library_is_said_to_be(monkeypatch):
    from rpapp import publishing
    assert publishing.missing_library(ImportError('No module named x')) == 'a library it needs is missing (No module named x)'
    monkeypatch.setattr(publishing.sys, 'version_info', (3, 14, 0))  # (none of them made for it yet)
    assert publishing.missing_library(ImportError('x')).startswith('it needs Python 3.13 or earlier, for now')
    assert publishing.sentence('a library it needs is missing (x)') == 'A library it needs is missing (x).'
    assert publishing.sentence('') == ''
