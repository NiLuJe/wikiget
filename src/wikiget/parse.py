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

"""Parse download targets and batch files."""

from __future__ import annotations
from pathlib import Path

from argparse import Namespace
import fileinput
import logging
import sys
from collections.abc import Iterable
from urllib.parse import unquote, urlparse

from more_itertools import ilen

import wikiget
from wikiget.exceptions import ParseError
from wikiget.file import File
from wikiget.validations import valid_file

logger = logging.getLogger(__name__)

def batch_size(batch_file: str) -> int:
    """Returns the amount of lines in the input.
    NOTE: This does *NOT* do any kind of validation,
          so this may return a count slightly higher than
          the actual amount of *viable* lines
          (e.g., it will include comments).

    :param batch_file: name of the file to parse or "-" for stdin
    :type batch_file: str
    :return: a line count
    :rtype: int
    """

    # NOTE: Because we consume batch_files as a generator,
    #       we don't really have a better way to do this than just consuming the iterator :/.
    size = ilen(fileinput.input(batch_file, encoding="utf-8"))

    # Rewind stdin for later batch_files calls
    if batch_file == "-":
        sys.stdin.seek(0)

    return size

def batch_files(batch_file: str) -> Iterable[tuple[int, str]]:
    """Parse a batch file or stdin for valid input.
    Any blank lines or lines starting with '#' are skipped.

    :param batch_file: name of the file to parse or "-" for stdin
    :type batch_file: str
    :return: a generator that yields line_num, filename tuples
    :rtype: Iterable[tuple[int, str]]
    """

    if batch_file == "-":
        logger.info("Using stdin for batch download")
    else:
        logger.info("Using file '%s' for batch download", batch_file)

    with fileinput.input(batch_file, encoding="utf-8") as fd:
        # read the file into memory and process each line as we go
        for line_num, line in enumerate(fd, start=1):
            line_s = line.strip()
            # ignore blank lines and lines starting with "#" (for comments)
            if line_s and not line_s.startswith("#"):
                yield line_num, line_s
