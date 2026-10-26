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

__lazy_modules__ = ["collections", "datetime", "functools", "logging", "signal", "niquests", "rich", "rich.progress"]

import asyncio
from asyncio import AbstractEventLoop, Event, Queue, QueueEmpty, QueueFull, QueueShutDown
from datetime import timedelta
from functools import partial
import logging
from pathlib import Path
import signal
from types import SimpleNamespace
from typing import Any, ClassVar

from cyclopts.types import StdioPath
import niquests
from niquests import __version__ as NIQUESTS_VERSION
from rich import filesize
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
from .utils import get_elapsed_time, get_transfer_speed
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
        self.auth_token = self.cfg.auth_token
        self.dry_run = self.cfg.dry_run

        self.console = console

        self.errors = 0
        self.warnings = 0
        self.successes = 0

        self.window_requests = 0
        self.total_requests = 0
        self.window_bytes = 0
        self.total_bytes = 0

        # Mainly to avoid double array notation inside TextColumns...
        # NOTE: Also, we need an object as we need this to be passed by reference to rich.progress's add_task,
        #       to be able to retrive it from inside a Progress instance *and* update it from within one of our coro here.
        self.progress_results = SimpleNamespace(
            {
                "rps": "?",
                "speed": "?",
            }
        )

        self.q = Queue(maxsize=self.concurrency)
        self.done_q = Queue(maxsize=self.concurrency)

        # And install our SIGINT handler
        self.done_event = Event()
        self.loop = asyncio.get_running_loop()
        self.loop.add_signal_handler(signal.SIGINT, partial(self.handle_sigint, self.loop))

        logger.debug("User agent: %s", self.USER_AGENT)
        if self.rate_limit is not None:
            logger.debug("Rate limit: %.2f rps", self.rate_limit)

        self.start_time = self.loop.time()
        self.previous_time = self.start_time
        self.current_window = timedelta(seconds=self.start_time)
        self.elapsed_time = timedelta(seconds=0)

    def update_elapsed_time(self) -> None:
        self.now = self.loop.time()
        self.current_window = timedelta(seconds=self.now - self.previous_time)
        self.elapsed_time = timedelta(seconds=self.now - self.start_time)
        self.previous_time = self.now

    def handle_sigint(self, loop: AbstractEventLoop) -> Any:
        self.done_event.set()
        self.console.log("Caught a [bold red]SIGINT[/], winding down...")

        # NOTE: This would force an immediate teardown, at the risk of leaving incomplete data around...
        # raise KeyboardInterrupt

    def overall_progress_bar(self) -> Progress:
        """Return a rich.progress Progress instance laid out for overall progress"""

        return Progress(
            SpinnerColumn(),
            TextColumn("[bold white][progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            "•",
            TextColumn("[blue][progress.description]{task.fields[status].rps}"),
            "•",
            TextColumn("[red][progress.description]{task.fields[status].speed}"),
            "•",
            TimeRemainingColumn(elapsed_when_finished=False),
            "•",
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

    def update_progress_results(self) -> None:
        self.update_elapsed_time()

        # Requests per second
        self.progress_results.rps = f"{self.window_requests / self.current_window.total_seconds():.2f} r/s"

        self.total_requests += self.window_requests
        self.window_requests = 0

        # Transfer speed
        speed = self.window_bytes / self.current_window.total_seconds()
        data_speed = filesize.decimal(int(speed))
        self.progress_results.speed = f"{data_speed}/s"

        self.total_bytes += self.window_bytes
        self.window_bytes = 0

    async def compute_overall_status(self) -> None:
        try:
            while True:
                # Over 10s windows, like WM recommends
                await asyncio.sleep(10)

                self.update_progress_results()
        except asyncio.CancelledError:
            raise
        finally:
            # We want a final update on task cancellation (i.e., on exit)...
            self.update_progress_results()

    async def download(self) -> int:
        with (
            self.overall_progress_bar() as self.overall_progress,
            self.progress_bar() as self.progress,
        ):
            await self.download_loop()

        if self.errors or self.warnings:
            logger.warning(
                "%d error%s and %d warning%s encountered during processing",
                self.errors,
                "s"[: self.errors ^ 1],
                self.warnings,
                "s"[: self.warnings ^ 1],
            )
        if self.successes:
            # Requests per second
            total_rps = self.total_requests / self.elapsed_time.total_seconds()

            # Transfer speed
            total_speed = self.total_bytes / self.elapsed_time.total_seconds()
            total_transfer_speed = filesize.decimal(int(total_speed))

            # We don't see INFO on console w/o verbose output...
            logger.info(
                "Downloaded %d items in %s (%.2f r/s) @ %s/s",
                self.successes,
                self.elapsed_time,
                total_rps,
                total_transfer_speed,
            )
            if self.cfg.verbose == 0:
                console.log(
                    f"Downloaded {self.successes} items in {self.elapsed_time} ({total_rps:.2f} r/s) @ {total_transfer_speed}/s"
                )

        # Return a non-zero exit code if any meaningful problems were encountered,
        # even if some downloads completed successfully
        return 1 if self.errors else 0

    async def download_loop(self) -> None:
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

        item_count = batch_size(self.input)
        if item_count < 1:
            logger.warning("Nothing to download!")
            return

        # Start tracking the overall progress
        self.overall_task = self.overall_progress.add_task("Download...", total=item_count, status=self.progress_results)

        # Setup auto-retry, as we're very likely to hit 429 on the way...
        # c.f., https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
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
        headers = {
            "user-agent": self.USER_AGENT,
        }
        # Setup our OAuth2 access token (if any)
        if self.auth_token is not None:
            headers["authorization"] = f"Bearer {self.auth_token}"

        if self.rate_limit is not None:
            # NOTE: Setup a rate limiter on our end to try to avoid hitting 429...
            limiter = niquests.AsyncLeakyBucketLimiter(rate=self.rate_limit)
        else:
            limiter = None

        # NOTE: There's currently only a single A record for upload.wikimedia.org,
        #       so, no need for happy eyeballs.
        # NOTE: We *would* use base_url here, but for the fact that we actually support passing full URLs ;).
        async with (
            niquests.AsyncSession(multiplexed=False, hooks=limiter, retries=retry, headers=headers) as s,
            asyncio.TaskGroup() as tg,
        ):
            # Compute overall speed statistics every 10s
            status = tg.create_task(self.compute_overall_status(), name="Progress")

            # Ensure the queue is always filled
            # NOTE: Started early so we don't shutdown the queue before it has a chance to fill
            tg.create_task(self.fill_download_queue(), eager_start=True, name="QFill")  # push

            # Spin up as many workers as requested to consume queue items, assuming there are any
            # NOTE: For maximum throughput *in a free-threaded build* (and with non rate-limited remotes),
            #       each worker could run in a dedicated thread with its own event loop.
            if not self.q.empty():
                # NOTE: Guarded behind an empty check to avoid deadlocks on get
                #       if fill_download_queue never actually puts anything in there (e.g., invalid input)
                for i in range(self.concurrency):
                    tg.create_task(self.dl_worker(s), name=f"DL-{i}")  # pull

            # Wait until the queue has been completely drained
            await self.q.join()

            # Now that we're done, signal our workers to spin down
            self.q.shutdown()
            # NOTE: We never actually catch QueueShutDown for this one, so this is just for semantics.
            #       It gets filled by dl_worker, which may break early on SIGINT, *without* draining it.
            #       Even in a normal complete run, since we wait for it to be full before dropping a bar,
            #       we'll *always* leave at most a queue's worth of progress bars visible.
            #       This is also why we don't join that queue: it *never* actually goes empty.
            self.done_q.shutdown()
            # Also cancel the status updates
            status.cancel()

        # If you *really* want to clear leftover complete bars on exit...
        # self.progress_cleanup()

    def progress_cleanup(self) -> None:
        """Manually drain the completed progress bar queue"""
        while True:
            try:
                task_id = self.done_q.get_nowait()
                self.progress.remove_task(task_id)
                self.done_q.task_done()
            except QueueEmpty, QueueShutDown:
                break

    def parse_input(self, dl: str) -> File:
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

        # Check if the destination file already exists; don't overwrite unless the user says
        if file.dest.is_file() and file.dest.stat().st_size != 0 and not self.force_redownload:
            msg = f"[{file.dest}] File already exists; skipping download"
            raise FileExistsError(msg)

        return file

    def get_file(self, line_num: int, line: str) -> File | None:
        """Prepare a download and compute the canonical Commons download URL

        Returns a File instance on success or None on failure.
        """

        logger.info("Processing `%s` @ line %i", line, line_num)
        try:
            return self.parse_input(line)
        except ParseError as e:
            logger.error("%s (@ line %i)", str(e), line_num)
            self.errors += 1
        except FileExistsError as e:
            logger.warning(e)
            self.warnings += 1

        return None

    async def fill_download_queue(self) -> None:
        for line, filename in batch_files(self.input):
            # Abort early if we caught a SIGINT
            if self.done_event.is_set():
                # Immediately drain the queue, so only the in-flight requests are processed
                self.q.shutdown(immediate=True)
                break

            file = self.get_file(line, filename)
            if file:
                # This will block until a slot is available,
                # ensuring we don't flood the queue upfront
                await self.q.put(file)
            else:
                # Account for invalid input immediately
                self.overall_progress.advance(self.overall_task)

    async def dl_worker(self, s: niquests.AsyncSession):
        while True:
            try:
                file = await self.q.get()
                progress_task = self.progress.add_task("download", filename=str(file.filename.name), total=None)

                async with await s.get(file.url, stream=True, headers=file.headers, params=file.params) as r:
                    await self.process_response(r, file, progress_task)

                # NOTE: A single Progress instance will only ever show as many tasks as the terminal height allows...
                #       Drop completed tasks to free up space, only keeping a few visible.
                try:
                    self.done_q.put_nowait(progress_task)
                except QueueFull:
                    # Remove the oldest bar to make space for this one
                    oldest_task = await self.done_q.get()
                    self.progress.remove_task(oldest_task)
                    self.done_q.task_done()

                    await self.done_q.put(progress_task)

                self.overall_progress.advance(self.overall_task)

                # We're done with this work item
                self.q.task_done()
            except QueueShutDown:
                break

    async def process_response(self, r: niquests.AsyncResponse, file: File, task: TaskID) -> None:
        """Fetch file information and contents if the file exists and save it to disk."""

        # That's a new request, successful or not!
        self.window_requests += 1

        # prepend the current filename to all log messages
        adapter = FileLogAdapter(logger, {"filename": file.filename.name})

        # Minimal error handling
        try:
            # NOTE: On r.status_code == requests.codes.ok (i.e., 200),
            #       raise_for_status will return:
            #       None with requests
            #       r with niquests
            r.raise_for_status()
        except niquests.HTTPError as e:
            adapter.error("File could not be downloaded: %s", e)
            self.errors += 1
            self.progress.console.log(
                f"[bold red]FAILED[/] to download [bold magenta]{file.filename.name}[/] ([bold yellow]{r.status_code}[/])"
            )
            return

        remote_size = int(r.headers.get("content-length", 0))  # int(str(r.oheaders.content_length))
        # On WM only, poor man's hash check, MD5
        if file.verify:
            if "etag" in r.oheaders:
                remote_hash = str(r.oheaders.etag)

                # Extremely minimal sanity check of the etag string, as the implementation varies between servers,
                # e.g., NGINX doesn't use a checksum, it just combines mtime & size,
                # c.f., https://github.com/nginx/nginx/blob/140aa942711868a8daeb95810bc9a81f272ada72/src/http/ngx_http_core_module.c#L1773-L1776
                if "-" in remote_hash:
                    adapter.warning("ETag %s doesn't look like a checksum, can't verify download!", remote_hash)
                    remote_hash = None
            else:
                adapter.warning("No ETag found, can't verify download!")
                remote_hash = None
        else:
            remote_hash = None

        adapter.info("Downloading %d bytes from %s", remote_size, file.url)

        if self.dry_run:
            adapter.warning("Dry run; download skipped")
            return

        try:
            self.progress.update(task, total=remote_size)

            # NOTE: When input is an URL, we preserve the full path;
            #       make sure the directory structure actually exists!
            file.dest.parent.mkdir(parents=True, exist_ok=True)

            with file.dest.open("wb") as fd:
                transferred_bytes = 0
                async for chunk in await r.iter_content():
                    fd.write(chunk)
                    if r.download_progress is not None:
                        self.progress.update(task, completed=r.download_progress.total)
                        # We need a delta for the overall counter...
                        chunk_bytes = r.download_progress.total - transferred_bytes
                        transferred_bytes = r.download_progress.total
                    else:
                        chunk_bytes = len(chunk)
                        self.progress.update(task, advance=chunk_bytes)
                    # Update the overall counter
                    self.window_bytes += chunk_bytes

                # Pull the elapsed time for our task out of rich's guts...
                if (delta := get_elapsed_time(self.progress, task)) is None:
                    delta = "?"
                speed = get_transfer_speed(self.progress, task)

                self.progress.console.log(
                    f"Downloaded [bold magenta]{file.filename.name}[/] in [bold yellow]{delta}[/] @ [bold red]{speed}[/]"
                )
        except OSError as e:
            adapter.error("File could not be written: %s", e)
            file.dest.unlink(missing_ok=True)
            self.errors += 1
            self.progress.console.log(
                f"[bold red]FAILED[/] to write local file for [bold magenta]{file.filename.name}[/]"
            )
            return

        if remote_hash is None:
            # No usable checksum, abort early
            self.successes += 1
            return

        # If possible, verify file integrity and log the details
        try:
            hash_ok = file.matches_checksum(remote_hash)
        except OSError as e:
            adapter.error("File downloaded but could not be verified: %s", e)
            file.dest.unlink()
            self.errors += 1
            self.progress.console.log(f"[bold red]FAILED[/] to verify downloaded [bold magenta]{file.filename.name}[/]")
            return

        if hash_ok:
            # At this point, we've successfully downloaded the file
            success_log = f"'{file.filename.name}' downloaded"
            if self.output:
                success_log += f" to '{file.dest}'"
            adapter.info(success_log)
        else:
            adapter.error("Hash mismatch! Downloaded file may be corrupt.")
            file.dest.unlink()
            self.errors += 1
            self.progress.console.log(f"[bold red]CORRUPT[/] download for [bold magenta]{file.filename.name}[/]")

        self.successes += 1
        return
