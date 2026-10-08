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

# NOTE: Drop this once we raise the minimum Python version to 3.14,
#       because PEP 649 & PEP 749 finally sloved this mess.
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from attrs import define, field

# NOTE: Can you partial a class constructor, so we don't have to pass output_dir every time?
@define(unsafe_hash=True)
class File:
    """A file object."""

    COMMONS_BASE_URL: ClassVar[str] = "https://upload.wikimedia.org/wikipedia/commons"

    filename: Path = field(converter=Path, eq=False)
    output: Path | None = field(default=None, eq=False)
    # NOTE: attrs creates slotted classes by default,
    #       so we need to declare them for them to get a slot,
    #       as we only ever populate them @ post_init.
    dest: Path = field(init=False, eq=False)
    url: str = field(init=False, eq=True)

    def __attrs_post_init__(self):
        # NOTE: Validators have already run by then
        # Compute dest
        self.dest: Path = self._compute_dest()

        # Compute url
        self.url: str = self._compute_commons_url()

    def _compute_dest(self) -> Path:
        if self.output:
            if self.output.is_dir():
                # i.e., batch mode
                return self.output / self.filename
            else:
                # i.e., single-file mode
                return self.output
        else:
            return self.filename

    def _compute_commons_url(self) -> str:
        # c.f., https://commons.wikimedia.org/wiki/Commons:FAQ#What_are_the_strangely_named_components_in_file_paths?
        # Heavily inspired from CommonsDownloadTool's commons_file_url, c.f.,
        # https://github.com/lingua-libre/CommonsDownloadTool/blob/b2653dc7f38d561d6034e460dcd0eb4e96fbef6c/commons_download_tool.py#L60C1-L90

        # Work on the final path component
        filename = str(self.filename.name).replace(" ", "_")

        # Ensure input is UTF-8, as that's how WM computes this
        hashed_name = hashlib.md5(filename.encode("utf-8")).hexdigest()

        file_path = f"{hashed_name[0]}/{hashed_name[:2]}/{filename}"

        return f"{self.COMMONS_BASE_URL}/{file_path}"

    @classmethod
    def for_output(cls, output: Path, *args, **kwargs) -> File:
        return cls(*args, output=output, **kwargs)
