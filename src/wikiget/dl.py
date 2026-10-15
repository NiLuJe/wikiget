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

"""Prepare and process file downloads."""

# Lazy load modules only used at runtime (Py 3.15+)
__lazy_modules__ = ["asyncio", "collections", "datetime", "functools", "itertools", "signal", "niquests", "rich.progress"]

import asyncio
from asyncio import AbstractEventLoop, Event
from collections import Counter
from datetime import timedelta
from functools import partial
from itertools import batched
import logging
from pathlib import Path
import signal
from typing import Any, ClassVar

from cyclopts.types import StdioPath
import niquests
from niquests import __version__ as NIQUESTS_VERSION
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .config import Config
from .exceptions import ParseError
from .file import File
from .logging import FileLogAdapter, console
from .parse import batch_files, batch_size
from .version import __version__ as WIKIGET_VERSION

logger = logging.getLogger(__name__)


class Downloader:
    USER_AGENT: ClassVar[str] = (
        f"wikiget/{WIKIGET_VERSION} (https://github.com/NiLuJe/wikiget) niquests/{NIQUESTS_VERSION}"
    )

    def __init__(self, input: StdioPath, output: Path | None, cfg: Config) -> None:
        """Instantiate a downloader instance, following the CLI args."""

        self.cfg = cfg
        self.input = input
        self.output = output
        self.force_redownload = self.cfg.force
        self.concurrency = self.cfg.concurrency
        self.rate_limit = self.cfg.rps
        self.dry_run = self.cfg.dry_run

        self.console = console

        self.status = Counter(
            {
                "errors": 0,
                "warnings": 0,
                "successes": 0,
            }
        )

        # And install our SIGINT handler
        self.done_event = Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, partial(self.handle_sigint, loop))

        logger.debug("User agent: %s", self.USER_AGENT)
        logger.debug("Rate limit: %.1f rps", self.rate_limit)

        self.start_time = loop.time()

    def errors(self) -> int:
        return self.status["errors"]

    def increment_errors(self) -> None:
        self.status["errors"] += 1

    def warnings(self) -> int:
        return self.status["warnings"]

    def increment_warnings(self) -> None:
        self.status["warnings"] += 1

    def successes(self) -> int:
        return self.status["successes"]

    def increment_successes(self) -> None:
        self.status["successes"] += 1

    def handle_sigint(self, loop: AbstractEventLoop) -> Any:
        self.done_event.set()
        self.console.log("Caught a [bold red]SIGINT[/], tearing down pending tasks...")

        # Forces an immediate teardown, at the risk of leaving incomplete data around...
        # raise KeyboardInterrupt

    def overall_progress_bar(self) -> Progress:
        """Return a rich.progress Progress instance laid out for overall progress"""

        return Progress(
            SpinnerColumn(),
            TextColumn("[bold white][progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            "•",
            TimeRemainingColumn(elapsed_when_finished=False),
            TimeElapsedColumn(),
            console=self.console,
        )

    def progress_bar(self) -> Progress:
        """Return a rich.progress Progress instance laid out for our individual downloads"""

        return Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(binary_units=True),
            "•",
            # Unlikely to have time to update, given our small file sizes
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(elapsed_when_finished=True),
            console=self.console,
        )

    def prep_download(self, dl: str) -> File:
        """Prepare to download a file by parsing the filename or URL.

        :param dl: a string representing the file or URL to download
        :type dl: str
        :raises FileExistsError: the destination file already exists on disk
        :return: a File object representing the file to download
        :rtype: wikiget.file.File
        """
        try:
            file = File(dl, output=self.output)
        except ParseError:
            raise

        # check if the destination file already exists; don't overwrite unless the user says
        if file.dest.is_file() and file.dest.stat().st_size != 0 and not self.force_redownload:
            msg = f"[{file.dest}] File already exists; skipping download"
            raise FileExistsError(msg)

        return file

    async def download(self) -> int:
        with (
            self.overall_progress_bar() as self.overall_progress,
            self.progress_bar() as self.progress,
        ):
            await self.process_download()

        errors, warnings = self.errors(), self.warnings()
        if errors or warnings:
            logger.warning(
                "%d error%s and %d warning%s encountered during processing",
                errors,
                "s"[: errors ^ 1],
                warnings,
                "s"[: warnings ^ 1],
            )
        successes = self.successes()
        if successes:
            end_time = asyncio.get_running_loop().time()
            delta = timedelta(seconds=round(end_time - self.start_time))

            logger.info("Downloaded %d items in %s (%.2f rps)", successes, delta, successes / delta.total_seconds())

        # Return a non-zero exit code if any meaningful problems were encountered,
        # even if some downloads completed successfully
        return 1 if errors else 0

    async def process_download(self) -> None:
        """Process the download target given in the CLI args as a single file or batch file.

        If the target is a batch file, process with batch_download and return the number of
        errors encountered, if any. If there were any errors, log the number and exit with
        code 1. If no errors, exit with code 0.

        If the target is a single file or URL, process with prep_download and log any
        exceptions that it raises. If there aren't any, download the file and return the
        exit code appropriately.

        :return: program exit code (1 if there were any problems or 0 otherwise)
        :rtype: int
        """

        # Setup auto-retry, as we're very likely to hit 429 on the way...
        # c.f., https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
        # NOTE: We *could* also add a rate limiter on *our* end here, if need be,
        #       c.f., https://niquests.readthedocs.io/en/latest/user/advanced.html#rate-limiting
        retry = niquests.RetryConfiguration(
            total=10,
            # NOTE: CommonsDownloadTool also attempts to retry on 403, which is... weird?
            status_forcelist=[429, 500, 502, 503, 504],
            # NOTE: Roughly matches the Retry-After value, which currently seems to start at 10s
            backoff_factor=5,
            backoff_max=30,
            backoff_jitter=0.5,
            respect_retry_after_header=True,
        )

        # Setup our UA
        ua = {"user-agent": self.USER_AGENT}

        # NOTE: Setup a rate limiter on our end to try to avoid hitting 429...
        limiter = niquests.AsyncLeakyBucketLimiter(rate=self.rate_limit)

        # NOTE: There's currently only a single A record for upload.wikimedia.org,
        #       so, no need for happy eyeballs.
        # NOTE: We *would* use base_url here, but for the fact that we actually support passing full URLs ;).
        # FIXME: Reimplement authentification (c.f., mwclient site_login)
        async with niquests.AsyncSession(multiplexed=False, hooks=limiter, retries=retry, headers=ua) as s:
            self.overall_task = self.overall_progress.add_task("Download...", total=batch_size(self.input))
            for batch in batched(batch_files(self.input), self.concurrency, strict=False):
                # Abort early w/o inflating the error count if we caught a SIGINT
                if self.done_event.is_set():
                    break

                downloads = {line: self.query_filename(line, filename) for line, filename in batch}
                requests = {
                    file: s.get(file.url, stream=True, headers=file.headers, params=file.params)
                    for file in downloads.values()
                    if file
                }
                # Start tasks ASAP so we get an accurate elapsed time
                tasks = {
                    file: self.progress.add_task("download", filename=str(file.dest.name), total=None)
                    for file in downloads.values()
                    if file
                }

                # Advance progress bar for lines where query_filename failed
                self.overall_progress.advance(self.overall_task, advance=len(downloads) - len(requests))

                # await s.gather(*responses.values())  # For mutliplexed=true
                responses = await asyncio.gather(*requests.values())
                async with asyncio.TaskGroup() as tg:
                    for file, r in zip(requests.keys(), responses, strict=True):
                        task = tasks[file]
                        tg.create_task(self.handle_response(r, file, task))

    def query_filename(self, line_num: int, line: str) -> File | None:
        """Prepare a download and compute the canonical Commons download URL

        Returns a File instance on success or None on failure.
        """

        logger.info("Processing '%s' @ line %i", line, line_num)
        try:
            return self.prep_download(line)
        except ParseError as e:
            logger.error("%s (@ line %i)", str(e), line_num)
            self.increment_errors()
        except FileExistsError as e:
            logger.warning(e)
            self.increment_warnings()

        return None

    async def handle_response(self, r: niquests.AsyncResponse, file: File, task: TaskID) -> None:
        try:
            await self.process_response(r, file, task)
        finally:
            await r.close()
            # NOTE: A single Progress instance will only ever show as many tasks as the terminal height allows...
            #       Drop completed tasks to free up space.
            self.progress.remove_task(task)
            self.overall_progress.advance(self.overall_task)

    async def process_response(self, r: niquests.AsyncResponse, file: File, task: TaskID) -> None:
        """Fetch file information and contents if the file exists and save it to disk."""

        # prepend the current filename to all log messages
        adapter = FileLogAdapter(logger, {"filename": file.filename})

        # Minimal error handling
        try:
            # NOTE: On r.status_code == requests.codes.ok (i.e., 200),
            #       raise_for_status will return:
            #       None with requests
            #       r with niquests
            r.raise_for_status()
        except niquests.HTTPError as e:
            adapter.error(f"File could not be downloaded: {e}")
            self.increment_errors()
            self.progress.console.log(
                f"[bold red]FAILED[/] to download [bold magenta]{file.filename}[/] ([bold yellow]{r.status_code}[/])"
            )
            return

        remote_size = int(r.headers.get("content-length", 0))  # int(str(r.oheaders.content_length))
        # Poor man's hash check, MD5
        remote_hash = str(r.oheaders.etag)

        filename_log = f"Downloading '{file.filename}' ({remote_size} bytes)"
        adapter.info(filename_log)
        adapter.info(f"{file.url}")

        if self.dry_run:
            adapter.warning("Dry run; download skipped")
            return

        try:
            self.progress.update(task, total=remote_size)

            # NOTE: When input is an URL, we preserve the full path;
            #       make sure the directory structure actually exists!
            file.dest.parent.mkdir(parents=True, exist_ok=True)

            with file.dest.open("wb") as fd:
                async for chunk in await r.iter_content():
                    fd.write(chunk)
                    if r.download_progress:
                        self.progress.update(task, completed=r.download_progress.total)
                    else:
                        self.progress.update(task, advance=len(chunk))

                # Pull the elapsed time for our task out of rich's guts...
                t = self.progress._tasks[task]
                elapsed = t.finished_time if t.finished else t.elapsed
                if elapsed is None:
                    delta = "?"
                else:
                    # NOTE: Its str dunder will do the formatting for us :)
                    delta = timedelta(seconds=max(0, round(elapsed)))

                self.progress.console.log(f"Downloaded [bold magenta]{file.filename}[/] in [bold yellow]{delta}[/]")
        except OSError as e:
            adapter.error(f"File could not be written: {e}")
            file.dest.unlink(missing_ok=True)
            self.increment_errors()
            self.progress.console.log(f"[bold red]FAILED[/] to write local file for [bold magenta]{file.filename}[/]")
            return

        # Verify file integrity and log the details
        try:
            hash_ok = file.matches_checksum(remote_hash)
        except OSError as e:
            adapter.error(f"File downloaded but could not be verified: {e}")
            file.dest.unlink()
            self.increment_errors()
            self.progress.console.log(f"[bold red]FAILED[/] to verify downloaded [bold magenta]{file.filename}[/]")
            return

        if hash_ok:
            # At this point, we've successfully downloaded the file
            success_log = f"'{file.filename}' downloaded"
            if self.output:
                success_log += f" to '{file.dest}'"
            adapter.info(success_log)
        else:
            adapter.error("Hash mismatch! Downloaded file may be corrupt.")
            file.dest.unlink()
            self.increment_errors()
            self.progress.console.log(f"[bold red]CORRUPT[/] download for [bold magenta]{file.filename}[/]")

        self.increment_successes()
        return
