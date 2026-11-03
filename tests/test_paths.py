"""Where the app keeps its files: next to main.py from the source folder, the user's folders installed."""
import sys
from pathlib import Path

from rpapp import paths
from rpapp.paths import example_config, in_flatpak, places, source_folder

HOME = Path.home()


def test_from_the_source_folder_everything_is_next_to_main_py():
    root = Path(__file__).resolve().parent.parent
    assert source_folder({}) == root  # (these tests run from it)
    here = places({})
    assert (here.config, here.data, here.installed) == (root, root / 'data', False)


def test_installed_on_linux():
    where = places({}, 'linux', source=None)
    assert where.config == HOME / '.config' / 'reality-later' and where.data == HOME / '.local' / 'share' / 'reality-later'
    flatpak = {'FLATPAK_ID': 'io.github.MiryanaNovak.RealityLater',
               'XDG_CONFIG_HOME': '/home/me/.var/app/io.github.MiryanaNovak.RealityLater/config',
               'XDG_DATA_HOME': '/home/me/.var/app/io.github.MiryanaNovak.RealityLater/data'}
    where = places(flatpak, 'linux')  # (a Flatpak is never "the source folder")
    assert where.installed
    assert where.config == Path('/home/me/.var/app/io.github.MiryanaNovak.RealityLater/config/reality-later')
    assert where.data == Path('/home/me/.var/app/io.github.MiryanaNovak.RealityLater/data/reality-later')


def test_installed_on_windows_and_macos():
    windows = places({'APPDATA': r'C:\Users\me\AppData\Roaming'}, 'win32', source=None)
    assert windows.config == Path(r'C:\Users\me\AppData\Roaming') / 'Reality Later'
    assert windows.data == windows.config / 'data'
    mac = places({}, 'darwin', source=None)
    assert mac.config == HOME / 'Library' / 'Application Support' / 'Reality Later' and mac.data == mac.config / 'data'


def test_rpapp_data_wins(tmp_path):
    for source in (True, None):
        where = places({'RPAPP_DATA': str(tmp_path)}, 'linux', source=source)
        assert where.data == tmp_path


def test_what_counts_as_installed(monkeypatch):
    assert in_flatpak({'FLATPAK_ID': 'x'}) and not in_flatpak({})
    assert source_folder({'FLATPAK_ID': 'x'}) is None
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert source_folder({}) is None  # a packaged program
    monkeypatch.delattr(sys, 'frozen')
    monkeypatch.setattr(paths, 'PACKAGE', Path('/usr/lib/python3/site-packages/rpapp'))
    assert source_folder({}) is None  # pip-installed: no main.py next to the package


def test_the_example_settings_file(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    assert example_config(tmp_path) == root / 'config.example.toml'  # (none next to config.toml: the source's)
    (tmp_path / 'config.example.toml').write_text('# mine')
    assert example_config(tmp_path) == tmp_path / 'config.example.toml'  # next to config.toml first
    monkeypatch.setattr(paths, 'source_folder', lambda env=None: None)
    monkeypatch.setattr(paths, 'FLATPAK_SHARE', tmp_path / 'share')
    monkeypatch.setenv('FLATPAK_ID', 'x')
    (tmp_path / 'share').mkdir()
    (tmp_path / 'share' / 'config.example.toml').write_text('# the Flatpak\'s')
    assert example_config(tmp_path / 'elsewhere') == tmp_path / 'share' / 'config.example.toml'
    monkeypatch.delenv('FLATPAK_ID')
    assert example_config(tmp_path / 'elsewhere') == tmp_path / 'elsewhere' / 'config.example.toml'  # (none)


def test_the_command_the_messages_give(monkeypatch):
    assert paths.command({}) == 'python main.py'  # (these tests run from the source folder)
    assert paths.command({'FLATPAK_ID': 'io.github.MiryanaNovak.RealityLater'}) == \
        'flatpak run io.github.MiryanaNovak.RealityLater'
    monkeypatch.setattr(paths, 'source_folder', lambda env=None: None)
    assert paths.command({}) == 'reality-later'  # pip install
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', r'C:\Users\nicolas\AppData\Local\Programs\Reality Later\Reality Later.exe')
    # The Windows program with a console, by its name alone (pages show it to other devices: no user name)
    assert paths.command({}, 'win32') == 'reality-later.exe'
