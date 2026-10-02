"""Start-up script that PyInstaller turns into the app (see notes2gdoc.spec)."""

import sys

from notes2gdoc.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
