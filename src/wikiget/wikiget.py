# wikiget - CLI tool for downloading files from Wikimedia sites
# Copyright (C) 2018-2023 Cody Logan and contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Wikiget is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Wikiget is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Wikiget. If not, see <https://www.gnu.org/licenses/>.

"""Set up the command-line interface and handle program start and exit."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import sys
from typing import Annotated, Final

from cyclopts import App, Group, Parameter, config, validators
from cyclopts.types import Directory, PositiveInt
from rich.console import Console
from rich.traceback import install as install_rich_traceback

from . import USER_AGENT
from . import __version__ as WIKIGET_VERSION
from .dl import Downloader
from .logging import configure_logging

error_console = Console(stderr=True)
app = App(console=Console(), error_console=error_console, default_parameter=Parameter(short_alias=True), help_prologue=f"Wikiget v{WIKIGET_VERSION} (https://github.com/NiLuJe/wikiget)", version_flags=["--version", "-V"], config=config.Toml("config.toml", use_commands_as_keys=False))
# NOTE: This adds a seemingly-required COMMAND placeholder in the USAGE string :/
# app.register_install_completion_command()

# Install verbose rich traceback handler using the error console
install_rich_traceback(console=error_console, show_locals=True)

def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the given argument list.

    :param argv: a list of arguments in string form
    :type argv: list[str]
    :return: a Namespace containing the arguments and their values
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(
        description="""
        A tool for downloading files from MediaWiki sites using the file name or
        description page URL.
        """,
        epilog="""
        Copyright (C) 2018-2023 Cody Logan and contributors. License GPLv3+: GNU GPL
        version 3 or later <http://www.gnu.org/licenses/gpl.html>. This is free
        software; you are free to change and redistribute it under certain conditions.
        There is NO WARRANTY, to the extent permitted by law.
        """,
        prog="wikiget",
    )
    parser.add_argument(
        "FILE",
        help="""
        name of the file to download, with the File: prefix, or the URL of its file
        description page
        """,
    )
    message_options = parser.add_mutually_exclusive_group()
    message_options.add_argument("-q", "--quiet", help="suppress warning messages", action="store_true")
    message_options.add_argument(
        "-v",
        "--verbose",
        help="print detailed information; use -vv for even more detail",
        action="count",
        default=0,
    )
    parser.add_argument("-f", "--force", help="force overwriting existing files", action="store_true")
    output_options = parser.add_mutually_exclusive_group()
    output_options.add_argument("-o", "--output", help="write download to OUTPUT")
    output_options.add_argument(
        "-a",
        "--batch",
        help="treat FILE as a textfile containing multiple files to download, one URL or filename per line",
        action="store_true",
    )
    parser.add_argument("-l", "--logfile", default="", help="save log output to LOGFILE")
    parser.add_argument(
        "-j",
        "--threads",
        default=1,
        help="number of parallel downloads to attempt in batch mode",
        type=int,
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        help="check the download or batch file without actually downloading anything",
        action="store_true",
    )

    return parser.parse_args(argv)

verbosity = Group(
    "Verbosity",
    default_parameter=Parameter(negative="", show_default=False),
    validator=validators.MutuallyExclusive(),
)

# No negatives, and no default values shown for store_true flags
flags = Group(
    default_parameter=Parameter(negative="", show_default=False),
)

# Flatten parameters in a single object we can pass around
@Parameter(name="*")
@dataclass
class Config:
    "Bundle of CLI config choices"

    logfile: Path | None = None
    "Path in which to store the log output."
    quiet: Annotated[bool, Parameter(group=verbosity)] = False
    "Suppress warning messages."
    verbose: Annotated[int, Parameter(group=verbosity, count=True)] = 0
    "Print detailed information; pass it twice for even more detail."
    force: Annotated[bool, Parameter(group=flags)] = False
    "Overwrite existing files in case of conflicts."
    batch: Annotated[bool, Parameter(group=flags)] = False
    "Treat INPUT as a text file containing one entry per line, in the same format as input would otherwise expect."
    dry_run: Annotated[bool, Parameter(alias="-n", group=flags)] = False
    "Process the input but stop short of actually downloading anything."
    concurrency: Annotated[PositiveInt, Parameter(alias="-j")] = 3
    "Amount of downloads to start in parallel. Check Wikimedia's current policies at https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits before raising this."

@app.default
def cli(
    input: Path | str,
    /,
    output: Directory | None = None,
    *,
    config: Config | None = None,
) -> int:
    """Download files from **Wikimedia Commons**

    **CLI** Help:

    Parameters
    ----------
    input:
        Name of the file to download, with the File: prefix;
        or the URL of its file description page.
    output:
        Path in which to store the downloaded files.
    """

    # TODO: Actually handle input being a str and not stdin?
    return 0

    if config is None:
        config = Config()

    configure_logging(verbosity=args.verbose, logfile=args.logfile, quiet=args.quiet)

    logger = logging.getLogger(__name__)

    # Sanity check args.FILE
    if args.FILE != "-" and not (os.path.isfile(args.FILE) and os.access(args.FILE, os.R_OK)):
        logger.critical("Cannot access input file `%s`!", args.FILE)
        return 1

    # log events are appended to the file if it already exists, so note the start of a
    # new download session
    logger.info("Starting download session using wikiget %s", WIKIGET_VERSION)
    logger.debug("User agent: %s", USER_AGENT)

    dl = Downloader(args)
    return dl.process_download()
