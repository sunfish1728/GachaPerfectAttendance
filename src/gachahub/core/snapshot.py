"""以 GDI 讀取整個虛擬螢幕，使用標準函式庫寫入 PNG。"""

from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes as w
from pathlib import Path


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
                ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 1)]


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def capture_screen(path: Path) -> bool:
    dc = memory = bitmap = old = None
    try:
        user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
        user.GetDC.argtypes, user.GetDC.restype = [w.HWND], w.HDC
        user.ReleaseDC.argtypes = [w.HWND, w.HDC]
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [w.HDC], w.HDC
        gdi.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
        gdi.CreateCompatibleBitmap.restype = w.HBITMAP
        gdi.SelectObject.argtypes, gdi.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
        gdi.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
        gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFO), w.UINT]
        gdi.DeleteObject.argtypes = [w.HANDLE]
        gdi.DeleteDC.argtypes = [w.HDC]
        x, y, width, height = [user.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        if width <= 0 or height <= 0:
            return False
        dc = user.GetDC(None)
        if not dc:
            return False
        memory = gdi.CreateCompatibleDC(dc)
        if not memory:
            return False
        bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
        if not bitmap:
            return False
        old = gdi.SelectObject(memory, bitmap)
        if not old or old == ctypes.c_void_p(-1).value:
            old = None
            return False
        if not gdi.BitBlt(memory, 0, 0, width, height, dc, x, y, 0x40CC0020):
            return False
        # GetDIBits 的 bitmap 不可仍被選入 DC。
        restored = gdi.SelectObject(memory, old)
        if not restored or restored == ctypes.c_void_p(-1).value:
            return False
        old = None
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height,
                                         1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(dc, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            return False
        raw = pixels.raw
        compressor = zlib.compressobj()
        compressed = []
        for row in range(height):
            bgra = raw[row * width * 4:(row + 1) * width * 4]
            rgb = bytearray(width * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
            compressed.append(compressor.compress(b"\0" + rgb))
        compressed.append(compressor.flush())
        png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
               + _chunk(b"IDAT", b"".join(compressed)) + _chunk(b"IEND", b""))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return True
    except Exception:
        return False
    finally:
        # 即使中途失敗，也必須還原選入物件並釋放每個 GDI 資源。
        for action in (lambda: gdi.SelectObject(memory, old) if memory and old else None,
                       lambda: gdi.DeleteObject(bitmap) if bitmap else None,
                       lambda: gdi.DeleteDC(memory) if memory else None,
                       lambda: user.ReleaseDC(None, dc) if dc else None):
            try:
                action()
            except Exception:
                pass
