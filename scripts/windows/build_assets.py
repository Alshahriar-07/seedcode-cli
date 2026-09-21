"""Generate the Windows branding assets for Seed Code — stdlib only.

Produces, under ``assets/windows/``:

* ``seedcode.ico``      — multi-resolution icon (16..128 BMP + 256 PNG),
                          embedded into seedcode.exe, setup.exe and every
                          shortcut so the default Python icon never appears.
* ``wizard.bmp``        — Inno Setup wizard side image (164x314, 24-bit).
* ``wizard-small.bmp``  — Inno Setup wizard header image (55x58, 24-bit).
* ``version_info.txt``  — PyInstaller version resource (publisher, version,
                          product name shown in Explorer file properties).
* ``preview.png``       — 256px render for eyeballing the artwork.

plus the canonical ``assets/logo.png`` — the official 256px mark committed
to source control.

The artwork itself lives in ``seedcode/branding.py`` (the 16x16 pixel-art
"sprouting seed" grid and its Seed Green palette) — the single source of
truth shared with the terminal renderer ``seedcode/ui/logo.py``. Everything
is deterministic: same inputs, byte-identical outputs.

Usage:
    python build_assets.py --version 6.2.5
    python build_assets.py --verify-exe ..\\..\\dist\\seedcode.exe
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

# Make the repo root importable when this runs as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from seedcode import branding as _branding
from seedcode.branding import CLEAR, INK, logo_pixels, render_logo_png  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "windows"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Canonical 256px logo committed to source control (the official asset the
# dashboard/UI references).
LOGO_PNG = REPO_ROOT / "assets" / "logo.png"

# Historical aliases kept so any external references keep resolving.
BG = (13, 17, 23, 255)         # dark panel background
PRIMARY = INK["G"]             # Seed Green
ACCENT = INK["L"]              # Soft Green
DARK = INK["D"]                # seed body
# The artwork grid lives in seedcode.branding (single source of truth).
_ART = _branding.ART
_INK = _branding.INK


def _render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """Render the mark at ``size`` px: rounded dark tile + scaled pixel art."""
    radius = size * 3 // 16
    grid = [[CLEAR] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            # Rounded-rect clip: inside unless outside a corner circle.
            cx = radius if x < radius else (size - 1 - radius if x >= size - radius else x)
            cy = radius if y < radius else (size - 1 - radius if y >= size - radius else y)
            if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius and (
                (x < radius or x >= size - radius) and (y < radius or y >= size - radius)
            ):
                continue
            cell = _ART[y * 16 // size][x * 16 // size]
            grid[y][x] = _INK.get(cell, BG)
    return grid


# --- PNG encoding (RGBA, no filters) -----------------------------------------
def _png(grid: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Encode a pixel grid as PNG — delegates to the canonical renderer."""
    return render_logo_png(len(grid))


# --- ICO assembly --------------------------------------------------------------
def _ico_bmp_entry(grid: list[list[tuple[int, int, int, int]]]) -> bytes:
    """One 32-bit BMP image as stored inside an ICO (DIB + empty AND mask)."""
    h, w = len(grid), len(grid[0])
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    xor = b"".join(
        bytes((px[2], px[1], px[0], px[3]))  # BGRA, rows bottom-up
        for row in reversed(grid)
        for px in row
    )
    and_mask = b"\x00" * (((w + 31) // 32) * 4) * h  # alpha carries transparency
    return header + xor + and_mask


def build_ico(path: Path) -> bytes:
    """Write the multi-resolution .ico; returns the 256px PNG payload."""
    bmp_sizes = (16, 24, 32, 48, 64, 128)
    images = [(s, _ico_bmp_entry(_render(s))) for s in bmp_sizes]
    png256 = _png(_render(256))
    images.append((256, png256))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        )
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)
    return png256


# --- Inno wizard bitmaps (24-bit BMP files) -------------------------------------
def _bmp_file(grid: list[list[tuple[int, int, int, int]]]) -> bytes:
    h, w = len(grid), len(grid[0])
    row_pad = (4 - (w * 3) % 4) % 4
    pixels = b"".join(
        b"".join(bytes((px[2], px[1], px[0])) for px in row) + b"\x00" * row_pad
        for row in reversed(grid)
    )
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    file_header = struct.pack("<2sIHHI", b"BM", 14 + len(info) + len(pixels), 0, 0, 14 + len(info))
    return file_header + info + pixels


def _wizard_canvas(w: int, h: int, mark_px: int) -> list[list[tuple[int, int, int, int]]]:
    """Dark canvas with the mark centred (nearest-neighbour from the art grid)."""
    grid = [[BG] * w for _ in range(h)]
    x0, y0 = (w - mark_px) // 2, (h - mark_px) // 2
    for y in range(mark_px):
        for x in range(mark_px):
            cell = _ART[y * 16 // mark_px][x * 16 // mark_px]
            if cell in _INK:
                grid[y0 + y][x0 + x] = _INK[cell]
    return grid


# --- PyInstaller version resource -------------------------------------------------
_VERSION_INFO = """\
# Generated by scripts/windows/build_assets.py — do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v_tuple}),
    prodvers=({v_tuple}),
    mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Eagox Studio'),
      StringStruct('FileDescription', 'Seed Code - terminal AI coding assistant'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'seedcode'),
      StringStruct('LegalCopyright', 'Copyright (c) Al Shahriar Sowan. MIT License.'),
      StringStruct('OriginalFilename', 'seedcode.exe'),
      StringStruct('ProductName', 'Seed Code'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_info(path: Path, version: str) -> None:
    parts = [int(p) for p in version.split(".") if p.isdigit()][:4]
    parts += [0] * (4 - len(parts))
    path.write_text(
        _VERSION_INFO.format(version=version, v_tuple=", ".join(map(str, parts))),
        encoding="utf-8",
    )


# --- verification ------------------------------------------------------------------
def verify_ico(path: Path) -> None:
    """Structural check: parse the ICO back and confirm every expected entry."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and kind == 1, "not an ICO file"
    sizes = set()
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i
        )
        assert offset + length <= len(data), "entry overruns file"
        sizes.add(w or 256)
        blob = data[offset : offset + length]
        assert blob[:8] == b"\x89PNG\r\n\x1a\n" or struct.unpack_from("<I", blob)[0] == 40
    expected = {16, 24, 32, 48, 64, 128, 256}
    assert sizes == expected, f"icon sizes {sorted(sizes)} != {sorted(expected)}"


def verify_exe_has_icon(exe: Path, ico: Path) -> None:
    """Confirm the built exe embeds this icon (the 256px PNG payload is stored
    verbatim as an RT_ICON resource, so a byte search is a reliable probe)."""
    ico_data = ico.read_bytes()
    png = ico_data[ico_data.find(b"\x89PNG") :]
    if png[:512] not in exe.read_bytes():
        raise SystemExit(
            f"VERIFY FAILED: {exe} does not embed the Seed Code icon "
            "(PyInstaller --icon step missing or used a different file)."
        )
    print(f"[OK] {exe.name} embeds the Seed Code icon.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="", help="app version for the exe resource")
    parser.add_argument("--verify-exe", default="", help="check an exe embeds the icon")
    opts = parser.parse_args()

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    ico_path = ASSETS_DIR / "seedcode.ico"

    if opts.verify_exe:
        verify_exe_has_icon(Path(opts.verify_exe), ico_path)
        return

    build_ico(ico_path)
    verify_ico(ico_path)
    # Canonical logo asset (committed to source control) + the volatile preview.
    LOGO_PNG.parent.mkdir(parents=True, exist_ok=True)
    LOGO_PNG.write_bytes(render_logo_png(256))
    (ASSETS_DIR / "preview.png").write_bytes(_png(_render(256)))
    (ASSETS_DIR / "wizard.bmp").write_bytes(_bmp_file(_wizard_canvas(164, 314, 128)))
    (ASSETS_DIR / "wizard-small.bmp").write_bytes(_bmp_file(_wizard_canvas(55, 58, 48)))
    if opts.version:
        write_version_info(ASSETS_DIR / "version_info.txt", opts.version)
    print(f"[OK] Branding assets written to {ASSETS_DIR}")
    for name in ("seedcode.ico", "wizard.bmp", "wizard-small.bmp", "preview.png"):
        print(f"     {name}  ({(ASSETS_DIR / name).stat().st_size} bytes)")
    print(f"[OK] Canonical logo: {LOGO_PNG}  ({LOGO_PNG.stat().st_size} bytes)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        sys.exit(f"VERIFY FAILED: {exc}")
