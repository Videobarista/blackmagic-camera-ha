# Contributing

Thanks for helping to improve this integration! Bug reports, camera test
results and pull requests are all welcome.

## Reporting bugs and requesting features

- Open an [issue](https://github.com/Videobarista/blackmagic-camera-ha/issues)
  with your camera model, firmware version and integration version.
- Add the integration's diagnostics (**Settings → Devices & services →
  Blackmagic Camera → ⋮ → Download diagnostics**) and the relevant log lines.
  Diagnostics redact credentials, stream keys, the camera name and media
  volume names; please check logs for personal data before posting them.
- Security problems are **not** reported in public issues: see
  [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make your change, with tests (see the test policy below).
3. Run the checks locally (see below) and open a pull request against `main`.
4. All CI checks must pass: Ruff, Hassfest, HACS validation, Tests and CodeQL.

The maintainer reviews pull requests and may ask for changes before merging.

### Requirements

- **Tests**: new functionality comes with tests in `tests/`, and every bug fix
  comes with a test that fails without the fix. The config flow stays fully
  covered, in line with the Home Assistant quality scale (Bronze).
- **Style**: code passes `ruff check` and `ruff format` with the settings in
  `ruff.toml` (including the security rules).
- **Language**: code, comments, documentation and commit messages in English.
- **No new dependencies** unless there is no reasonable alternative; the
  integration only uses what Home Assistant ships.
- **Untrusted input**: treat everything the camera sends as untrusted, never
  log credentials, and keep personal data out of test fixtures. Code that
  parses camera answers or user input belongs in `parsing.py`, which is
  covered by the fuzz target in `tests/fuzz/`.

## Running the checks locally

```bash
pip install -r requirements_test.txt -r requirements_lint.txt
ruff check .
ruff format --check .
pytest
```

Coverage guided fuzzing of the parsers with Atheris (Python 3.11 to 3.13 at
the time of writing; nothing else needs to be installed):

```bash
pip install atheris
python tests/fuzz/fuzz_camera_data.py -max_total_time=300
```
