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
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from itertools import batched
import logging
from pathlib import Path
import signal
from threading import Event
from types import FrameType
from typing import Any

from cyclopts.types import StdioPath
import niquests
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

from . import USER_AGENT
from .config import Config
from .exceptions import ParseError
from .file import File
from .logging import FileLogAdapter, console
from .parse import batch_files, batch_size
from .validations import Validator

logger = logging.getLogger(__name__)


class Downloader:
    def __init__(self, input: StdioPath, output: Path | None, cfg: Config) -> None:
        """Instantiate a downloader instance, following the CLI args."""

        self.cfg = cfg
        self.input = input
        self.output = output
        self.force_redownload = self.cfg.force
        self.concurrency = self.cfg.concurrency
        self.dry_run = self.cfg.dry_run

        self.console = console

        self.status = Counter(
            {
                "errors": 0,
                "warnings": 0,
            }
        )

        self.validate = Validator()

        # All our File instances will use the same output,
        # so, save us some typing down the line ;).
        self.File: Callable[[str], File] = partial(File, output=output)

        # TODO: Revisit that once async?
        # And install our SIGINT handler
        self.done_event = Event()
        signal.signal(signal.SIGINT, partial(self.handle_sigint))

    def errors(self) -> int:
        return self.status["errors"]

    def increment_errors(self) -> None:
        self.status["errors"] += 1

    def warnings(self) -> int:
        return self.status["warnings"]

    def increment_warnings(self) -> None:
        self.status["warnings"] += 1

    def handle_sigint(self, _signum: int, _frame: FrameType | None) -> Any:
        self.done_event.set()
        self.console.log("Caught a [bold red]SIGINT[/], tearing down pending tasks...")

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

    def get_file_info(self, dl: str) -> File:
        f = self.File(dl)

        if not hasattr(f, "filename"):
            # no file extension and/or prefix, probably an article
            raise ParseError(f"Could not parse input '{dl}' as a file")

        return f

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
        file = self.get_file_info(dl)

        # check if the destination file already exists; don't overwrite unless the user says
        if file.dest.is_file() and file.dest.stat().st_size != 0 and not self.force_redownload:
            msg = f"[{file.dest}] File already exists; skipping download"
            raise FileExistsError(msg)

        return file

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

        # Setup auto-retry, as we're very likely to hit 429 on the way...
        # c.f., https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
        # NOTE: We *could* also add a rate limiter on *our* end here, if need be,
        #       c.f., https://niquests.readthedocs.io/en/latest/user/advanced.html#rate-limiting
        retry = niquests.RetryConfiguration(
            total=10,
            # NOTE: CommonsDownloadTool also attempts to retry on 403, which is... weird?
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=5,
            backoff_max=30,
            backoff_jitter=0.5,
            respect_retry_after_header=True,
        )

        # Setup our UA
        ua = {"user-agent": USER_AGENT}

        # NOTE: There's currently only a single A record for upload.wikimedia.org,
        #       so, no need for happy eyeballs.
        # NOTE: We *would* use base_url here, but for the fact that we actually support passing full URLs ;).
        # FIXME: Reimplement authentification (c.f., mwclient site_login)
        with (
            niquests.Session(multiplexed=True, retries=retry, headers=ua) as s,
            self.overall_progress_bar() as overall_progress,
            self.progress_bar() as progress,
        ):
            overall_task = overall_progress.add_task("Download...", total=batch_size(self.input))
            for batch in batched(batch_files(self.input), self.concurrency):
                # Abort early w/o inflating the error count if we caught a SIGINT
                if self.done_event.is_set():
                    break

                downloads = {line: self.query_filename(line, filename) for line, filename in batch}
                responses = {file: s.get(file.url, stream=True) for file in downloads.values() if file}
                # Start tasks ASAP so we get an accurate elapsed time
                tasks = {
                    file: progress.add_task("download", filename=str(file.dest), total=None)
                    for file in downloads.values()
                    if file and file.dest
                }

                # Advance progress bar for lines where query_filename failed
                overall_progress.advance(overall_task, advance=len(downloads) - len(responses))

                s.gather(*responses.values())
                for file, r in responses.items():
                    task = tasks[file]
                    try:
                        self.process_response(r, file, progress, task)
                    finally:
                        r.close()
                        # NOTE: A single Progress instance will only ever show as much tasks as the terminal height allows...
                        #       Drop completed tasks to free up space.
                        progress.remove_task(task)
                        overall_progress.advance(overall_task)

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

    def query_filename(self, line_num: int, line: str) -> File | None:
        """Prepare a download and query Commons for the canonical download URL

        Returns a File instance on success or None on failure.
        """

        logger.info("Processing '%s' at line %i", line, line_num)
        try:
            file = self.prep_download(line)
        except ParseError as e:
            logger.error("%s (line %i)", str(e), line_num)
            self.increment_errors()
            file = None
        except FileExistsError as e:
            logger.warning(e)
            self.increment_warnings()
            file = None

        return file

    def process_response(self, r: niquests.Response, f: File, progress: Progress, task: TaskID) -> None:
        """Fetch file information and contents if the file exists and save it to disk."""

        filename = f.filename
        # prepend the current filename to all log messages
        adapter = FileLogAdapter(logger, {"filename": filename})

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
            progress.console.log(
                f"[bold red]FAILED[/] to download [bold magenta]{filename}[/] ([bold yellow]{r.status_code}[/])"
            )
            return

        dest = f.dest
        file_url = f.url
        file_size = int(r.headers.get("content-length", 0))  # int(str(r.oheaders.content_length))
        # Poor man's hash check, MD5
        file_hash = str(r.oheaders.etag)

        filename_log = f"Downloading '{filename}' ({file_size} bytes)"
        if self.output:
            filename_log += f" to '{dest}'"
        adapter.info(filename_log)
        adapter.info(f"{file_url}")

        if self.dry_run:
            adapter.warning("Dry run; download skipped")
            return

        try:
            progress.update(task, total=file_size)
            with dest.open("wb") as fd:
                for chunk in r.iter_content():
                    fd.write(chunk)
                    if r.download_progress:
                        progress.update(task, completed=r.download_progress.total)
                    else:
                        progress.update(task, advance=len(chunk))

                    # Clean up on SIGINT, so we don't leave incomplete files around
                    if self.done_event.is_set():
                        adapter.error("Caught a SIGINT, aborting")
                        dest.unlink()
                        progress.console.log(f"[bold red]Aborted[/] [bold magenta]{filename}[/] download")
                        return

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
            self.increment_errors()
            progress.console.log(f"[bold red]FAILED[/] to write local file for [bold magenta]{filename}[/]")
            return

        # Verify file integrity and log the details
        try:
            dl_hash = self.validate.hash(dest)
        except OSError as e:
            adapter.error(f"File downloaded but could not be verified: {e}")
            dest.unlink()
            self.increment_errors()
            progress.console.log(f"[bold red]FAILED[/] to verify downloaded [bold magenta]{filename}[/]")
            return

        adapter.info(f"Remote file hash is {file_hash}")
        adapter.info(f"Local file hash is {dl_hash}")
        if dl_hash == file_hash:
            adapter.info("Hashes match!")
            # at this point, we've successfully downloaded the file
            success_log = f"'{filename}' downloaded"
            if self.output:
                success_log += f" to '{dest}'"
            adapter.info(success_log)
        else:
            adapter.error("Hash mismatch! Downloaded file may be corrupt.")
            dest.unlink()
            self.increment_errors()
            progress.console.log(f"[bold red]CORRUPT[/] download for [bold magenta]{filename}[/]")

        return
