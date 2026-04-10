# PyInstaller entry point for the kamp bundle.
# kamp_daemon/__main__.py uses relative imports (.config, .daemon_core, etc.)
# which fail when the file is run directly as a script. This thin wrapper
# imports the package properly so relative imports resolve correctly.
from kamp_daemon.__main__ import main

if __name__ == "__main__":
    main()
