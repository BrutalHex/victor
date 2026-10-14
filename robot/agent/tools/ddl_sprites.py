#!/usr/bin/env python3
"""Pack DDL's knowledge-graph "searching" face (small squares linked up) for
the 160x80 Vector 2 panel.

Source: github.com/digital-dream-labs/vector-animations-build
  assets/sprites/spriteSequences/face_knowledgegraph_searching_getin/ (8)
  assets/sprites/spriteSequences/face_knowledgegraph_searching/       (36)
played by anim_knowledgegraph_searching_getin_01 / _searching_01 at the
engine's 30 fps sprite rate. Used under the Digital Dream Labs Software Asset
License 1.0 (personal use with a Vector robot).

Sprites are 184x96 grayscale (Vector 1 size). They are scaled by 160/184 on
both axes and the 83-row result is cropped to the centred 80 rows; the engine
tints grayscale face sprites with the eye colour, the agent does that at run
time. Output: gzip of b"KGS1" + u8 getin + u8 loop + frames (160*80 bytes each).

usage: ddl_sprites.py <vector-animations-build checkout> <out.gz>
"""
import glob
import gzip
import os
import sys

from PIL import Image

W, H = 160, 80


def frames(root, seq):
    files = sorted(glob.glob(os.path.join(root, "assets/sprites/spriteSequences", seq, "*.png")))
    if not files:
        sys.exit(f"no sprites for {seq}")
    out = []
    for f in files:
        im = Image.open(f).convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        bg.alpha_composite(im)
        g = bg.convert("L")
        sw, sh = g.size
        nh = round(sh * W / sw)
        g = g.resize((W, nh), Image.LANCZOS)
        top = (nh - H) // 2
        g = g.crop((0, top, W, top + H))
        out.append(bytes(0 if v < 10 else v for v in g.tobytes()))
    return out


def main():
    root, dst = sys.argv[1], sys.argv[2]
    getin = frames(root, "face_knowledgegraph_searching_getin")
    loop = frames(root, "face_knowledgegraph_searching")
    blob = b"KGS1" + bytes([len(getin), len(loop)]) + b"".join(getin + loop)
    with gzip.open(dst, "wb", compresslevel=9) as f:
        f.write(blob)
    print(f"{dst}: getin {len(getin)} loop {len(loop)} raw {len(blob)} gz {os.path.getsize(dst)}")


if __name__ == "__main__":
    main()
