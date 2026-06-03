"""
Shared Excel-COM session lifecycle.

Both the COM formatter (`com_formatter.py`) and the legacy `.xls` → `.xlsx`
converter (`xls_convert.py`) drive a private Excel instance over COM. This
module owns the *entire* lifecycle in one place so the two callers can never
drift:

  1. **Per-thread COM apartment init.** The CLI runs on the main thread (COM
     auto-initialized), but the Gradio UI dispatches handlers onto worker
     threads where COM is NOT initialized. Without an explicit
     ``CoInitialize`` there, ``DispatchEx`` raises "CoInitialize has not been
     called", the caller throws, and the pipeline silently degrades to the
     lossy openpyxl/xlrd path (output loses images, merged cells, widths).

  2. **A dedicated Excel process** via ``DispatchEx`` (never the user's own
     interactive instance).

  3. **Guaranteed teardown.** ``Quit()`` alone is not enough: any Python-side
     COM wrapper that outlives it — a transient ``sheet.Cells(r,c).Value``
     object caught in a GC cycle, an exception traceback pinning locals —
     keeps the spawned ``excel.exe`` resident. In a long-running UI process
     these orphans pile up across hundreds of requests until Windows can no
     longer start a new instance; then ``DispatchEx`` fails and every quote
     silently comes out unformatted until the app is restarted. That is
     exactly the "跑久了就丢格式、重启就好" symptom users report.

     So we drop our references and ``gc.collect()`` to give the process a
     chance to exit cleanly, then force-kill the specific PID we spawned if it
     is still alive after a short grace window. In practice a hidden
     ``DispatchEx`` Excel usually survives ``Quit()`` regardless of how
     carefully we release references (the surviving pin is a COM marshaling
     artifact, not a Python reference), so the kill is the *normal* cleanup
     path, not a rare backstop — that's why it's logged at DEBUG, not WARNING.
     The kill only ever targets a process that did NOT exist before our
     ``DispatchEx`` — so it can never take down the user's own open Excel/WPS
     documents.

Everything in the teardown path is best-effort and swallows its own errors:
the backstop must never turn a successful format into a failure.
"""

from __future__ import annotations

import gc
import logging
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)


# Spreadsheet-host image names we might have spawned. Listing WPS here is
# safe: we only ever kill a PID that was absent before our own DispatchEx, so
# enumerating the user's WPS can never cause us to kill their open documents.
_OFFICE_IMAGE_NAMES = ("excel.exe", "et.exe", "wps.exe", "wpscloudsvr.exe")

# How long to let a just-Quit() instance exit on its own before we force-kill
# it. WaitForSingleObject returns the instant the process exits, so a clean
# teardown pays only its real exit time; a hidden DispatchEx Excel usually
# never releases on its own, so this budget IS the per-quote latency the
# Gradio UI eats before the kill. Keep it tight — just long enough to catch
# the occasional instance that does exit cleanly.
_EXIT_GRACE_MS = 1000


@contextmanager
def excel_session() -> Iterator:
    """Yield a hidden, dedicated Excel ``Application`` COM object.

    Initializes COM on the calling thread, spins up an isolated Excel via
    ``DispatchEx``, and on exit guarantees the spawned process is gone
    (clean Quit when possible, force-kill as a backstop). Use as::

        with excel_session() as excel:
            wb = excel.Workbooks.Open(...)
            try:
                ...
            finally:
                wb.Close(SaveChanges=False)
                wb = None        # let teardown free the process cleanly

    Callers SHOULD drop their own workbook references (set to ``None``) before
    leaving the ``with`` block. If they don't, the kill backstop still keeps
    the process table clean — it just costs the grace window and an abrupt
    kill instead of a tidy exit.
    """
    import pythoncom
    import win32com.client as win32

    # CoInitialize returns S_FALSE (no raise) when this thread is already
    # initialized with the same apartment model; it raises com_error only on
    # RPC_E_CHANGED_MODE. Track whether WE initialized so we balance the ref
    # count and never uninitialize an apartment someone else owns.
    _co_init = False
    try:
        pythoncom.CoInitialize()
        _co_init = True
    except pythoncom.com_error:
        pass

    pre_pids = _office_pids()
    excel = None
    pid: int | None = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        pid = _spawned_pid(excel, pre_pids)
        yield excel
    finally:
        try:
            if excel is not None:
                excel.Quit()
        except Exception:                                          # noqa: BLE001
            pass
        # Drop the last Python-side reference, THEN gc, so win32com releases
        # its COM wrappers and Excel can exit of its own accord.
        excel = None
        gc.collect()
        if pid is not None:
            _terminate_if_alive(pid)
        if _co_init:
            try:
                pythoncom.CoUninitialize()
            except Exception:                                      # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Process bookkeeping for the kill backstop
# ---------------------------------------------------------------------------
def _spawned_pid(excel, pre_pids: set[int]) -> int | None:
    """PID of the instance we just created, or ``None`` if we can't prove we
    spawned it (in which case we won't kill anything).

    Derived from the Application's main window handle. The pre-existing-PID
    guard covers WPS, whose ``DispatchEx`` may hand back an already-running
    process — that PID would be in ``pre_pids`` and we leave it alone.
    """
    try:
        import win32process

        _tid, pid = win32process.GetWindowThreadProcessId(excel.Hwnd)
        pid = int(pid)
    except Exception:                                              # noqa: BLE001
        return None
    if pid <= 0 or pid in pre_pids:
        return None
    return pid


def _office_pids() -> set[int]:
    """PIDs of running spreadsheet hosts (excel/et/wps), via the Toolhelp
    snapshot API. Locale-independent. Returns an empty set on any failure, in
    which case we simply skip the kill backstop for this run.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    pids: set[int] = set()
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (None, 0, INVALID_HANDLE_VALUE):
            return pids
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            wanted = {n.lower() for n in _OFFICE_IMAGE_NAMES}
            ok = k32.Process32First(snap, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile.decode("ascii", "ignore").lower()
                if name in wanted:
                    pids.add(int(entry.th32ProcessID))
                ok = k32.Process32Next(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
    except Exception:                                              # noqa: BLE001
        return set()
    return pids


def _terminate_if_alive(pid: int, *, grace_ms: int = _EXIT_GRACE_MS) -> None:
    """Wait up to ``grace_ms`` for the spawned process to exit after Quit();
    force-terminate it if it is still alive. Best-effort — every failure mode
    (already gone, no rights, API missing) is swallowed.
    """
    try:
        import win32api
        import win32con
        import win32event
    except Exception:                                              # noqa: BLE001
        return
    handle = None
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE, False, pid,
        )
    except Exception:                                              # noqa: BLE001
        return  # process already exited, or we lack rights — nothing to do
    if not handle:
        return
    try:
        # Quit() normally lets Excel exit within milliseconds;
        # WaitForSingleObject returns the moment that happens, so the grace
        # window is spent in full only on a genuinely stuck instance.
        if win32event.WaitForSingleObject(handle, grace_ms) != win32event.WAIT_OBJECT_0:
            win32api.TerminateProcess(handle, 0)
            # DEBUG, not WARNING: a hidden DispatchEx Excel surviving Quit() is
            # the expected case, and this kill is the routine cleanup that
            # keeps orphans from accumulating — not an anomaly worth alarming
            # the operator on every quote.
            logger.debug(
                "Excel COM instance (pid=%s) still alive after Quit(); "
                "force-terminated (routine teardown).", pid,
            )
    except Exception:                                              # noqa: BLE001
        pass
    finally:
        try:
            win32api.CloseHandle(handle)
        except Exception:                                          # noqa: BLE001
            pass


__all__ = ["excel_session"]
