import os
import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli
        return cli()
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        from .cli import main as cli
        return cli(["--help"])
    from .gui import main as gui
    return gui()


if __name__ == "__main__":
    sys.exit(main())
