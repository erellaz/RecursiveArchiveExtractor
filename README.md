<p align="center">
  <img src="icon.png" width="120" alt="Recursive Archive Extractor icon">
</p>

<h1 align="center">Recursive Archive Extractor</h1>

<p align="center">
  A small Windows-friendly Tkinter GUI that recursively finds archives in a folder tree<br>
  and extracts them all in parallel.
</p>

---

## Features

- **Recursive search** — scans a selected folder and all its subfolders for archives.
- **Parallel extraction** — uses a process pool to extract multiple archives at once, using all available CPU cores.
- **Two extraction modes**
  - **Default:** every archive is extracted into a single `Unzipped` folder created inside the selected directory.
  - **Unzip in place:** each archive is extracted next to itself, in its own folder.
- **Live console panel** — shows which archive is currently being extracted, and whether each one finished or failed, right inside the window (no DOS console needed).
- **Optional log file** — a checkbox saves everything shown in the console to a timestamped text file in the selected folder.
- **Help menu** — built-in "How to Use" and "Adding RAR Support" instructions for end users.
- **Color-coded status** — the status line turns blue while running, green on success, orange when nothing was found, and red on error.

## Supported archive formats

| Format | Library used |
|---|---|
| `.7z` | [py7zr](https://pypi.org/project/py7zr/) |
| `.zip` | Python's built-in `zipfile` |
| `.rar` | [rarfile](https://pypi.org/project/rarfile/) (requires the external `unrar` tool — see below) |
| `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, `.txz` | Python's built-in `tarfile` |

## Requirements

- Python 3.9+
- Dependencies from [`requirements.txt`](requirements.txt):
  ```
  py7zr
  rarfile
  ```
- For `.rar` support, the `unrar` command-line tool must also be installed and on your `PATH`:
  - **Windows:** download UnRAR from https://www.rarlab.com/rar_add.htm
  - **Linux:** `sudo apt install unrar`
  - **macOS:** `brew install unrar`

  (The app's **Help → Adding RAR Support** menu shows these same steps.)

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate      # on Windows
pip install -r requirements.txt
python Recursive_unzip.py
```

### Using the app

1. Click **Browse** and select the folder you want to scan for archives.
2. *(Optional)* Tick **Unzip in place** to extract each archive next to itself instead of into a single `Unzipped` folder.
3. *(Optional)* Tick **Save console output to a log file** to keep a text record of the run.
4. Click **Execute Recursive Extraction**.
5. Watch the console panel for per-archive progress, and the status line for the final result.

## Building a standalone executable

The project includes a ready-made [`RecursiveArchiveExtractor.spec`](RecursiveArchiveExtractor.spec) file for [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller RecursiveArchiveExtractor.spec
```

Or build from scratch with an equivalent command:

```bash
pyinstaller --onefile --windowed --name RecursiveArchiveExtractor --copy-metadata py7zr --icon=icon.ico Recursive_unzip.py
```

Notes:
- `--windowed` (a.k.a. `--noconsole`) hides the DOS console window — the app's built-in console panel replaces it.
- `--copy-metadata py7zr` is required: `py7zr` reads its own package version via `importlib.metadata` at runtime, and without this flag the frozen `.exe` raises a `PackageNotFoundError` the first time it touches a `.7z` file.
- The built executable will be at `dist\RecursiveArchiveExtractor.exe`.
- RAR support still depends on the external `unrar` tool being available on the machine running the `.exe` (it isn't bundled).

## Project structure

```
Recursive_unzip.py              # main application
RecursiveArchiveExtractor.spec  # PyInstaller build spec
requirements.txt                # Python dependencies
icon.ico / icon.png             # application icon
```
