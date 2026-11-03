"""The Windows program without a console: its messages in a log file, and a message box when it can't start."""
import sys

import pytest

from rpapp import launcher, windowless
from rpapp.config import Config


@pytest.fixture
def no_console(monkeypatch):
    """The message boxes shown (see run)."""
    monkeypatch.setattr(windowless, 'log_path', None)
    return []


def run(main, argv, folder, boxes):
    """windowless.run as in PyInstaller's windowed program: nowhere to print to. (Set here, in the test
    itself: pytest puts its own sys.stdout back between a fixture and the test.)"""
    before = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = None
    try:
        assert not windowless.has_console()
        windowless.run(main, argv, folder=folder, show_error=boxes.append)
    finally:
        log, (sys.stdout, sys.stderr) = sys.stdout, before
        if log is not None:
            log.close()


def test_without_a_console_it_writes_to_the_log(tmp_path, no_console):
    run(lambda argv: print('Reality Later is running.'), [], tmp_path / 'Reality Later', no_console)
    log = (tmp_path / 'Reality Later' / windowless.LOG_NAME).read_text()
    assert ': started\n' in log and 'Reality Later is running.' in log
    assert windowless.log_path == tmp_path / 'Reality Later' / windowless.LOG_NAME
    assert no_console == []  # all went well: no message box


def test_each_start_adds_to_it(tmp_path, no_console):
    (tmp_path / windowless.LOG_NAME).write_text('the running copy\'s messages\n')
    run(lambda argv: print('Reality Later is already running.'), ['--no-browser'], tmp_path, no_console)
    log = (tmp_path / windowless.LOG_NAME).read_text()
    assert log.startswith('the running copy\'s messages\n')  # (a second start must not erase it)
    assert ': --no-browser\n' in log and log.endswith('Reality Later is already running.\n')


def test_a_big_log_is_put_aside(tmp_path, no_console, monkeypatch):
    monkeypatch.setattr(windowless, 'MAX_LOG', 10)
    (tmp_path / windowless.LOG_NAME).write_text('older messages, many of them\n')
    run(lambda argv: print('new'), [], tmp_path, no_console)
    assert (tmp_path / windowless.OLD_LOG_NAME).read_text() == 'older messages, many of them\n'
    assert 'older' not in (tmp_path / windowless.LOG_NAME).read_text()


def test_a_start_that_fails_says_why(tmp_path, no_console):
    def main(argv):
        sys.exit('Can\'t start: port 8090 is already used by another program.')
    with pytest.raises(SystemExit) as stopped:
        run(main, [], tmp_path, no_console)
    assert stopped.value.code == 1
    assert no_console == ['Can\'t start: port 8090 is already used by another program.']
    assert 'port 8090 is already used' in (tmp_path / windowless.LOG_NAME).read_text()


def test_with_only_an_exit_code_the_box_shows_what_it_printed(tmp_path, no_console):
    (tmp_path / windowless.LOG_NAME).write_text('an earlier start\'s messages\n')

    def main(argv):
        print('Reality Later is already running.')
        print('It doesn\'t answer (process 1234). If it\'s stuck, stop it (Task Manager on Windows...)')
        sys.exit(1)
    with pytest.raises(SystemExit):
        run(main, [], tmp_path, no_console)
    [box] = no_console
    assert box.startswith('Reality Later couldn\'t start.\n\nReality Later is already running.\nIt doesn\'t answer')
    assert 'earlier start' not in box


def test_an_error_says_so_and_where_the_details_are(tmp_path, no_console):
    def main(argv):
        raise ValueError('something unexpected')
    with pytest.raises(SystemExit):
        run(main, [], tmp_path, no_console)
    [box] = no_console
    assert 'ValueError: something unexpected' in box and str(tmp_path / windowless.LOG_NAME) in box
    log = (tmp_path / windowless.LOG_NAME).read_text()
    assert 'Traceback (most recent call last)' in log and 'ValueError: something unexpected' in log


def test_quitting_normally_shows_nothing(tmp_path, no_console):
    with pytest.raises(SystemExit) as stopped:
        run(lambda argv: sys.exit(0), [], tmp_path, no_console)
    assert stopped.value.code == 0 and no_console == []


def test_the_tray_icons_process_shows_no_box(tmp_path, no_console):
    with pytest.raises(SystemExit):
        run(lambda argv: sys.exit('No tray icon: Reality Later doesn\'t answer.'), ['tray', 'data'], tmp_path,
            no_console)
    assert no_console == []  # (in the log only: the app itself is what matters)


def test_with_a_console_it_says_to_leave_it_open(tmp_path, monkeypatch):
    info = launcher.ServerInfo(Config(folder=tmp_path), None, tmp_path / 'data')
    assert 'Leave this window open' in launcher.ready_lines(info, opening=False)[0]
    monkeypatch.setattr(windowless, 'log_path', tmp_path / windowless.LOG_NAME)
    assert launcher.ready_lines(info, opening=False)[0] == (
        'Reality Later is running, without a window: quit it from its tray icon\'s menu, or its "This computer" '
        'page.')
