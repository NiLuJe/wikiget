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

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Group, Parameter, validators
from cyclopts.types import PositiveInt

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
