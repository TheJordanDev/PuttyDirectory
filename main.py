"""Entry point: ``python main.py``.

Everything lives in the ``puttydirectory`` package; this is just a launcher so
the app can be started without installing it.
"""

from puttydirectory.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
