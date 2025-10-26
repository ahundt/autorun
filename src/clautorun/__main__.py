#!/usr/bin/env python3

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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