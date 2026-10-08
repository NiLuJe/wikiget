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

import logging
import os

from cyclopts import App, Parameter, config
from cyclopts.types import Directory, StdioPath
from rich.traceback import install as install_rich_traceback

from . import USER_AGENT
from . import __version__ as WIKIGET_VERSION
from .config import Config
from .dl import Downloader
from .logging import configure_logging, console, error_console

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

# Install rich traceback handler using the error console
install_rich_traceback(console=error_console)

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
        Path in which to store the downloaded file;
        or output directory in which to store the files in batch mode.
    """

    if cfg is None:
        cfg = Config()

    configure_logging(verbosity=cfg.verbose, logfile=cfg.logfile, quiet=cfg.quiet)

    logger = logging.getLogger(__name__)

    # Sanity check args.FILE
    if not input.is_stdio and not (input.is_file() and os.access(input, os.R_OK)):
        logger.critical("Cannot access input file `%s`!", str(input))
        return 1

    if output:
        if cfg.batch:
            # Create & check the output directory in batch mode
            output.mkdir(parents=True, exist_ok=True)
            if not output.is_dir():
                logger.critical("Output `%s` is not a directory!", str(output))
            if not os.access(output, os.W_OK):
                logger.critical("Output directory `%s` is not writable!", str(output))
        else:
            # In single-file mode, make sure the parent folder exists
            output.parent.mkdir(parents=True, exist_ok=True)

    # Log events are appended to the file if it already exists,
    # so, note the start of a new download session.
    logger.info("***")
    logger.info("Starting download session using wikiget %s", WIKIGET_VERSION)
    logger.debug("User agent: %s", USER_AGENT)

    dl = Downloader(input, output, cfg)
    return dl.process_download()
