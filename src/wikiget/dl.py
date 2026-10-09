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

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from functools import partial
import logging
import signal
from threading import Event, Lock
from types import FrameType
from typing import TYPE_CHECKING

from mwclient import APIError, InvalidResponse, LoginError, Site
from requests import ConnectionError, HTTPError
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from wikiget.client import connect_to_site, query_api
from wikiget.exceptions import ParseError
from wikiget.logging import FileLogAdapter, console
from wikiget.parse import get_dest, batch_files, batch_size
from wikiget.validations import verify_hash

if TYPE_CHECKING:
    from argparse import Namespace

    from wikiget.file import File

logger = logging.getLogger(__name__)

class Downloader:
    def __init__(self, args: Namespace) -> None:
        """Instantiate a downloader instance, following the CLI args.

        :param args: command-line arguments and their values
        :type args: argparse.Namespace
        """

        self.args = args
        self.input = self.args.FILE
        self.output = self.args.output
        self.force_redownload = self.args.force
        self.batch_mode = self.args.batch
        self.thread_count = self.args.threads
        self.dry_run = self.args.dry_run

        self.sites: dict[str, Site] = {}
        self.sites_lock = Lock()
        self.console = console

        self.status = Counter({
            "errors": 0,
            "warnings": 0,
        })

        # And install our SIGINT handler
        self.done_event = Event()
        signal.signal(signal.SIGINT, partial(self.handle_sigint))

    def errors(self) -> int:
        return self.status["errors"]

    @staticmethod
    def increment_errors(counter: Counter) -> None:
        counter["errors"] += 1

    def warnings(self) -> int:
        return self.status["warnings"]

    @staticmethod
    def increment_warnings(counter: Counter) -> None:
        counter["warnings"] += 1

    def handle_sigint(self, _signum: int, _frame: FrameType):
        self.done_event.set()
        self.console.log("Caught a [bold red]SIGINT[/], tearing down pending tasks...")

    def prep_download(self, dl: str) -> File:
        """Prepare to download a file by parsing the filename or URL and CLI arguments.

        :param dl: a string representing the file or URL to download
        :type dl: str
        :param args: command-line arguments and their values
        :type args: argparse.Namespace
        :raises FileExistsError: the destination file already exists on disk
        :return: a File object representing the file to download
        :rtype: wikiget.file.File
        """
        file = get_dest(dl, self.args)

        # check if the destination file already exists; don't overwrite unless the user says
        if file.dest.is_file() and file.dest.stat().st_size != 0 and not self.force_redownload:
            msg = f"[{file.dest}] File already exists; skipping download"
            raise FileExistsError(msg)

        return file

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


    def process_download(self) -> int:
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

        if self.batch_mode:
            # batch download mode

            # Display an outer progress bar that tracks prgress over the full batch
            with self.overall_progress_bar() as overall_progress:
                self.threaded_download(overall_progress) if self.thread_count > 1 else self.batched_download(overall_progress)
        else:
            # single download mode
            with self.progress_bar() as progress:
                self.status = self.download_pipeline(1, self.input, progress)

        errors, warnings = self.errors(), self.warnings()
        if errors or warnings:
            logger.warning(
                    "%d error%s and %d warning%s encountered during processing",
                    errors,
                    "s"[: errors ^ 1],
                    warnings,
                    "s"[: warnings ^ 1],
            )

        # return a non-zero exit code if any meaningful problems were encountered,
        # even if some downloads completed successfully
        return 1 if errors else 0


    def batched_download(self, overall_progress: Progress) -> None:
        """Download files specified in a batch file.

        The batch file is parsed as we go, and files are checked
        for validity before being downloaded one by one.
        """

        overall_task = overall_progress.add_task("Batched download", total=batch_size(self.input))
        with self.progress_bar() as progress:
            for line_num, line in batch_files(self.input):
                self.status.update(self.download_pipeline(line_num, line, progress))
                overall_progress.advance(overall_task)


    def threaded_download(self, overall_progress: Progress) -> None:
        """Download files specified in a batch file.

        The batch file is parsed into a dictionary, and the dictionary's items are checked
        for validity before being downloaded using a ThreadPool for simultaneous downloads,
        if threading was specified on the command line.
        """

        overall_task = overall_progress.add_task("Threaded download", total=batch_size(self.input))
        with (
            self.progress_bar() as progress,
            ThreadPoolExecutor(max_workers=self.thread_count) as executor,
        ):
            futures = (
                executor.submit(self.download_pipeline, line_num, line, progress)
                for line_num, line in batch_files(self.input)
            )
            # wait for downloads to finish
            for future in as_completed(futures):
                self.status.update(future.result())
                overall_progress.advance(overall_task)

                # Abort early on SIGINT
                if self.done_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break


    def query_filename(self, line_num: int, line: str) -> tuple[Counter, File | None]:
        """Prepare a download and query Commons for the canonical download URL

        Returns a File instance on success or None on failure.
        """

        status = Counter()

        logger.info("Processing '%s' at line %i", line, line_num)
        try:
            file = self.prep_download(line)
            # Make sure concurrent threads won't attempt to connect to the same site
            with self.sites_lock:
                site = self.sites.get(file.site, None)

                # if there's already a Site object matching the desired host,
                # reuse it to reduce the number of API calls made per file
                if site:
                    logger.debug("Reusing the existing connection to %s", site.host)
                else:
                    logger.debug("Making a new connection to %s", file.site)
                    site = connect_to_site(file.site, self.args)
                    # cache the new Site for reuse
                    self.sites[site.host] = site
            file.image = query_api(file.name, site)
        except ParseError as e:
            logger.error("%s (line %i)", str(e), line_num)
            self.increment_errors(status)
            file = None
        except FileExistsError as e:
            logger.warning(e)
            self.increment_warnings(status)
            file = None
        except (ConnectionError, HTTPError, InvalidResponse, LoginError, APIError):
            logger.error(
                "Unable to download '%s' (line %i) due to an API query error",
                line,
                line_num,
            )
            self.increment_errors(status)
            file = None

        return status, file


    def download(self, f: File, progress: Progress, task: TaskID) -> Counter:
        """Fetch file information and contents if the file exists and save it to disk.

        :param f: a File object representing the file to be downloaded
        :type f: wikiget.file.File
        :param args: command-line arguments and their values
        :type args: argparse.Namespace
        :return: count of errors and warnings encountered during processing
        :rtype: Counter
        """
        file = f.image
        filename = f.name
        dest = f.dest
        site = file.site

        status = Counter()

        # prepend the current filename to all log messages
        adapter = FileLogAdapter(logger, {"filename": filename})

        if file.imageinfo:
            # file exists either locally or at a common repository, like Wikimedia Commons
            file_url = file.imageinfo["url"]
            file_size = file.imageinfo["size"]
            file_sha1 = file.imageinfo["sha1"]

            filename_log = f"Downloading '{filename}' ({file_size} bytes) from {site.host}"
            if self.output:
                filename_log += f" to '{dest}'"
            adapter.info(filename_log)
            adapter.info(f"{file_url}")

            if self.dry_run:
                adapter.warning("Dry run; download skipped")
                return status

            try:
                progress.update(task, total=file_size)
                with dest.open("wb") as fd:
                    progress.start_task(task)
                    # NOTE: Strong urge to also fork mwclient and just import niquests as requests...
                    #       That would require also wrapping that iter_content in a context manager.
                    # download the file using the existing Site session
                    r = site.connection.get(file_url, stream=True)

                    # Minimal error handling
                    try:
                        # NOTE: On r.status_code == requests.codes.ok (i.e., 200),
                        #       raise_for_status will return:
                        #       None with requests
                        #       r with niquests
                        r.raise_for_status()
                    except HTTPError as e:
                        adapter.error(f"File could not be downloaded: {e}")
                        dest.unlink()
                        self.increment_errors(status)
                        progress.console.log(f"[bold red]FAILED[/] to download [bold magenta]{filename}[/]")
                        return status

                    for chunk in r.iter_content(None):
                        fd.write(chunk)
                        progress.update(task, advance=len(chunk))

                        # Clean up on SIGINT, so we don't leave incomplete files around
                        if self.done_event.is_set():
                            adapter.error("Caught a SIGINT, aborting")
                            dest.unlink()
                            progress.remove_task(task)
                            progress.console.log(f"[bold red]Aborted[/] [bold magenta]{filename}[/] download")
                            return status

                    # Pull the elapsed time for our task out of rich's guts...
                    t = progress._tasks[task]
                    elapsed = t.finished_time if t.finished else t.elapsed
                    if elapsed is None:
                        delta = "?"
                    else:
                        # NOTE: Its str dunder will do the formatting for us :)
                        delta = timedelta(seconds=max(0, round(elapsed)))
                    progress.console.log(f"Downloaded [bold magenta]{filename}[/] in [bold yellow]{delta}[/]")
            except OSError as e:
                adapter.error(f"File could not be written: {e}")
                dest.unlink(missing_ok=True)
                progress.remove_task(task)
                self.increment_errors(status)
                progress.console.log(f"[bold red]FAILED[/] to write local file for [bold magenta]{filename}[/]")
                return status

            # verify file integrity and log the details
            try:
                dl_sha1 = verify_hash(dest)
            except OSError as e:
                adapter.error(f"File downloaded but could not be verified: {e}")
                dest.unlink()
                progress.remove_task(task)
                self.increment_errors(status)
                progress.console.log(f"[bold red]FAILED[/] to verify downloaded [bold magenta]{filename}[/]")
                return status

            adapter.info(f"Remote file SHA1 is {file_sha1}")
            adapter.info(f"Local file SHA1 is {dl_sha1}")
            if dl_sha1 == file_sha1:
                adapter.info("Hashes match!")
                # at this point, we've successfully downloaded the file
                success_log = f"'{filename}' downloaded"
                if self.output:
                    success_log += f" to '{dest}'"
                adapter.info(success_log)
            else:
                adapter.error("Hash mismatch! Downloaded file may be corrupt.")
                dest.unlink()
                self.increment_errors(status)
                progress.console.log(f"[bold red]CORRUPT[/] download for [bold magenta]{filename}[/]")

            # NOTE: A single Progress instance will only ever show as much tasks as the terminal height allows...
            #       Drop completed tasks to free up space.
            progress.remove_task(task)

        else:
            # no file information returned
            adapter.warning("Target does not appear to be a valid file")
            self.increment_warnings(status)

        return status


    def download_pipeline(self, line_num: int, line: str, progress: Progress) -> Counter:
        """Full download pipeline, from API query, to progress handling, to actual download.

           Returns the number of errors encountered.
        """

        status = Counter()

        # Abort early w/o inflating the error count if we caught a SIGINT
        if self.done_event.is_set():
            return status

        status, file = self.query_filename(line_num, line)

        if not file:
            return status

        task = progress.add_task("download", filename=str(file.dest), total=None, start=False)
        status.update(self.download(file, progress, task))

        return status
