"""PyInstaller entry point.

Importing the GUI as a package member (``djmigrationkit.gui.app``) rather than running
``app.py`` directly is essential: a frozen script run as ``__main__`` has no parent
package, so app.py's ``from ..matcher import ...`` relative imports would fail with
"attempted relative import with no known parent package". Going through this
launcher keeps the package structure intact.
"""

from djmigrationkit.gui.app import main

if __name__ == "__main__":
    main()
