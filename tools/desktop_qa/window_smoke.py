#!/usr/bin/env python3
"""Native-window smoke test for a launched desktop app (stdlib + ctypes, Windows only).

Launch --cmd, find a visible top-level window of the launched process TREE with --title,
report hwnd/class/rect/pid/image/visible/cloaked, assert image identity + not a browser,
optionally assert Chromium renderer/GPU children, screenshot (PrintWindow -> PNG via zlib),
close (WM_CLOSE or kill of recorded PIDs only), assert the whole tree (plus vrb.exe/dotnet.exe
descendants) is gone within 10 s. Never kills by image name.
"""
import argparse, ctypes, json, os, struct, subprocess, sys, time, zlib
from ctypes import wintypes as wt

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    dwm = ctypes.WinDLL("dwmapi")
except OSError:
    dwm = None

BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe", "vivaldi.exe"}
TH32CS_SNAPPROCESS = 2
PROCESS_QUERY_LIMITED = 0x1000
STILL_ACTIVE = 259
WM_CLOSE = 0x10


class PE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]


def snapshot():
    """pid -> (ppid, exe name)"""
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out = {}
    pe = PE32(); pe.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32FirstW(h, ctypes.byref(pe))
    while ok:
        out[pe.th32ProcessID] = (pe.th32ParentProcessID, pe.szExeFile)
        ok = k32.Process32NextW(h, ctypes.byref(pe))
    k32.CloseHandle(h)
    return out


def image_path(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024); n = wt.DWORD(1024)
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
    finally:
        k32.CloseHandle(h)


def alive(pid, want_image=None):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return False
    try:
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        if want_image:
            cur = image_path(pid)
            if cur and os.path.normcase(cur) != os.path.normcase(want_image):
                return False  # pid reused by another image
        return True
    finally:
        k32.CloseHandle(h)


def descend(roots, snap, known):
    """Add every process whose parent is in known; returns the grown set (mutates known)."""
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _) in snap.items():
            if pid not in known and ppid in known and pid != ppid:
                known[pid] = None; changed = True
    return known


def windows():
    res = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        pid = wt.DWORD(); u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        t = ctypes.create_unicode_buffer(n + 1); u32.GetWindowTextW(hwnd, t, n + 1)
        c = ctypes.create_unicode_buffer(256); u32.GetClassNameW(hwnd, c, 256)
        r = wt.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
        cloaked = wt.DWORD(0)
        if dwm:
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
        res.append(dict(hwnd=int(hwnd or 0), pid=pid.value, title=t.value, cls=c.value, visible=bool(u32.IsWindowVisible(hwnd)),
                        cloaked=bool(cloaked.value), rect=[r.left, r.top, r.right, r.bottom]))
        return True
    u32.EnumWindows(CB(cb), 0)
    return res


def cmdlines(pids):
    if not pids:
        return {}
    ps = ("Get-CimInstance Win32_Process -Filter \"%s\" | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
          % " OR ".join("ProcessId=%d" % p for p in pids))
    try:
        o = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return {}
    d = {}
    for line in o.splitlines():
        if "\t" in line:
            a, b = line.split("\t", 1)
            if a.strip().isdigit():
                d[int(a)] = b
    return d


def png_bytes(w, h, bgra):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]; rgb[1::3] = row[1::4]; rgb[2::3] = row[0::4]
        raw += b"\x00" + rgb

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def screenshot(hwnd, path):
    r = wt.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return dict(ok=False, error="empty rect")
    hdc = u32.GetWindowDC(hwnd); mdc = g32.CreateCompatibleDC(hdc); bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    okp = u32.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("x", wt.LONG), ("y", wt.LONG), ("c", wt.DWORD), ("i", wt.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g32.DeleteObject(bmp); g32.DeleteDC(mdc); u32.ReleaseDC(hwnd, hdc)
    data = buf.raw
    distinct = len(set(data[i:i + 4] for i in range(0, len(data), 4 * 97)))
    with open(path, "wb") as f:
        f.write(png_bytes(w, h, data))
    return dict(ok=bool(okp), path=path, width=w, height=h, distinct_sampled_colors=distinct)


def terminate(pid):
    h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 1); k32.CloseHandle(h)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--title", default="ValoHub")
    ap.add_argument("--expect-image")
    ap.add_argument("--expect-class")
    ap.add_argument("--expect-chromium-children", action="store_true", help="assert same-image --type=renderer and --type=gpu-process children")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--close-mode", choices=["wm_close", "kill", "none"], default="wm_close")
    ap.add_argument("--title-match", choices=["equals", "contains"], default="equals")
    ap.add_argument("--hold", type=float, default=0, help="seconds to keep the app open before closing")
    ap.add_argument("--screenshot")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE extra env for the child")
    a = ap.parse_args(argv)

    env = dict(os.environ)
    for kv in a.env:
        k, v = kv.split("=", 1); env[k] = v
    rep = dict(cmd=a.cmd, checks=[], window=None, tree=[], survivors=[])

    def check(name, ok, detail=""):
        rep["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail))); print(("PASS " if ok else "FAIL ") + name, detail)

    proc = subprocess.Popen(a.cmd, env=env)
    root = proc.pid
    root_dir = os.path.dirname(os.path.abspath(a.cmd.split()[0].strip('"'))) if a.cmd else ""
    known = {root: None}
    names = {}
    t0 = time.time(); win = None
    while time.time() - t0 < a.timeout:
        snap = snapshot(); descend([root], snap, known)
        for p in known:
            if p in snap:
                names[p] = snap[p][1]
        for w in windows():
            if w["pid"] in known and w["visible"] and (w["title"] == a.title or (a.title_match == "contains" and a.title in w["title"])):
                win = w; break
        if win:
            break
        if proc.poll() is not None and not any(alive(p) for p in known):
            break
        time.sleep(0.3)
    check("window found", win, "" if win else "no visible window titled %r within %ss" % (a.title, a.timeout))
    if win:
        win["image"] = image_path(win["pid"]); rep["window"] = win
        check("window visible & not cloaked", win["visible"] and not win["cloaked"], "visible=%s cloaked=%s rect=%s" % (win["visible"], win["cloaked"], win["rect"]))
        base = os.path.basename(win["image"] or "").lower()
        check("owner is not a browser", base not in BROWSERS, base)
        if a.expect_image:
            check("owner image == expected", win["image"] and os.path.normcase(os.path.abspath(win["image"])) == os.path.normcase(os.path.abspath(a.expect_image)),
                  "%s vs %s" % (win["image"], a.expect_image))
        if a.expect_class:
            check("window class", win["cls"] == a.expect_class, win["cls"])
        time.sleep(1.5)  # let children spawn
        snap = snapshot(); descend([root], snap, known)
        for p in known:
            if p in snap:
                names[p] = snap[p][1]
        cl = cmdlines(list(known))
        for p in known:
            rep["tree"].append(dict(pid=p, name=names.get(p), image=image_path(p), parent=snap.get(p, (None,))[0], alive=alive(p), cmdline=cl.get(p, "")))
        if a.expect_chromium_children:
            same = [t for t in rep["tree"] if t["pid"] != win["pid"] and t["image"] and win["image"] and os.path.normcase(t["image"]) == os.path.normcase(win["image"])]
            check("renderer child of same image", any("--type=renderer" in t["cmdline"] for t in same), "same-image children=%d" % len(same))
            check("gpu child of same image", any("--type=gpu-process" in t["cmdline"] for t in same), "")
        if a.screenshot:
            s = screenshot(win["hwnd"], a.screenshot); rep["screenshot"] = s
            check("screenshot captured", s.get("ok") and s.get("distinct_sampled_colors", 0) > 1, json.dumps(s))
    if a.hold:
        time.sleep(a.hold)  # keep the app up (e.g. so run_checks.mjs can drive it over CDP)
    # close
    recorded = {p: image_path(p) for p in known if alive(p)}
    if a.close_mode == "wm_close" and win:
        u32.PostMessageW(win["hwnd"], WM_CLOSE, 0, 0)
    elif a.close_mode == "kill":
        for p in list(recorded):
            terminate(p)
    t1 = time.time()
    if a.close_mode != "none":
        while time.time() - t1 < 10:
            snap = snapshot(); descend([root], snap, known)  # catch late descendants (vrb.exe/dotnet.exe)
            for p in known:
                if p not in recorded and alive(p):
                    img = image_path(p) or ""
                    # PID-reuse guard (integration fix): only adopt late descendants that live under the
                    # app's own folder or are known helper images; never an unrelated process.
                    if (os.path.normcase(img).startswith(os.path.normcase(root_dir)) if root_dir else False) \
                            or os.path.basename(img).lower() in ("conhost.exe", "dotnet.exe", "clireader.exe", "vrb.exe"):
                        recorded[p] = img
            if not any(alive(p, recorded[p]) for p in recorded):
                break
            time.sleep(0.25)
        surv = [dict(pid=p, image=recorded[p]) for p in recorded if alive(p, recorded[p])]
        rep["survivors"] = surv
        check("whole tree gone within 10s", not surv, "elapsed=%.1fs survivors=%s" % (time.time() - t1, surv))
        for s in surv:  # cleanup of our own recorded PIDs only
            terminate(s["pid"])
    rep["ok"] = all(c["ok"] for c in rep["checks"])
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, "w") as f:
            json.dump(rep, f, indent=2)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
