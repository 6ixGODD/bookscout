"""``package`` subcommand — Python namespace package management.

Exposes ``package_app`` to be registered on the parent Typer app.
"""

from __future__ import annotations

import typer

from scripts.package.build import build
from scripts.package.list import list_packages
from scripts.package.new import new
from scripts.package.remove import remove

package_app = typer.Typer(name="package", help="Python namespace package management")
package_app.command(name="build")(build)
package_app.command(name="list")(list_packages)
package_app.command(name="ls", hidden=True)(list_packages)
package_app.command(name="new")(new)
package_app.command(name="n", hidden=True)(new)
package_app.command(name="remove")(remove)
package_app.command(name="rm", hidden=True)(remove)

if __name__ == "__main__":
    package_app()
