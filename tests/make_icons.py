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


def hexc(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def ramp(stops, t):
    """Colour of a gradient (list of (offset, colour)) at t."""
    t = clamp(t)
    for (o0, c0), (o1, c1) in zip(stops, stops[1:]):
        if t <= o1:
            return mix(c0, c1, clamp((t - o0) / ((o1 - o0) or 1)))
    return stops[-1][1]


# Same drawing as static/img/favicon.svg: an arc reactor centred on 0,0 (scaled 1.12 on a 64-unit tile).
ACCENT, ACCENT2, HOT, DEEP = hexc("#72c9ff"), hexc("#3aa6f0"), hexc("#e6f7ff"), hexc("#1c7fd6")
WHITE, RIM, EDGE, GAP, BOLT, BOLT_EDGE = (255, 255, 255), hexc("#9fdcff"), hexc("#d9f4ff"), hexc("#04101a"), hexc("#0b131c"), hexc("#a9b9ca")
TILE = [(0, hexc("#11233a")), (.55, hexc("#070e17")), (1, hexc("#020407"))]
METAL = [(0, hexc("#d2dde8")), (.28, hexc("#63768b")), (.55, hexc("#1b2531")), (.78, hexc("#52657a")), (1, hexc("#a9b9ca"))]
COIL = [(0, HOT), (.62, HOT), (.8, ACCENT), (1, DEEP)]
CORE = [(0, WHITE), (.45, HOT), (.8, ACCENT), (1, ACCENT2)]
R0, R1, W1, W0 = 12.6, 19.4, math.radians(12.5), math.radians(10)  # coils: radii, half-widths at the rim / inside


def coil_dist(x, y):
    """Signed distance to the nearest of the ten coils (annular sectors, narrower inside)."""
    rho = math.hypot(x, y)
    ang = math.atan2(x, -y)                               # 0 at the top, clockwise
    step = 2 * math.pi / 10
    d_ang = abs((ang + step / 2) % step - step / 2)       # angle to the nearest coil's axis
    half = W0 + (W1 - W0) * clamp((rho - R0) / (R1 - R0))
    return max(R0 - rho, rho - R1, (d_ang - half) * max(rho, 1e-6))


def render(size: int, maskable: bool) -> bytearray:
    px = bytearray(size * size * 4)
    unit = size / 64.0                                    # drawing in a 64-unit box
    small = size <= 32 and not maskable                   # tiny icons: bigger reactor, no hairlines
    k = 1.0 if maskable else 1.24 if small else 1.12      # maskable: stay in the safe zone
    aa = 1.0 / (unit * k)                                 # one pixel in emblem units
    bolts = [(23 * math.sin(math.radians(a)), -23 * math.cos(math.radians(a))) for a in (45, 135, 225, 315)]
    for y in range(size):
        for x in range(size):
            tx, ty = (x + .5) / unit, (y + .5) / unit
            col = ramp(TILE, math.hypot(tx - 32, ty - 28.8) / 46.5)
            if maskable:
                alpha = 1.0
            else:  # rounded tile with a hairline border
                qx, qy = abs(tx - 32) - 31 + 14, abs(ty - 32) - 31 + 14
                d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 14
                alpha = clamp(.5 - d * unit)
                col = mix(col, hexc("#8fd4ff"), clamp(.5 - (abs(d + .5) - .5) * unit) * .22)

            def over(c, dist, a=1.0):
                nonlocal col
                col = mix(col, c, clamp(.5 - dist / aa) * a)

            def glow(c, dist, reach, a):
                nonlocal col
                if dist > 0:
                    col = mix(col, c, a * math.exp(-(dist / reach) ** 2))

            ex, ey = (tx - 32) / k, (ty - 32) / k
            rho = math.hypot(ex, ey)
            if rho < 30:
                col = mix(col, ACCENT, .30 * (1 - rho / 30) ** 1.6)                              # ambient light
            if rho < 27:
                if not small:
                    over(RIM, abs(rho - 24.9) - .175, .45)                                       # rim
                dm = abs(rho - 23) - 1.8
                if dm < aa:
                    t = clamp(((ex + 22) * 42 + (ey + 24) * 48) / (42 * 42 + 48 * 48))
                    over(ramp(METAL, t), dm)                                                     # brushed metal ring
                if not small:
                    for bx, by in bolts:
                        db = math.hypot(ex - bx, ey - by)
                        over(BOLT_EDGE, db - 1.125)
                        over(BOLT, db - .775)                                                    # bolts
                    over(ACCENT, abs(rho - 21.1) - .25, .6)                                      # inner line
                dc = coil_dist(ex, ey)
                glow(ACCENT, dc, 1.4, .45)                                                       # coils' light
                if dc < aa:
                    over(ramp(COIL, rho / 19.4), dc, .95)                                        # coils
                    if not small:
                        over(EDGE, abs(dc) - .175, .7)
                over(GAP, abs(rho - 11) - .7)                                                    # dark gap
                if rho < 14:
                    t = rho / 14
                    c = mix(HOT, ACCENT, clamp(t / .42))
                    col = mix(col, c, .95 * (1 - t / .42) + (1 - t) * .42 if t < .42 else .42 * (1 - (t - .42) / .58))  # halo
                dco = rho - 8.6
                glow(HOT, dco, 1.2, .5)
                if dco < aa:
                    over(ramp(CORE, math.hypot(ex + 1.03, ey + 1.72) / 11.0), dco)               # core
                if not small:
                    over(WHITE, abs(rho - 5) - .3, .5)                                           # core ring
                    sx, sy = ex + 2.7, ey + 3.1                                                  # shine, tilted -35°
                    ca, sa = math.cos(math.radians(35)), math.sin(math.radians(35))
                    u, v = sx * ca - sy * sa, sx * sa + sy * ca
                    over(WHITE, (math.hypot(u / 1.9, v / 1.3) - 1) * 1.3, .75)
            i = (y * size + x) * 4
            px[i:i + 4] = bytes((int(clamp(col[0] / 255) * 255), int(clamp(col[1] / 255) * 255),
                                 int(clamp(col[2] / 255) * 255), int(alpha * 255)))
    return px


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("icon-maskable-512.png", 512, True)):
        png(OUT / name, size, render(size, mask))
        print("écrit", name)
    ico(OUT / "jarvis.ico")
    icns(OUT / "jarvis.icns")
    print("écrit jarvis.ico, jarvis.icns")
