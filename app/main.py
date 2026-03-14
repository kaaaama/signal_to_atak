"""Application entry point for the Signal-to-TAK bridge."""

from __future__ import annotations

from app.services.application import Application


def main() -> None:
    """Run the application from the module entry point.

    Keeping the entry point as a thin wrapper makes the startup sequence easy
    to reuse in tests or alternative launchers.
    """
    app = Application()
    app.run()


if __name__ == "__main__":
    main()
