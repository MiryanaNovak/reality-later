"""The Windows program's starting point (see reality-later.spec): the app, as `reality-later` runs it.

Both programs start here: "Reality Later.exe", without a console (its messages go to a log file, and
a start that fails says why in a message box: see rpapp/windowless.py), and reality-later.exe, with
one (for the commands, and to watch the messages).
"""
import multiprocessing
import sys

if __name__ == '__main__':
    # NiceGUI's process pool starts this program again for its workers: this makes those runs workers,
    # not a second copy of the app.
    multiprocessing.freeze_support()

    from rpapp import windowless
    from rpapp.main import main
    from rpapp.paths import places

    if windowless.has_console():
        main(sys.argv[1:])
    else:
        windowless.run(main, sys.argv[1:], folder=places().config)
