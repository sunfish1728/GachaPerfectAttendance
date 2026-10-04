"""由原始圖片產生應用程式圖示：裁成正方形、加上方角邊框（配合介面的工業線框風格），輸出 icon.png 與多尺寸 icon.ico。

用法：.venv/Scripts/python.exe scripts/build_icon.py <原始圖片> [x y 邊長]
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
FRAME = QColor("#0A4550")  # 介面淺色主題的結構色（深青綠）


def render(src: QImage, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # 小尺寸邊框至少 1px，留 0 邊距讓圖示在工作列看起來夠大
    border = max(1.0, round(size * 0.045))
    radius = size * 0.08
    outer = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(outer, radius, radius)
    p.setClipPath(path)
    p.drawImage(outer, src)
    p.setClipping(False)
    pen = QPen(FRAME, border)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    half = border / 2
    p.drawRoundedRect(outer.adjusted(half, half, -half, -half), radius - half, radius - half)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(images: list[QImage], path: Path) -> None:
    blobs = [png_bytes(i) for i in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for img, blob in zip(images, blobs):
        w = img.width() if img.width() < 256 else 0
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    app = QGuiApplication([])  # noqa: F841
    src = QImage(sys.argv[1])
    if src.isNull():
        raise SystemExit("讀不到圖片")
    if len(sys.argv) >= 5:
        x, y, side = (int(v) for v in sys.argv[2:5])
    else:
        side = min(src.width(), src.height())
        x, y = (src.width() - side) // 2, (src.height() - side) // 2
    square = src.copy(x, y, side, side).scaled(
        1024, 1024, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    render(square, 1024).save(str(assets / "icon.png"))
    write_ico([render(square, s) for s in SIZES], assets / "icon.ico")
    for s in (16, 32, 256):
        render(square, s).save(str(ROOT / "runtime" / "dev" / f"icon_{s}.png"))
    print("ok", side)


if __name__ == "__main__":
    main()
