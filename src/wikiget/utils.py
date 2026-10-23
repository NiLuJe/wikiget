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

# Lazy load modules only used at runtime (Py 3.15+)
__lazy_modules__ = ["datetime"]

from datetime import timedelta

from rich import filesize
from rich.progress import Progress, TaskID


# Because https://github.com/Textualize/rich/pull/2345 never landed ;'(
def get_elapsed_time(progress: Progress, task_id: TaskID) -> timedelta | None:
    """Pull the finished or elapsed time of a rich.progress task (exactly like TimeElapsedColumn)"""
    with progress._lock:
        task = progress._tasks[task_id]
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return None
        else:
            # NOTE: Its str dunder will do the formatting for us :)
            return timedelta(seconds=max(0, elapsed))


def get_transfer_speed(progress: Progress, task_id: TaskID) -> str:
    """Pull the transfer speed of a rich.progress task (exactly like TransferSpeedColumn)"""
    with progress._lock:
        task = progress._tasks[task_id]
        speed = task.finished_speed or task.speed
        if speed is None:
            return "?"
        data_speed = filesize.decimal(int(speed))
        return f"{data_speed}/s"
