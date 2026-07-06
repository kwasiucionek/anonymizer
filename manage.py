#!/usr/bin/env python3
"""Narzędzie linii poleceń Django dla Web UI anonimizatora."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Nie można zaimportować Django. Czy jest zainstalowane i czy "
            "aktywowałeś wirtualne środowisko?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
