# -*- coding: utf-8 -*-
"""A program ikonjának (icon_youtube_letolto.ico) előállítása.

Külső könyvtár nélkül rajzol: a képet 4x felbontásban raszterizáljuk, majd
átlagolva kicsinyítjük - ettől lesz sima a széle. Az .ico-ba PNG-ként
kerülnek a méretek (Vista óta ezt minden Windows kezeli).

Futtatás:  python ikon_keszites.py
"""

import os
import struct
import zlib

KIMENET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "icon_youtube_letolto.ico")
MERETEK = (16, 24, 32, 48, 64, 128, 256)
MINTAVETEL = 4                      # élsimítás: ennyiszeres felbontásban rajzol


def keverd(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def lekerekitett_teglalapban(x, y, x1, y1, x2, y2, r):
    """Benne van-e a pont a lekerekített téglalapban?"""
    if not (x1 <= x <= x2 and y1 <= y <= y2):
        return False
    kx = min(max(x, x1 + r), x2 - r)
    ky = min(max(y, y1 + r), y2 - r)
    return (x - kx) ** 2 + (y - ky) ** 2 <= r * r


def haromszogben(x, y, csucsok):
    def elojel(ax, ay, bx, by, cx, cy):
        return (ax - cx) * (by - cy) - (bx - cx) * (ay - cy)
    a, b, c = csucsok
    d1 = elojel(x, y, *a, *b)
    d2 = elojel(x, y, *b, *c)
    d3 = elojel(x, y, *c, *a)
    negativ = d1 < 0 or d2 < 0 or d3 < 0
    pozitiv = d1 > 0 or d2 > 0 or d3 > 0
    return not (negativ and pozitiv)


def pont_szine(x, y):
    """Egy egységnyi (0..1) koordinátájú pont színe -> (r, g, b, a) vagy None."""
    # háttér: lekerekített piros négyzet, függőleges színátmenettel
    if not lekerekitett_teglalapban(x, y, 0.015, 0.015, 0.985, 0.985, 0.235):
        return None
    hatter = keverd((255, 74, 88), (200, 12, 30), min(max(y, 0.0), 1.0))

    feher = (255, 255, 255)
    # letöltés-nyíl szára
    if 0.425 <= x <= 0.575 and 0.20 <= y <= 0.50:
        return feher + (255,)
    # nyílhegy
    if haromszogben(x, y, ((0.295, 0.465), (0.705, 0.465), (0.50, 0.735))):
        return feher + (255,)
    # tálca a nyíl alatt
    if lekerekitett_teglalapban(x, y, 0.255, 0.775, 0.745, 0.855, 0.04):
        return feher + (255,)
    return hatter + (255,)


def kep_keszites(meret):
    """RGBA képpontok (bytearray) `meret` x `meret` méretben."""
    n = meret * MINTAVETEL
    px = bytearray(meret * meret * 4)
    for cy in range(meret):
        for cx in range(meret):
            r = g = b = a = 0
            for sy in range(MINTAVETEL):
                for sx in range(MINTAVETEL):
                    x = (cx * MINTAVETEL + sx + 0.5) / n
                    y = (cy * MINTAVETEL + sy + 0.5) / n
                    szin = pont_szine(x, y)
                    if szin:
                        r += szin[0]
                        g += szin[1]
                        b += szin[2]
                        a += szin[3]
            db = MINTAVETEL * MINTAVETEL
            # Az átlátszó szélen a színt a fedettséggel súlyozzuk, különben
            # a lekerekített sarok szürkés peremet kapna.
            fedes = a / (255 * db)
            i = (cy * meret + cx) * 4
            if fedes > 0:
                px[i] = int(round(r / (a / 255)))
                px[i + 1] = int(round(g / (a / 255)))
                px[i + 2] = int(round(b / (a / 255)))
                px[i + 3] = int(round(a / db))
    return px


def png_bajtok(px, meret):
    sorok = b"".join(b"\x00" + bytes(px[y * meret * 4:(y + 1) * meret * 4])
                     for y in range(meret))

    def darab(tipus, adat):
        return (struct.pack(">I", len(adat)) + tipus + adat
                + struct.pack(">I", zlib.crc32(tipus + adat) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + darab(b"IHDR", struct.pack(">IIBBBBB", meret, meret, 8, 6, 0, 0, 0))
            + darab(b"IDAT", zlib.compress(sorok, 9))
            + darab(b"IEND", b""))


def ico_bajtok(kepek):
    fejlec = struct.pack("<HHH", 0, 1, len(kepek))
    eltolas = 6 + 16 * len(kepek)
    bejegyzesek = b""
    adatok = b""
    for meret, png in kepek:
        bejegyzesek += struct.pack("<BBBBHHII", meret % 256, meret % 256, 0, 0,
                                   1, 32, len(png), eltolas)
        eltolas += len(png)
        adatok += png
    return fejlec + bejegyzesek + adatok


if __name__ == "__main__":
    kepek = []
    for meret in MERETEK:
        print(f"  {meret}x{meret} …", flush=True)
        kepek.append((meret, png_bajtok(kep_keszites(meret), meret)))
    with open(KIMENET, "wb") as f:
        f.write(ico_bajtok(kepek))
    print(f"Kész: {KIMENET} ({os.path.getsize(KIMENET)} byte)")
