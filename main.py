"""Start the app from its source folder:

    python main.py               (python main.py --help: its options and commands)

The app itself starts in rpapp/main.py, which `reality-later` runs too once the app is installed.
Running from here, its settings file (config.toml) and data folder (data/) are next to this file.
"""
import sys
from pathlib import Path

if __name__ in {'__main__', '__mp_main__'}:  # (__mp_main__: the process NiceGUI starts with --reload)
    from rpapp.main import main
    main(sys.argv[1:], first=__name__ == '__main__', script=Path(__file__).resolve())
