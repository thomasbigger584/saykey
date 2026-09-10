"""Entry point:  python -m ui  [--tray]   (also runnable as a file path).

--tray is accepted for parity with the autostart command; the app always starts
in the tray and only shows the window when 'start hidden' is off.
"""

import sys
from pathlib import Path

# Make `import ui...` work when this file is run directly (autostart uses a
# bare file path, where the project root isn't on sys.path).
_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

from ui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
