# PyInstaller entry point for the kamp bundle.
# kamp_daemon/__main__.py uses relative imports (.config, .daemon_core, etc.)
# which fail when the file is run directly as a script. This thin wrapper
# imports the package properly so relative imports resolve correctly.

# freeze_support() must be called before any other code when the frozen binary
# is re-invoked as a multiprocessing worker (e.g. resource_tracker). Without
# it the worker arguments ("from multiprocessing.resource_tracker import ...")
# fall through to argparse and produce "invalid choice" errors.
import multiprocessing

multiprocessing.freeze_support()

from kamp_daemon.__main__ import main

if __name__ == "__main__":
    main()
