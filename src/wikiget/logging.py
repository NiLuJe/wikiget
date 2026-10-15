# wikiget - CLI tool for downloading files from Wikimedia sites
# Copyright (C) 2018-2023 Cody Logan
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

"""Configure program log configuration and adapters."""

# Lazy load modules only used at runtime (Py 3.15+)
__lazy_modules__ = ["logging.handlers", "queue", "rich.console", "rich.logging"]

from collections.abc import MutableMapping
import logging
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
import queue
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

# -v
VERBOSE = 1
# -vv
VERY_VERBOSE = 2

console = Console()
error_console = Console(stderr=True)


class FileLogAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(self, msg: str, kwargs: MutableMapping[str, Any]) -> tuple[str, MutableMapping[str, Any]]:
        if self.extra is not None:
            return f"[{self.extra['filename']}] {msg}", kwargs
        return msg, kwargs


def configure_logging(verbosity: int, logfile: Path | None, *, quiet: bool) -> QueueListener:
    """Set the program's log configuration according to the given settings.

    :param verbosity: how verbose the log messages should be
    :type verbosity: int
    :param logfile: file to write log messages to, if any
    :type logfile: str
    :param quiet: True if log messages should be suppressed or False otherwise
    :type quiet: bool
    """
    loglevel = logging.WARNING  # default log level
    if verbosity >= VERY_VERBOSE:
        # this includes API and library messages, not just from wikiget
        loglevel = logging.DEBUG
    elif verbosity >= VERBOSE:
        loglevel = logging.INFO
    elif quiet:
        loglevel = logging.ERROR

    # configure logging:
    # console log level is set via -v, -vv, and -q options;
    # file log level is always debug (TODO: make this user configurable)
    # NOTE: We opt for the threaded setup described in
    #       https://docs.python.org/3/howto/logging-cookbook.html#blocking-handlers,
    #       as rich handlers are pretty CPU-intensive...
    console_log_format = "%(threadName)s: %(message)s"
    file_log_format = "%(asctime)s [%(levelname)-7s] %(threadName)s: %(message)s"

    logger = logging.getLogger("")  # root logger
    logger.setLevel(logging.DEBUG)

    # Unbounded queue for threaded logging
    que = queue.Queue(-1)
    # set up console logging
    c_qh = QueueHandler(que)
    ch = RichHandler(console=console)
    ch.setLevel(loglevel)
    ch.setFormatter(logging.Formatter(console_log_format))
    logger.addHandler(c_qh)

    if logfile:
        # also log to file
        f_qh = QueueHandler(que)
        fh = logging.FileHandler(logfile)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(file_log_format))
        logger.addHandler(f_qh)
        return QueueListener(que, ch, fh, respect_handler_level=True)
    else:
        return QueueListener(que, ch, respect_handler_level=True)
