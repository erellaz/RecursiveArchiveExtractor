"""
Recursive Archive Extractor
============================
A tkinter GUI application that recursively searches a selected directory
for archive files and extracts them in parallel.

Supported archive formats:
    - .7z   (via py7zr)
    - .zip  (via Python's built-in zipfile module)
    - .rar  (via rarfile — requires 'unrar' CLI tool installed on the system)
    - .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz  (via Python's built-in tarfile module)

Extraction modes:
    - Default:        All archives are extracted into a single "Unzipped" folder
                      inside the selected root directory.
    - Unzip in place: Each archive is extracted into the same directory where
                      the archive file itself resides.

A built-in console panel (below the controls) shows which archive is
currently being extracted, so the app can be compiled with PyInstaller's
"no console" option without losing visibility into progress. An optional
checkbox saves everything shown in the console to a log file.

Dependencies:
    pip install py7zr rarfile

    For .rar support, the 'unrar' command-line tool must also be installed:
        - Windows: download UnRAR from https://www.rarlab.com/rar_add.htm
        - Linux:   sudo apt install unrar
        - macOS:   brew install unrar
"""

import os
import sys

# When built with PyInstaller's "no console" option, sys.stdout/stderr are
# None on Windows. Any leftover print() call would then raise an
# AttributeError and crash the app, so redirect them to a no-op stream.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Console codepages (e.g. Windows cp1252) can't encode every character a
# file path might contain. Without this, print() raises UnicodeEncodeError
# on such a path, which gets caught by extract_archive's broad except and
# misreported as an extraction failure even though extraction succeeded.
# Replacing unencodable characters instead of raising keeps that from
# ever taking down (or false-failing) a real extraction.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from tkinter import messagebox
import zipfile
import tarfile
import py7zr
import rarfile
import concurrent.futures
import glob
from pathlib import Path
import itertools
import threading
import queue
import datetime
from multiprocessing import freeze_support, Manager


# ---------------------------------------------------------------------------
# Supported archive extensions
# ---------------------------------------------------------------------------
# Each glob pattern is used to recursively find matching files.
# We keep them in a list so it's easy to add more formats later.
ARCHIVE_GLOB_PATTERNS = [
    "**/*.7z",
    "**/*.zip",
    "**/*.rar",
    "**/*.tar",
    "**/*.tar.gz",
    "**/*.tgz",
    "**/*.tar.bz2",
    "**/*.tbz2",
    "**/*.tar.xz",
    "**/*.txz",
]


def extract_archive(file_path, output_dir, progress_queue=None):
    """
    Extract a single archive file to the specified output directory.

    The correct extraction library is chosen based on the file extension:
        - .7z            → py7zr
        - .zip           → zipfile
        - .rar           → rarfile
        - .tar(.gz|.bz2|.xz) / .tgz / .tbz2 / .txz  → tarfile

    Parameters
    ----------
    file_path : str or Path
        Full path to the archive file to extract.
    output_dir : str or Path
        Directory where the archive contents will be placed.
    progress_queue : multiprocessing.Queue, optional
        If provided, status messages are pushed here so the GUI's console
        can show which archive is currently being processed.
    """
    if progress_queue is not None:
        progress_queue.put(("start", str(file_path)))

    try:
        # Ensure the output directory exists before attempting extraction.
        os.makedirs(output_dir, exist_ok=True)

        # Convert to a lowercase string for reliable extension matching.
        file_lower = str(file_path).lower()

        # --- 7-Zip archives ---
        if file_lower.endswith(".7z"):
            with py7zr.SevenZipFile(str(file_path), mode='r') as archive:
                archive.extractall(path=str(output_dir))

        # --- Standard ZIP archives ---
        elif file_lower.endswith(".zip"):
            with zipfile.ZipFile(str(file_path), 'r') as archive:
                archive.extractall(path=str(output_dir))

        # --- RAR archives ---
        elif file_lower.endswith(".rar"):
            with rarfile.RarFile(str(file_path), 'r') as archive:
                archive.extractall(path=str(output_dir))

        # --- Tar-based archives (.tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz) ---
        elif (
            file_lower.endswith(".tar")
            or file_lower.endswith(".tar.gz")
            or file_lower.endswith(".tgz")
            or file_lower.endswith(".tar.bz2")
            or file_lower.endswith(".tbz2")
            or file_lower.endswith(".tar.xz")
            or file_lower.endswith(".txz")
        ):
            # tarfile.open auto-detects the compression method when mode='r:*'
            with tarfile.open(str(file_path), mode='r:*') as archive:
                archive.extractall(path=str(output_dir))

        else:
            # If we reach here, the file matched a glob pattern but wasn't
            # handled above — this shouldn't happen unless ARCHIVE_GLOB_PATTERNS
            # is expanded without updating this function.
            print(f"Unsupported archive format: {file_path}")
            if progress_queue is not None:
                progress_queue.put(("error", str(file_path), "Unsupported archive format"))
            return

        print(f"Successfully extracted: {file_path} -> {output_dir}")
        if progress_queue is not None:
            progress_queue.put(("done", str(file_path), str(output_dir)))

    except Exception as e:
        print(f"Error extracting {file_path}: {e}")
        if progress_queue is not None:
            progress_queue.put(("error", str(file_path), str(e)))


def find_all_archives(root_directory):
    """
    Recursively search for all supported archive files under root_directory.

    Parameters
    ----------
    root_directory : str or Path
        The top-level directory to search.

    Returns
    -------
    list[str]
        A deduplicated, sorted list of absolute paths to archive files.
    """
    found_files = set()  # Use a set to avoid duplicates across patterns.

    for pattern in ARCHIVE_GLOB_PATTERNS:
        search_pattern = os.path.join(str(root_directory), pattern)
        matches = glob.glob(search_pattern, recursive=True)
        found_files.update(matches)

    return sorted(found_files)


def compute_output_dir(archive_path, root_output_dir, unzip_in_place):
    """
    Determine where a given archive should be extracted.

    Parameters
    ----------
    archive_path : str
        Path to the archive file.
    root_output_dir : str
        The central "Unzipped" directory (used when unzip_in_place is False).
    unzip_in_place : bool
        If True, extract into the same directory as the archive.
        If False, extract into root_output_dir.

    Returns
    -------
    str
        The output directory path for this archive.
    """
    if unzip_in_place:
        # Extract right next to the archive file itself.
        return os.path.dirname(archive_path)
    else:
        # Extract into the central "Unzipped" directory.
        return root_output_dir


def parallel_extract_archives(root_directory, root_output_dir, unzip_in_place, progress_queue=None):
    """
    Find all supported archive files under root_directory and extract them
    in parallel using a process pool.

    Parameters
    ----------
    root_directory : str or Path
        The top-level directory to search for archives.
    root_output_dir : str
        The central output directory (used when unzip_in_place is False).
    unzip_in_place : bool
        If True, each archive is extracted beside its source file.
    progress_queue : multiprocessing.Queue, optional
        If provided, forwarded to each worker so it can report progress.

    Returns
    -------
    int
        The number of archive files found and queued for extraction.
    """
    files_to_extract = find_all_archives(root_directory)

    if not files_to_extract:
        print(f"No supported archive files found in {root_directory}")
        return 0

    print(f"Found {len(files_to_extract)} archive(s). Starting parallel extraction...")
    print("Files:", files_to_extract)
    if progress_queue is not None:
        progress_queue.put(("log", f"Found {len(files_to_extract)} archive(s). Starting parallel extraction..."))

    # Build a list of per-file output directories so each worker knows
    # exactly where to extract its archive.
    output_dirs = [
        compute_output_dir(f, root_output_dir, unzip_in_place)
        for f in files_to_extract
    ]

    # Use ProcessPoolExecutor to extract archives in parallel across CPU cores.
    with concurrent.futures.ProcessPoolExecutor() as executor:
        list(
            executor.map(
                extract_archive,
                files_to_extract,
                output_dirs,
                itertools.repeat(progress_queue),
            )
        )

    return len(files_to_extract)


def process_directory(directory_path, output_dir, unzip_in_place, progress_queue):
    """
    Entry point for the extraction workflow.
    Validates the selected directory, launches parallel extraction,
    and reports progress/result via the progress queue.

    Parameters
    ----------
    directory_path : str or Path
        The user-selected root directory to scan.
    output_dir : str
        Path to the central "Unzipped" folder.
    unzip_in_place : bool
        Whether to extract archives beside their source files.
    progress_queue : multiprocessing.Queue
        Queue used to report progress and the final status back to the GUI.
    """
    if not directory_path:
        print("Error: No directory selected.")
        progress_queue.put(("status", "Error: No directory selected.", "red"))
        return

    print(f"--- Executing recursive extraction on directory: {directory_path} ---")
    mode_text = "in place" if unzip_in_place else f"to {output_dir}"
    print(f"Extraction mode: {mode_text}")
    progress_queue.put(("log", f"Executing recursive extraction on: {directory_path}"))
    progress_queue.put(("log", f"Extraction mode: {mode_text}"))

    try:
        count = parallel_extract_archives(directory_path, output_dir, unzip_in_place, progress_queue)

        if count == 0:
            progress_queue.put((
                "status",
                "No archive files found in the selected directory.",
                "orange",
            ))
        else:
            print("All extraction tasks finished.")
            progress_queue.put(("log", "All extraction tasks finished."))
            progress_queue.put((
                "status",
                f"Extraction completed — {count} archive(s) processed.",
                "green",
            ))

    except OSError as e:
        print(f"Error accessing directory: {e}")
        progress_queue.put(("status", f"Error accessing directory: {e}", "red"))


def run_extraction(directory_path, output_dir, unzip_in_place, progress_queue):
    """
    Thread target that runs process_directory and guarantees a final
    status message is always sent, even on an unexpected error, so the
    GUI never gets stuck with the Execute button disabled.
    """
    try:
        process_directory(directory_path, output_dir, unzip_in_place, progress_queue)
    except Exception as e:
        print(f"Unexpected error: {e}")
        progress_queue.put(("status", f"Unexpected error: {e}", "red"))


def main():
    """
    Build and launch the tkinter GUI.

    Layout:
        Row 0 — Directory selector: label, text entry, and Browse button.
        Row 1 — "Unzip in place" checkbox.
        Row 2 — Execute button.
        Row 3 — Status label (shows results or errors).
        Row 4 — Console panel showing per-archive progress, plus the
                "save to log file" checkbox.
    """
    root = tk.Tk()
    root.title("Recursive Archive Extractor")
    # Taller than the original to accommodate the console panel.
    root.geometry("560x430")
    root.resizable(False, False)

    # --- Menu bar: Help ---
    def show_usage_help():
        messagebox.showinfo(
            "How to Use",
            "1. Click 'Browse' and select the folder you want to scan for archives.\n\n"
            "2. (Optional) Tick 'Unzip in place' to extract each archive next to "
            "itself instead of into a single 'Unzipped' folder created in the "
            "selected directory.\n\n"
            "3. (Optional) Tick 'Save console output to a log file' to keep a "
            "text record of the run, saved into the selected folder.\n\n"
            "4. Click 'Execute Recursive Extraction'.\n\n"
            "The console below shows which archive is currently being "
            "extracted. The status line above it turns green when finished, "
            "orange if no archives were found, or red if an error occurred.\n\n"
            "Supported formats: .7z, .zip, .rar, .tar, .tar.gz/.tgz, "
            ".tar.bz2/.tbz2, .tar.xz/.txz."
        )

    def show_rar_help():
        messagebox.showinfo(
            "Adding RAR Support",
            "The Python side of RAR support is already built in — you just "
            "need the free 'UnRAR' command-line tool installed on your "
            "system:\n\n"
            "Windows:\n"
            "  1. Download UnRAR from https://www.rarlab.com/rar_add.htm\n"
            "  2. Install it, or copy the extracted UnRAR.exe next to this "
            "program's .exe file.\n"
            "  3. Restart this app.\n\n"
            "Linux:\n"
            "  sudo apt install unrar\n\n"
            "macOS:\n"
            "  brew install unrar\n\n"
            "Once installed, .rar files found under the selected folder are "
            "extracted automatically — no other setup needed."
        )

    def show_about():
        messagebox.showinfo(
            "About",
            "Recursive Archive Extractor\n\n"
            "Recursively finds and extracts .7z, .zip, .rar and tar-family "
            "archives in a folder, in parallel."
        )

    menubar = tk.Menu(root)
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(label="How to Use", command=show_usage_help)
    help_menu.add_command(label="Adding RAR Support", command=show_rar_help)
    help_menu.add_separator()
    help_menu.add_command(label="About", command=show_about)
    menubar.add_cascade(label="Help", menu=help_menu)
    root.config(menu=menubar)

    # StringVar bound to the directory entry field.
    directory_path_var = tk.StringVar()

    # BooleanVar bound to the "Unzip in place" checkbox (unchecked by default).
    unzip_in_place_var = tk.BooleanVar(value=False)

    # BooleanVar bound to the "Save console output to a log file" checkbox.
    save_log_var = tk.BooleanVar(value=False)

    # Queue used by worker processes/threads to report progress back to the GUI.
    # A Manager queue is required (rather than a plain multiprocessing.Queue)
    # because ProcessPoolExecutor.map pickles arguments to send to its
    # already-running worker processes, and a raw Queue can only be shared
    # through inheritance at process-creation time, not via pickling.
    queue_manager = Manager()
    progress_queue = queue_manager.Queue()

    # Holds the currently open log file handle, if logging is enabled for
    # this run. A plain dict is used so the nested functions below can
    # mutate it via closure.
    log_state = {"handle": None}

    def select_directory():
        """Open a folder-selection dialog and store the chosen path."""
        folder_selected = filedialog.askdirectory()

        if folder_selected:
            directory_path_var.set(folder_selected)

            status_label.config(
                text="Directory selected. Ready to run.",
                foreground="black"
            )

    def append_console(text, tag="info"):
        """Append a line to the console panel and, if enabled, the log file."""
        console_text.config(state="normal")
        console_text.insert(tk.END, text + "\n", tag)
        console_text.see(tk.END)
        console_text.config(state="disabled")

        if log_state["handle"] is not None:
            try:
                log_state["handle"].write(text + "\n")
                log_state["handle"].flush()
            except OSError:
                pass

    def poll_progress_queue():
        """Drain the progress queue and update the console/status label.

        Runs on the main thread via root.after, so it's the only place
        that touches tkinter widgets in response to background work.
        """
        try:
            while True:
                message = progress_queue.get_nowait()
                msg_type = message[0]

                if msg_type == "start":
                    append_console(f"Extracting: {message[1]}", "progress")
                elif msg_type == "done":
                    append_console(f"Done: {message[1]} -> {message[2]}", "success")
                elif msg_type == "error":
                    append_console(f"Error: {message[1]}: {message[2]}", "error")
                elif msg_type == "log":
                    append_console(message[1], "info")
                elif msg_type == "status":
                    _, text, color = message
                    status_label.config(text=text, foreground=color)
                    execute_button.config(state="normal")
                    if log_state["handle"] is not None:
                        try:
                            log_state["handle"].close()
                        except OSError:
                            pass
                        log_state["handle"] = None
        except queue.Empty:
            pass
        finally:
            root.after(100, poll_progress_queue)

    def execute_button_command():
        """
        Called when the user clicks 'Execute Recursive Extraction'.
        Reads the current directory path and checkbox state, resets the
        console, optionally opens a log file, then kicks off the
        extraction process on a background thread.
        """
        selected_directory = Path(directory_path_var.get())

        # The central output directory — only used when "Unzip in place" is off.
        output_dir = os.path.join(selected_directory, "Unzipped")

        # Read the checkbox state.
        unzip_in_place = unzip_in_place_var.get()

        # Clear the console for the new run.
        console_text.config(state="normal")
        console_text.delete("1.0", tk.END)
        console_text.config(state="disabled")

        if save_log_var.get():
            try:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                log_path = os.path.join(str(selected_directory), f"unzip_log_{timestamp}.txt")
                log_state["handle"] = open(log_path, "w", encoding="utf-8")
                append_console(f"Logging to: {log_path}", "info")
            except OSError as e:
                log_state["handle"] = None
                append_console(f"Could not create log file: {e}", "error")

        # Signal that extraction is running before the blocking work starts.
        status_label.config(text="Extracting, please wait…", foreground="blue")
        execute_button.config(state="disabled")

        thread = threading.Thread(
            target=run_extraction,
            args=(selected_directory, output_dir, unzip_in_place, progress_queue),
            daemon=True,
        )
        thread.start()

    # -----------------------------------------------------------------------
    # GUI layout
    # -----------------------------------------------------------------------

    # Main frame with padding for the directory selector row.
    frame = ttk.Frame(root, padding="10")
    frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

    # --- Row 0: Directory selector ---
    label = ttk.Label(frame, text="Selected Directory:")
    label.grid(row=0, column=0, sticky=tk.W, pady=(0, 5))

    directory_entry = ttk.Entry(
        frame,
        textvariable=directory_path_var,
        width=50
    )
    directory_entry.grid(
        row=0,
        column=1,
        sticky=(tk.W, tk.E),
        pady=(0, 5),
        padx=(0, 5)
    )

    browse_button = ttk.Button(
        frame,
        text="Browse",
        command=select_directory
    )
    browse_button.grid(row=0, column=2, sticky=tk.W, pady=(0, 5))

    # --- Row 1: "Unzip in place" checkbox ---
    # When checked, each archive is extracted into the same directory where
    # the archive resides, instead of into a central "Unzipped" folder.
    unzip_in_place_check = ttk.Checkbutton(
        frame,
        text="Unzip in place (extract beside each archive file)",
        variable=unzip_in_place_var,
    )
    unzip_in_place_check.grid(row=1, column=1, sticky=tk.W, pady=(5, 0))

    # --- Row 2: Execute button ---
    execute_button = ttk.Button(
        frame,
        text="Execute Recursive Extraction",
        command=execute_button_command
    )
    execute_button.grid(row=2, column=1, pady=(10, 0))

    # --- Row 3: Status label ---
    status_label = ttk.Label(root, text="", foreground="black")
    status_label.grid(row=2, column=0, pady=(10, 0))

    # --- Row 4: Console panel + "save log" checkbox ---
    console_frame = ttk.Frame(root, padding="10")
    console_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

    console_label = ttk.Label(console_frame, text="Console:")
    console_label.grid(row=0, column=0, sticky=tk.W)

    console_text = tk.Text(
        console_frame,
        height=12,
        width=68,
        state="disabled",
        wrap="word",
        font=("Consolas", 9),
        background="white",
    )
    console_text.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

    console_scrollbar = ttk.Scrollbar(console_frame, orient="vertical", command=console_text.yview)
    console_scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))
    console_text.config(yscrollcommand=console_scrollbar.set)

    # Tag colors mirror the status label's color scheme.
    console_text.tag_config("info", foreground="black")
    console_text.tag_config("progress", foreground="#0057b8")
    console_text.tag_config("success", foreground="green")
    console_text.tag_config("error", foreground="red")

    save_log_check = ttk.Checkbutton(
        console_frame,
        text="Save console output to a log file",
        variable=save_log_var,
    )
    save_log_check.grid(row=2, column=0, sticky=tk.W, pady=(5, 0))

    # Start polling the progress queue so console/status updates flow in
    # as background workers report them.
    root.after(100, poll_progress_queue)

    # Start the tkinter event loop.
    root.mainloop()


if __name__ == "__main__":
    # freeze_support() is required for ProcessPoolExecutor to work correctly
    # when the script is packaged into a frozen executable (e.g. via PyInstaller).
    freeze_support()
    main()