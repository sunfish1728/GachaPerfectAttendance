import ctypes
import struct
import zlib
from types import SimpleNamespace

import pytest

from gachahub.core import snapshot as s
from test_presence import Fn


@pytest.fixture
def api(monkeypatch):
    events = []
    user = SimpleNamespace(GetSystemMetrics=Fn(lambda i: {76: -2, 77: -1, 78: 2, 79: 1}[i]),
        GetDC=Fn(lambda hwnd: 10), ReleaseDC=Fn(lambda *args: events.append("release") or 1))
    def select(dc, obj):
        events.append(("select", obj))
        return 40 if obj == 30 else 30
    def blit(*args):
        assert args[6:8] == (-2, -1)
        return 1
    def bits(dc, bitmap, first, count, pixels, info, usage):
        assert info._obj.bmiHeader.biHeight == -1
        ctypes.memmove(pixels, b"\x01\x02\x03\0\x04\x05\x06\0", 8)
        return 1
    gdi = SimpleNamespace(CreateCompatibleDC=Fn(lambda dc: 20),
        CreateCompatibleBitmap=Fn(lambda *args: 30), SelectObject=Fn(select),
        BitBlt=Fn(blit), GetDIBits=Fn(bits),
        DeleteObject=Fn(lambda obj: events.append("bitmap") or 1),
        DeleteDC=Fn(lambda dc: events.append("memory") or 1))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user, gdi32=gdi))
    return user, gdi, events


def test_png_and_resources(tmp_path, api):
    path = tmp_path / "s.png"
    assert s.capture_screen(path)
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (2, 1)
    pos = 8
    while data[pos+4:pos+8] != b"IDAT":
        pos += 12 + struct.unpack(">I", data[pos:pos+4])[0]
    size = struct.unpack(">I", data[pos:pos+4])[0]
    assert zlib.decompress(data[pos+8:pos+8+size]) == b"\0\x03\x02\x01\x06\x05\x04"
    assert api[2][-3:] == ["bitmap", "memory", "release"]


@pytest.mark.parametrize("failure", ["CreateCompatibleDC", "CreateCompatibleBitmap", "BitBlt", "GetDIBits"])
def test_failure_releases_resources(tmp_path, api, failure):
    _, gdi, events = api
    setattr(gdi, failure, Fn(lambda *args: 0))
    path = tmp_path / "s.png"
    assert not s.capture_screen(path)
    assert not path.exists()
    assert events[-1] == "release"
    if failure in ("BitBlt", "GetDIBits"):
        assert events[-3:] == ["bitmap", "memory", "release"]


def test_screen_readonly_smoke(tmp_path):
    path = tmp_path / "screen.png"
    if not s.capture_screen(path):
        pytest.skip("目前 Windows 工作階段無法讀取螢幕（受限桌面亦可能拒絕 BitBlt）")
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
