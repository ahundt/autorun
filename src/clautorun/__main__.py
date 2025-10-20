#!/usr/bin/env python3
"""Main module entry point for clautorun package"""
import sys
from .install import main as install_main
from .main import main as app_main


def main():
    """Main entry point for python -m clautorun"""
    if len(sys.argv) > 1 and sys.argv[1] in ["install", "uninstall", "check"]:
        # Remove the script name from argv before passing to install_main
        sys.argv = ["clautorun"] + sys.argv[1:]
        # Delegate to installation module
        install_main()
    else:
        # Run the main application
        app_main()


if __name__ == "__main__":
    main()