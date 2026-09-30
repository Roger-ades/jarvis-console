"""Render the app icons: PNG for the installable web app, .ico (Windows launcher) and
.icns (macOS launcher). No dependency.

    .venv\\Scripts\\python.exe tests\\make_icons.py
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "img"


def png_bytes(size: int, pixels: bytearray) -> bytes:
    raw = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def png(path: Path, size: int, pixels: bytearray):
    path.write_bytes(png_bytes(size, pixels))


def ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Windows icon with PNG-compressed images (supported since Vista)."""
    images = [(n, png_bytes(n, render(n, False))) for n in sizes]
    offset = 6 + 16 * len(images)
    head, body = struct.pack("<HHH", 0, 1, len(images)), b""
    for n, data in images:
        head += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    path.write_bytes(head + body)


def icns(path: Path):
    """macOS icon: PNG entries of 128, 256 and 512 px."""
    body = b"".join(t + struct.pack(">I", len(d) + 8) + d
                    for t, d in ((b"ic07", png_bytes(128, render(128, False))), (b"ic08", png_bytes(256, render(256, False))),
                                 (b"ic09", png_bytes(512, render(512, False)))))
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def render(size: int, maskable: bool) -> bytearray:
    px = bytearray(size * size * 4)
    unit = size / 64.0                       # drawing in a 64-unit box
    scale = 0.74 if maskable else 1.0        # maskable: keep the artwork in the safe zone
    bg = (11, 17, 25)
    tri = [(32, 15.5), (46.5, 40.5), (17.5, 40.5)]
    for y in range(size):
        for x in range(size):
            u = ((x + .5) / unit - 32) / scale + 32
            v = ((y + .5) / unit - 32) / scale + 32
            aa = 1.0 / (unit * scale)        # one pixel in drawing units
            # background: full bleed for maskable, rounded square otherwise
            if maskable:
                cov_bg = 1.0
            else:
                qx, qy = abs((x + .5) / unit - 32) - 32 + 13, abs((y + .5) / unit - 32) - 32 + 13
                d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 13
                cov_bg = clamp(.5 - d * unit)
            r_, g_, b_ = bg
            a_ = cov_bg
            dist = math.hypot(u - 32, v - 32)
            # soft glow
            glow = max(0.0, 1 - dist / 30) ** 2 * .35
            r_, g_, b_ = r_ + (64 - r_) * glow, g_ + (220 - g_) * glow, b_ + (255 - b_) * glow

            def over(col, cov):
                nonlocal r_, g_, b_
                r_, g_, b_ = r_ + (col[0] - r_) * cov, g_ + (col[1] - g_) * cov, b_ + (col[2] - b_) * cov

            # dashed outer ring
            ring = abs(dist - 26.5) - 1.5
            ang = (math.atan2(v - 32, u - 32) + math.pi) * 26.5
            dash = (ang % 15.2) < 11
            if dash:
                over((64, 220, 255), clamp(.5 - ring / aa) * .9)
            # amber ring
            over((255, 179, 71), clamp(.5 - (abs(dist - 19.5) - .85) / aa) * .85)
            # triangle outline
            dtri = min(seg_dist(u, v, *tri[i], *tri[(i + 1) % 3]) for i in range(3)) - .85
            over((64, 220, 255), clamp(.5 - dtri / aa) * .75)
            # core with radial gradient
            core = dist - 10.5
            if core < aa:
                t = dist / 10.5
                col = (242 + (64 - 242) * min(1, t / .45), 253 + (220 - 253) * min(1, t / .45), 255) if t < .45 else \
                      (64 + (11 - 64) * (t - .45) / .55, 220 + (42 - 220) * (t - .45) / .55, 255 + (68 - 255) * (t - .45) / .55)
                over(col, clamp(.5 - core / aa))
            i = (y * size + x) * 4
            px[i:i + 4] = bytes((int(clamp(r_ / 255) * 255), int(clamp(g_ / 255) * 255), int(clamp(b_ / 255) * 255), int(a_ * 255)))
    return px


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("icon-maskable-512.png", 512, True)):
        png(OUT / name, size, render(size, mask))
        print("écrit", name)
    ico(OUT / "jarvis.ico")
    icns(OUT / "jarvis.icns")
    print("écrit jarvis.ico, jarvis.icns")
