"""Console entry point."""

from __future__ import annotations

import sys

from openmediadl.application import run_application


def main() -> int:
    return run_application()


if __name__ == "__main__":
    sys.exit(main())
