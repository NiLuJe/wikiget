# wikiget

[![Made with Python](https://img.shields.io/badge/made_with-python-3776AB?logo=python)][python]
[![GitHub Actions](https://img.shields.io/github/actions/workflow/status/NiLuJe/wikiget/python.yml?logo=github)][action]
[![Codecov coverage](https://img.shields.io/codecov/c/gh/NiLuJe/wikiget)][codecov]
[![PyPI version](https://img.shields.io/pypi/v/wikiget)][pypi]
[![PyPI license](https://img.shields.io/pypi/l/wikiget)][license]

Something like [wget] for downloading a file from Wikimedia Commons using only the file name.

## Installation

Requires Python 3.14+ and uv. Install the latest version with:

```bash
uv pip install "git+https://github.com/NiLuJe/wikiget@commons-only"
```

## Usage

`wikiget [-h] [-V] [-q | -v] [-f] [-l LOGFILE] [-j CONCURRENCY] [-b] [-o OUTPUT] [-n] [-r RPS] [-a AUTH] INPUT`

The only required parameter is `FILE`, which is the file you want to download. It should be the name of the file on
the wiki, including the namespace prefix. For a `FILE` in the form `File:Example.jpg` or `Image:Example.jpg`,
it will be fetched from "commons.wikimedia.org".

More detailed information, such as the site used and full URL of the file, can be displayed with `-v` or `--verbose`.
Use `-vv` to display even more detail, mainly debugging information. `-q` can be used to silence
warnings.  A logfile can be specified with `-l` or `--logfile`. If this option is present, the logfile will contain the
same information as `-vv` along with timestamps. New log entries will be appended to an existing logfile.

By default, the program won't overwrite existing files with the same name as the target, but this can be forced with
`-f` or `--force`.

Files can be batch downloaded with the `-b` or `--batch` flag. In this mode, `FILE` will be treated as an input file
containing multiple files to download, one filename or URL per line. Blank lines and lines starting with "#" are
ignored. For large batches, the process can be sped up by downloading files in parallel.
The number of parallel downloads can be set with `-j`. By default, wikiget will download three files at once,
to comply with current (as of writing this) Wikimedia rate limits.
Speaking of rate limits, you can tweak (`-r`) or disable (`--no-rps`) the defaults,
which may be useful for authenticated clients (via an OAuth2 bearer token,
passed via `-a`, or, better yet, an `auth_token` key-value pair in a local `config.toml` placed in the CWD).
Ideally, this should be paired with actual bandwidth throttling, e.g., via `tc`/`tcconfig`, or `trickle`,
in order to honor WM's current robot policies.
In batch mode, `-o` can be used to point to a *folder* where downloads will be stored
(if not in batch mode, it can be used to set the path of the output *file*).

### Example usage

```bash
wikiget File:Example.jpg
```

## Contributing

Pull requests, bug reports, or feature requests are more than welcome.

See [CONTRIBUTING] for more info.

## License

Copyright (C) 2018-2023 Cody Logan and contributors

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see
<https://www.gnu.org/licenses/>.

[wget]: https://www.gnu.org/software/wget/
[github]: https://github.com/NiLuJe/wikiget/
[homebrew]: https://brew.sh/
[python]: https://www.python.org/
[action]: https://github.com/NiLuJe/wikiget/actions/workflows/python.yml
[codecov]: https://app.codecov.io/gh/NiLuJe/wikiget/
[pypi]: https://pypi.org/project/wikiget/
[license]: https://github.com/NiLuJe/wikiget/blob/master/LICENSE
[contributing]: CONTRIBUTING.md
