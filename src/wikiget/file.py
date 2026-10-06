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


# FIXME: refactor that a bit once we move to cyclopts...
# FIXME: Switch to attrs while doing that.
class File:
    """A file object."""

    def __init__(
        self,
        name: Path,
        dest: Path | None,
        url: str,
    ) -> None:
        """Initialize a new file with the given parameters.

        Only the name is required. If a destination isn't specified, the provided name
        will be used as the output name.

        :param name: name of the file
        :type name: str
        :param dest: output name of the file; if not specified, defaults to name
        :type dest: Path, optional
        """
        self.name = name
        self.dest = dest or name
        self.url = url

    def __eq__(self, other: object) -> bool:
        """Compare this File object with another for equality.

        :param other: another File to compare
        :type other: wikiget.file.File
        :return: True if the Files are equal and False otherwise
        :rtype: bool
        """
        if not isinstance(other, File):
            return NotImplemented
        return self.url == other.url

    def __str__(self) -> str:
        """Return a basic string representation of this class, for str().

        :return: string form of the class
        :rtype: str
        """
        return str(self.__dict__)

    def __repr__(self) -> str:
        """Return a formal string representation of this class, for repr().

        :return: string form of the class
        :rtype: str
        """
        attr_list = [self.name, self.dest, self.url]
        return '{}("{}")'.format(
            self.__class__.__name__,
            '", "'.join(map(str, attr_list)),
        )

    def __hash__(self) -> int:
        return hash(self.url)
