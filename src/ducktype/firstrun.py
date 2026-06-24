"""First-run data-folder bootstrap and data migration (Windows, ctypes only).

On the first launch of a packaged build the user must choose a folder where all
DuckType data (database, config, logs, native hook copies, phrases) is stored.
The choice is recorded by ``paths.write_pointer``. If an earlier version already
left data in the default location, it is migrated into the chosen folder
(copy -> verify -> delete source) behind a progress bar.

Everything here uses only ctypes + the Win32/Shell APIs so the packaged exe
needs no extra dependency (tkinter is intentionally excluded from the bundle).
All native calls degrade gracefully: if a fancy dialog can't be created we fall
back to a plain message box, and migration still works without a progress
window.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import paths

log = logging.getLogger("ducktype")

# Files that make up a data root, in copy order (db first so its bytes dominate
# the progress bar). Sidecar WAL/SHM travel with the db when copied as files.
_DB_FILES = ("ducktype.db", "ducktype.db-wal", "ducktype.db-shm")
_OTHER_FILES = ("config.json", "phrases.txt")
_LOG_FILE = "ducktype.log"

# Marker written into a new root by the dashboard "relocate" flow so the next
# startup can finish a copy-verify-delete across the restart boundary.
CLEANUP_MARKER = ".cleanup_old"


# ===========================================================================
# Migration core (shared with the dashboard relocate flow)
# ===========================================================================
def plan_files(src_root: Path, dst_root: Path, include_db: bool = True,
               include_log: bool = True) -> List[Tuple[Path, Path]]:
    """List (src, dst) pairs for every data file present under ``src_root``.

    ``include_db`` is False when the caller copies the live database separately
    (via the online backup API); ``include_log`` is False when the log file is
    held open by a running instance and can't be reliably copied.
    """
    names = (list(_DB_FILES) if include_db else []) + list(_OTHER_FILES)
    if include_log:
        names.append(_LOG_FILE)
    plan: List[Tuple[Path, Path]] = []
    for name in names:
        s = src_root / name
        if s.exists():
            plan.append((s, dst_root / name))
    native = src_root / "native"
    if native.is_dir():
        for dll in native.glob("ducktype_hook_*.dll"):
            plan.append((dll, dst_root / "native" / dll.name))
    return plan


def _total_bytes(plan: List[Tuple[Path, Path]]) -> int:
    total = 0
    for s, _ in plan:
        try:
            total += s.stat().st_size
        except OSError:
            pass
    return total


def copy_files(plan: List[Tuple[Path, Path]],
               on_progress: Optional[Callable[[int, int], None]] = None) -> int:
    """Copy each (src, dst) in 1 MB chunks, reporting cumulative bytes.

    Returns the total bytes copied. Destination directories are created."""
    total = _total_bytes(plan)
    done = 0
    if on_progress:
        on_progress(0, total)
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                chunk = fi.read(1 << 20)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        shutil.copystat(src, dst, follow_symlinks=True)
    if on_progress:
        on_progress(total, total)
    return total


def verify_files(plan: List[Tuple[Path, Path]]) -> bool:
    """True if every destination exists with the same size as its source."""
    for src, dst in plan:
        try:
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                return False
        except OSError:
            return False
    return True


def delete_files(paths_: List[Path]) -> None:
    for p in paths_:
        try:
            p.unlink()
        except OSError:
            pass


def _char_count(db_file: Path) -> Optional[int]:
    """char_events row count if ``db_file`` is a readable DuckType DB, else None
    (missing file, not SQLite, or no such table)."""
    if not db_file.exists():
        return None
    import sqlite3
    try:
        con = sqlite3.connect(str(db_file))
        try:
            row = con.execute("SELECT COUNT(*) FROM char_events").fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return None


def _migration_verified(old: Path, new_root: Path) -> bool:
    """True only if the new root holds a complete copy of the old root's data,
    so deleting the old root cannot lose anything.

    The database needs special handling: the dashboard relocate flow writes it
    via SQLite's *online backup*, so the destination ``ducktype.db`` is a fresh,
    fully-checkpointed file whose byte size differs from the source and which has
    no ``-wal`` / ``-shm`` sidecars. A plain size compare (the old behaviour)
    therefore almost always failed, orphaning the old data forever. We accept the
    DB when it is a byte-for-byte copy (raw-file first-run migration) *or* a valid
    DuckType database with at least as many char rows as the source. The remaining
    flat files must still match by size; throwaway WAL/SHM sidecars are ignored.
    """
    old_db, new_db = old / "ducktype.db", new_root / "ducktype.db"
    if not new_db.exists():
        return False
    db_ok = False
    try:
        if old_db.exists() and new_db.stat().st_size == old_db.stat().st_size:
            db_ok = True  # identical bytes (raw copy / first-run migration)
    except OSError:
        pass
    if not db_ok:
        new_n = _char_count(new_db)
        if new_n is None:
            return False  # destination isn't a usable DuckType DB -> keep source
        old_n = _char_count(old_db)
        db_ok = old_n is None or new_n >= old_n
    for name in _OTHER_FILES:                      # config.json, phrases.txt
        s = old / name
        if not s.exists():
            continue
        d = new_root / name
        try:
            if not d.exists() or d.stat().st_size != s.stat().st_size:
                return False
        except OSError:
            return False
    return db_ok


def _delete_root_data(old: Path) -> None:
    """Remove every data file from a (now-migrated) old root, including the DB's
    WAL/SHM sidecars and any hook DLLs, then drop the native dir if it empties."""
    for name in (*_DB_FILES, *_OTHER_FILES, _LOG_FILE):
        try:
            (old / name).unlink()
        except OSError:
            pass
    nd = old / "native"
    if nd.is_dir():
        for dll in nd.glob("ducktype_hook_*.dll"):
            try:
                dll.unlink()
            except OSError:
                pass
        try:
            if not any(nd.iterdir()):
                nd.rmdir()
        except OSError:
            pass


def cleanup_old_root(new_root: Path) -> None:
    """Finish a relocation started in a previous run: if ``new_root`` holds the
    CLEANUP_MARKER pointing at the old root, delete the old root's data once the
    new root is confirmed to hold a complete copy, then drop the marker.

    The marker is only removed when the job is truly finished (old gone, or
    verified-and-deleted). If verification fails -- e.g. the new root sits on a
    drive that wasn't mounted yet -- the marker is *kept* so the next launch
    retries, rather than abandoning the orphaned copy as the old code did."""
    marker = new_root / CLEANUP_MARKER
    if not marker.exists():
        return
    try:
        old = Path(marker.read_text(encoding="utf-8").strip())
    except OSError:
        return
    done = False
    try:
        if not old.exists() or old.resolve() == new_root.resolve():
            done = True  # nothing to clean up
        elif _migration_verified(old, new_root):
            _delete_root_data(old)
            log.info("Cleaned up old data root after relocation: %s", old)
            done = True
        else:
            log.warning(
                "Relocation cleanup deferred: %s not yet a verified copy of %s; "
                "keeping the old data for a retry.", new_root, old)
    finally:
        if done:
            try:
                marker.unlink()
            except OSError:
                pass


# ===========================================================================
# Native dialogs (ctypes)
# ===========================================================================
def _load_duck_hicon() -> Optional[int]:
    try:
        ico = paths.icon_path()
        if not ico.exists():
            return None
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        h = u.LoadImageW(None, str(ico), IMAGE_ICON, 0, 0,
                         LR_LOADFROMFILE | LR_DEFAULTSIZE)
        return int(h) if h else None
    except Exception:
        return None


# ---- TASKDIALOGCONFIG (1-byte packed, per commctrl.h) --------------------
class _TASKDIALOG_BUTTON(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("nButtonID", ctypes.c_int),
                ("pszButtonText", wintypes.LPCWSTR)]


class _TASKDIALOGCONFIG(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwndParent", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", wintypes.UINT),
        ("dwCommonButtons", wintypes.UINT),
        ("pszWindowTitle", wintypes.LPCWSTR),
        ("hMainIcon", ctypes.c_void_p),  # union hMainIcon / pszMainIcon
        ("pszMainInstruction", wintypes.LPCWSTR),
        ("pszContent", wintypes.LPCWSTR),
        ("cButtons", wintypes.UINT),
        ("pButtons", ctypes.c_void_p),
        ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", wintypes.UINT),
        ("pRadioButtons", ctypes.c_void_p),
        ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", wintypes.LPCWSTR),
        ("pszExpandedInformation", wintypes.LPCWSTR),
        ("pszExpandedControlText", wintypes.LPCWSTR),
        ("pszCollapsedControlText", wintypes.LPCWSTR),
        ("hFooterIcon", ctypes.c_void_p),
        ("pszFooter", wintypes.LPCWSTR),
        ("pfCallback", ctypes.c_void_p),
        ("lpCallbackData", ctypes.c_void_p),
        ("cxWidth", wintypes.UINT),
    ]


_TDF_USE_HICON_MAIN = 0x0002
_TDF_USE_COMMAND_LINKS = 0x0010
_TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
_TDF_POSITION_RELATIVE_TO_WINDOW = 0x1000


def _intro_dialog() -> bool:
    """Welcome + ask permission to choose a folder. Returns True to proceed,
    False to quit. Tries a modern task dialog, falls back to MessageBox."""
    title = "码字鸭 DuckType"
    head = "欢迎使用 码字鸭 🦆"
    body = ("首次使用需要选择一个文件夹来保存 DuckType 的所有数据"
            "（输入记录数据库、配置、日志、捕获组件等）。\n\n"
            "之后可以在仪表盘的「数据管理」里随时更改这个位置，"
            "现有数据会一并移动过去。")
    ID_CHOOSE, ID_QUIT = 1001, 1002
    try:
        comctl = ctypes.WinDLL("comctl32", use_last_error=True)
        comctl.TaskDialogIndirect.argtypes = [
            ctypes.POINTER(_TASKDIALOGCONFIG),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(wintypes.BOOL),
        ]
        comctl.TaskDialogIndirect.restype = ctypes.c_long  # HRESULT

        buttons = (_TASKDIALOG_BUTTON * 2)(
            _TASKDIALOG_BUTTON(ID_CHOOSE, "选择数据文件夹\n挑一个目录开始使用"),
            _TASKDIALOG_BUTTON(ID_QUIT, "退出\n暂时不使用"),
        )
        cfg = _TASKDIALOGCONFIG()
        cfg.cbSize = ctypes.sizeof(_TASKDIALOGCONFIG)
        cfg.dwFlags = _TDF_USE_COMMAND_LINKS | _TDF_ALLOW_DIALOG_CANCELLATION
        cfg.pszWindowTitle = title
        cfg.pszMainInstruction = head
        cfg.pszContent = body
        cfg.cButtons = 2
        cfg.pButtons = ctypes.cast(buttons, ctypes.c_void_p)
        cfg.nDefaultButton = ID_CHOOSE
        hicon = _load_duck_hicon()
        if hicon:
            cfg.dwFlags |= _TDF_USE_HICON_MAIN
            cfg.hMainIcon = hicon

        pressed = ctypes.c_int(0)
        hr = comctl.TaskDialogIndirect(ctypes.byref(cfg), ctypes.byref(pressed),
                                       None, None)
        if hr == 0:  # S_OK
            return pressed.value == ID_CHOOSE
    except Exception as exc:
        log.info("TaskDialog unavailable (%s); using MessageBox.", exc)

    # Fallback: classic message box.
    MB_OKCANCEL = 0x00000001
    MB_ICONINFORMATION = 0x00000040
    IDOK = 1
    res = ctypes.windll.user32.MessageBoxW(
        None,
        body + "\n\n点击「确定」选择文件夹，「取消」退出。",
        title, MB_OKCANCEL | MB_ICONINFORMATION)
    return res == IDOK


# IFileOpenDialog through raw COM vtables (no comtypes dependency).
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s: str):
        super().__init__()
        ctypes.oledll.ole32.IIDFromString(s, ctypes.byref(self))


_CLSID_FileOpenDialog = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
_IID_IFileOpenDialog = "{D57C7288-D4AD-4768-BE02-9D969532D960}"
_IID_IShellItem = "{43826D1E-E718-42EE-BC55-A1E261C37BFE}"


def _vtbl_call(ptr, index, restype, argtypes, *args):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    fn_ptr = vtbl[0][index]
    proto = ctypes.WINFUNCTYPE(restype, *argtypes)
    return proto(fn_ptr)(ptr, *args)


def _pick_folder_com(initial: Optional[Path] = None) -> Optional[str]:
    ole32 = ctypes.oledll.ole32
    S_OK = 0
    CLSCTX_INPROC_SERVER = 1
    FOS_PICKFOLDERS = 0x00000020
    FOS_FORCEFILESYSTEM = 0x00000040
    SIGDN_FILESYSPATH = 0x80058000

    # vtable indices for IFileOpenDialog (inherits IModalWindow : IUnknown)
    # IUnknown: 0 QueryInterface, 1 AddRef, 2 Release
    # IModalWindow: 3 Show
    # IFileDialog: 4 SetFileTypes ... 9 SetOptions, 10 GetOptions ...
    #   12 SetFolder, 14 SetFileName, 17 SetTitle, 20 Show? -> use known layout
    IDX_RELEASE = 2
    IDX_SHOW = 3
    IDX_SETOPTIONS = 9
    IDX_GETOPTIONS = 10
    IDX_SETTITLE = 17
    IDX_GETRESULT = 20  # IFileDialog::GetResult

    try:
        ole32.CoInitialize(None)
    except Exception:
        pass
    clsid = _GUID(_CLSID_FileOpenDialog)
    iid = _GUID(_IID_IFileOpenDialog)
    pdlg = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
                                ctypes.byref(iid), ctypes.byref(pdlg))
    if hr != S_OK or not pdlg:
        return None
    try:
        # GetOptions / SetOptions to add the folder-pick flag.
        opts = wintypes.DWORD(0)
        _vtbl_call(pdlg, IDX_GETOPTIONS, ctypes.c_long,
                   [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)],
                   ctypes.byref(opts))
        opts.value |= FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
        _vtbl_call(pdlg, IDX_SETOPTIONS, ctypes.c_long,
                   [ctypes.c_void_p, wintypes.DWORD], opts)
        _vtbl_call(pdlg, IDX_SETTITLE, ctypes.c_long,
                   [ctypes.c_void_p, wintypes.LPCWSTR],
                   "选择 DuckType 数据文件夹")

        hr = _vtbl_call(pdlg, IDX_SHOW, ctypes.c_long,
                        [ctypes.c_void_p, wintypes.HWND], None)
        if hr != S_OK:  # user cancelled (HRESULT 0x800704C7)
            return None

        pitem = ctypes.c_void_p()
        hr = _vtbl_call(pdlg, IDX_GETRESULT, ctypes.c_long,
                        [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                        ctypes.byref(pitem))
        if hr != S_OK or not pitem:
            return None
        try:
            # IShellItem::GetDisplayName is vtable index 5.
            ppath = ctypes.c_wchar_p()
            hr = _vtbl_call(pitem, 5, ctypes.c_long,
                            [ctypes.c_void_p, ctypes.c_ulong,
                             ctypes.POINTER(ctypes.c_wchar_p)],
                            SIGDN_FILESYSPATH, ctypes.byref(ppath))
            if hr != S_OK or not ppath.value:
                return None
            result = ppath.value
            ctypes.windll.ole32.CoTaskMemFree(
                ctypes.cast(ppath, ctypes.c_void_p))
            return result
        finally:
            _vtbl_call(pitem, IDX_RELEASE, ctypes.c_ulong, [ctypes.c_void_p])
    finally:
        _vtbl_call(pdlg, IDX_RELEASE, ctypes.c_ulong, [ctypes.c_void_p])


def pick_folder(initial: Optional[Path] = None) -> Optional[str]:
    """Public wrapper: returns a chosen folder path or None (cancel)."""
    try:
        return _pick_folder_com(initial)
    except Exception:
        log.exception("Folder picker failed")
        return None


# ---- migration progress window (msctls_progress32) ----------------------
class _ProgressWindow:
    """A tiny native window with a label and a determinate progress bar,
    driven from the calling (main) thread while a worker copies files."""
    _WM_PROGRESS = 0x0400 + 1   # WM_APP+1: wParam=permille (0..1000)
    _WM_DONE = 0x0400 + 2

    def __init__(self, title: str, label: str):
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._title = title
        self._label = label
        self._hwnd = None
        self._bar = None
        self._static = None
        self._done = False
        self._wndproc = None

    def __enter__(self):
        try:
            self._create()
        except Exception:
            log.exception("Progress window failed; continuing without it")
            self._hwnd = None
        return self

    def __exit__(self, *exc):
        if self._hwnd:
            try:
                self._u.DestroyWindow(self._hwnd)
            except Exception:
                pass
        self._hwnd = None

    def _create(self):
        u = self._u
        ICC_PROGRESS_CLASS = 0x00000020

        class INITCOMMONCONTROLSEX(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("dwICC", wintypes.DWORD)]
        icc = INITCOMMONCONTROLSEX(ctypes.sizeof(INITCOMMONCONTROLSEX),
                                   ICC_PROGRESS_CLASS)
        ctypes.WinDLL("comctl32").InitCommonControlsEx(ctypes.byref(icc))

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                     wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)

        def proc(hwnd, msg, wp, lp):
            if msg == self._WM_PROGRESS and self._bar:
                # PBM_SETPOS = WM_USER+2 = 0x0402
                u.SendMessageW(self._bar, 0x0402, int(wp), 0)
                return 0
            if msg == 0x0010:  # WM_CLOSE -> ignore (modal during copy)
                return 0
            return u.DefWindowProcW(hwnd, msg, wp, lp)

        self._wndproc = WNDPROC(proc)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]
        hinst = ctypes.WinDLL("kernel32").GetModuleHandleW(None)
        wc = WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hbrBackground = 16  # COLOR_BTNFACE+1
        wc.lpszClassName = "DuckTypeMigrateWnd"
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        u.RegisterClassW(ctypes.byref(wc))

        WS = 0x00C00000 | 0x00080000 | 0x00010000  # CAPTION|SYSMENU? keep simple
        WS_OVERLAPPED = 0x00CF0000 & ~0x00050000   # caption, no min/max/resize
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, wintypes.LPVOID]
        self._hwnd = u.CreateWindowExW(0, "DuckTypeMigrateWnd", self._title,
            WS_OVERLAPPED, 0x80000000, 0x80000000, 440, 150, None, None, hinst, None)
        if not self._hwnd:
            return
        # Center on screen.
        sw = u.GetSystemMetrics(0); sh = u.GetSystemMetrics(1)
        u.SetWindowPos(self._hwnd, None, (sw - 440)//2, (sh - 150)//2, 0, 0, 0x0001)

        WS_CHILD_VIS = 0x40000000 | 0x10000000
        self._static = u.CreateWindowExW(0, "STATIC", self._label, WS_CHILD_VIS,
            20, 18, 400, 40, self._hwnd, None, hinst, None)
        self._bar = u.CreateWindowExW(0, "msctls_progress32", None,
            WS_CHILD_VIS, 20, 70, 400, 24, self._hwnd, None, hinst, None)
        # PBM_SETRANGE32 = WM_USER+6 = 0x0406
        u.SendMessageW(self._bar, 0x0406, 0, 1000)
        u.ShowWindow(self._hwnd, 5)  # SW_SHOW
        u.UpdateWindow(self._hwnd)
        self._pump()

    def _pump(self):
        msg = wintypes.MSG()
        u = self._u
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def set_progress(self, done: int, total: int):
        if not self._hwnd:
            return
        permille = 1000 if total <= 0 else max(0, min(1000, int(done * 1000 / total)))
        self._u.SendMessageW(self._bar, 0x0402, permille, 0)  # PBM_SETPOS
        self._pump()


# ===========================================================================
# Bootstrap entry point
# ===========================================================================
def _legacy_root() -> Optional[Path]:
    """Where an earlier version left data, if any (the default anchor, or a
    custom dir saved in an old config.json's ``data_dir``)."""
    candidates: List[Path] = []
    # An old config that used the DB-relocate feature.
    cfg = paths.data_dir() / "config.json"
    if cfg.exists():
        try:
            import json
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            dd = (raw.get("data_dir") or "").strip()
            if dd:
                candidates.append(Path(dd).expanduser())
        except (ValueError, OSError):
            pass
    candidates.append(paths.data_dir())
    for c in candidates:
        if (c / "ducktype.db").exists():
            return c
    return None


def ensure_data_root() -> Optional[Path]:
    """Resolve the data root for this launch, prompting on first run.

    Returns the chosen root, or None if the user declined (caller should exit).
    Only meant for the GUI app path; callers that don't want a prompt should not
    call this (``paths.root_dir`` falls back to the default anchor).
    """
    # Explicit override (portable installs / power users / CI): skip the prompt.
    override = os.environ.get("DUCKTYPE_DATA_DIR")
    if override:
        root = paths.set_root(override)
        cleanup_old_root(root)
        return root

    # Already configured -> use it (and finish any pending relocation cleanup).
    pointed = paths.read_pointer()
    if pointed and Path(pointed).expanduser().exists():
        root = paths.set_root(pointed)
        cleanup_old_root(root)
        return root

    legacy = _legacy_root()

    if not _intro_dialog():
        return None

    chosen: Optional[str] = None
    while not chosen:
        chosen = pick_folder(initial=legacy or paths.data_dir())
        if chosen is None:
            # Cancelled the picker: confirm whether to quit or retry.
            MB_YESNO = 0x00000004
            MB_ICONQUESTION = 0x00000020
            IDYES = 6
            again = ctypes.windll.user32.MessageBoxW(
                None, "没有选择文件夹。要重新选择吗？\n选「否」将退出 DuckType。",
                "码字鸭 DuckType", MB_YESNO | MB_ICONQUESTION)
            if again != IDYES:
                return None
    dst = Path(chosen).expanduser()
    dst.mkdir(parents=True, exist_ok=True)

    # Migrate existing data (copy -> verify -> delete) if needed.
    if legacy and legacy.resolve() != dst.resolve():
        plan = plan_files(legacy, dst)
        if plan:
            with _ProgressWindow("码字鸭 · 正在迁移数据",
                                 f"正在把现有数据移动到：\n{dst}") as win:
                copy_files(plan, on_progress=win.set_progress)
            if verify_files(plan):
                delete_files([s for s, _ in plan])
                nd = legacy / "native"
                if nd.is_dir() and not any(nd.iterdir()):
                    try:
                        nd.rmdir()
                    except OSError:
                        pass
                log.info("Migrated data from %s to %s", legacy, dst)
            else:
                log.error("Migration verify failed; left source data at %s", legacy)

    paths.write_pointer(str(dst))
    return paths.set_root(dst)
