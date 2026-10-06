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
import logging
import os
from pathlib import Path
import sys
from typing import Annotated

from cyclopts import App, Group, Parameter, validators
from cyclopts.types import Directory, PositiveInt
from . import USER_AGENT
from . import __version__ as WIKIGET_VERSION
from .dl import Downloader
from .logging import configure_logging


# FIXME: Fix the usage string, we don't have a COMMAND set
app = App(default_parameter=Parameter(short_alias=True), help_prologue=f"Wikiget v{WIKIGET_VERSION} (https://github.com/NiLuJe/wikiget)", version_flags=["--version", "-V"])
app.register_install_completion_command()

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

@app.default
def cli(
    input: Path | str,
    /,
    output: Directory | None = None,
    logfile: Path | None = None,
    quiet: Annotated[bool, Parameter(group=verbosity)] = False,
    verbose: Annotated[int, Parameter(group=verbosity, count=True)] = 0,
    force: Annotated[bool, Parameter(group=flags)] = False,
    batch: Annotated[bool, Parameter(group=flags)] = False,
    dry_run: Annotated[bool, Parameter(alias="-n", group=flags)] = False,
    concurrency: Annotated[PositiveInt, Parameter(alias="-j")] = 3,
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
    quiet:
        Suppress warning messages.
    verbose:
        Print detailed information;
        pass it twice for even more detail.
    force:
        Overwrite existing files in case of conflicts.
    batch:
        Treat input as a text file containing one entry per line,
        in the same format as input would otherwise expect.
    dry_run:
        Process the input it stop short of actually downloading anything.
    logfile:
        Path in which to store the log output.
    concurrency:
        Amount of downloads to start in parallel.
        You should probably not need to modify this,
        but if you do, check Wikimedia's current policies at
        https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
        first.
    """

    # TODO: Actually handle input being a str and not stdin?
    return 0

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
