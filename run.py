"""PyInstaller / dev entry script. Prefer `python -m ducktype` when developing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ducktype.app import main  # noqa: E402

if __name__ == "__main__":
    main()
