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

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Annotated

from cyclopts import App, Group, Parameter, config, validators
from cyclopts.types import Directory, PositiveInt, StdioPath
from rich.console import Console
from rich.traceback import install as install_rich_traceback

from . import USER_AGENT
from . import __version__ as WIKIGET_VERSION
from .dl import Downloader
from .logging import configure_logging

console = Console()
error_console = Console(stderr=True)
app = App(
    console=console,
    error_console=error_console,
    default_parameter=Parameter(short_alias=True),
    help_prologue=f"Wikiget v{WIKIGET_VERSION} (https://github.com/NiLuJe/wikiget)",
    version_flags=["--version", "-V"],
    config=config.Toml("config.toml", use_commands_as_keys=False),
)
# NOTE: This adds a seemingly-required COMMAND placeholder in the USAGE string :/
# app.register_install_completion_command()

# Install verbose rich traceback handler using the error console
install_rich_traceback(console=error_console, show_locals=True)

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
    input: StdioPath,
    /,
    output: Directory | None = None,
    *,
    cfg: Config | None = None,
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

    if cfg is None:
        cfg = Config()

    configure_logging(verbosity=cfg.verbose, logfile=cfg.logfile, quiet=cfg.quiet)

    logger = logging.getLogger(__name__)

    # Sanity check args.FILE
    if not input.is_stdio and not (input.is_file() and os.access(input, os.R_OK)):
        logger.critical("Cannot access input file `%s`!", input)
        return 1

    # Log events are appended to the file if it already exists,
    # so, note the start of a new download session.
    logger.info("Starting download session using wikiget %s", WIKIGET_VERSION)
    logger.debug("User agent: %s", USER_AGENT)

    dl = Downloader(input, output, cfg)
    return dl.process_download()
