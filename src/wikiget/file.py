# wikiget - CLI tool for downloading files from Wikimedia sites
# Copyright (C) 2023 Cody Logan
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

"""Define a File class for representing individual files to be downloaded."""

from __future__ import annotations

from pathlib import Path

from attrs import define

# FIXME: refactor that a bit once we move to cyclopts...
@define
class File:
    """A file object."""
    name: Path
    dest: Path | None
    url: str

    def __eq__(self, other: object) -> bool:
        """Compare this File object with another for equality.

        :param other: another File to compare
        :type other: wikiget.file.File
        :return: True if the Files are equal and False otherwise
        :rtype: bool
        """
        if other.__class__ is self.__class__:
            return self.url == other.url
        else:
            return NotImplemented

    def __hash__(self) -> int:
        return hash(self.url)
