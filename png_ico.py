# -*- coding: utf-8 -*-
"""Kép -> Windows ikon (.ico) átalakító.

PNG / JPG / WebP / BMP fájlból készít több méretet tartalmazó .ico-t, hogy a
tálcán, az Explorerben és az ablak sarkában is éles legyen. A dekódolást és a
kicsinyítést a program által amúgy is letöltött ffmpeg végzi, az .ico
összerakását pedig az ikon_keszites.py-ban lévő saját PNG/ICO kódoló - így
nincs szükség Pillow-ra vagy bármi másra.

Használat:
    python png_ico.py kep.png                  -> icon_youtube_letolto.ico
    python png_ico.py kep.png sajat_ikon.ico   -> megadott kimenet

A négyzet alakúra vágás helyett átlátszó kerettel egészíti ki a képet, tehát a
nem négyzetes forrás sem torzul el.
"""

import os
import subprocess
import sys

from ikon_keszites import MERETEK, ico_bajtok, png_bajtok

HELY = os.path.dirname(os.path.abspath(__file__))
ALAP_KIMENET = os.path.join(HELY, "icon_youtube_letolto.ico")
FFMPEG = os.path.join(os.getenv("LOCALAPPDATA", "."), "ZeneLetolto",
                      "ffmpeg", "ffmpeg.exe")


def kep_beolvasas(forras, meret):
    """A képet `meret` x `meret` nyers RGBA képpontokká alakítja ffmpeg-gel."""
    szuro = (f"format=rgba,"
             f"scale={meret}:{meret}:force_original_aspect_ratio=decrease"
             f":flags=lanczos,"
             f"pad={meret}:{meret}:(ow-iw)/2:(oh-ih)/2:color=#00000000")
    parancs = [FFMPEG, "-v", "error", "-i", forras, "-vf", szuro,
               "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
    r = subprocess.run(parancs, capture_output=True)
    if r.returncode != 0 or len(r.stdout) != meret * meret * 4:
        raise SystemExit(
            f"[HIBA] Az ffmpeg nem tudta feldolgozni a képet ({meret}x{meret}).\n"
            f"       {(r.stderr or b'').decode('utf-8', 'replace').strip()[:400]}")
    return bytearray(r.stdout)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    forras = sys.argv[1]
    kimenet = sys.argv[2] if len(sys.argv) > 2 else ALAP_KIMENET

    if not os.path.isfile(forras):
        raise SystemExit(f"[HIBA] Nincs ilyen fájl: {forras}")
    if not os.path.isfile(FFMPEG):
        raise SystemExit(
            "[HIBA] Nem találom az ffmpeg-et. Indítsd el egyszer a programot,\n"
            f"       az letölti ide: {FFMPEG}")

    print(f"Forrás: {forras}")
    kepek = []
    for meret in MERETEK:
        print(f"  {meret}x{meret} …", flush=True)
        kepek.append((meret, png_bajtok(kep_beolvasas(forras, meret), meret)))

    with open(kimenet, "wb") as f:
        f.write(ico_bajtok(kepek))
    print(f"\nKész: {kimenet} ({os.path.getsize(kimenet)} byte, "
          f"{len(kepek)} méret)")
    if os.path.normcase(kimenet) == os.path.normcase(ALAP_KIMENET):
        print("A következő build már ezt az ikont fogja használni.")


if __name__ == "__main__":
    main()
