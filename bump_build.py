# -*- coding: utf-8 -*-
"""A kiadás-szkript segédje: a BUILD_SZAM növelése.

A helyi zeneletolto.py-ban lévő szám mellett a GitHubon már publikáltat is
lekérdezi, és a kettő közül a nagyobbikból indul ki. Enélkül egy elavult helyi
checkout-ról futtatott kiadás visszaléptetné a publikált build-számot, és a
felhasználók verziója régebbinek látszana a valóságosnál.

A version_info.txt-be sütött Windows fájlverziót is szinkronban tartja - ha
elcsúszik, az exe Tulajdonságok ablaka mást mutat, mint a program fejléce.
"""

import re
import ssl
import urllib.request

FORRAS = "zeneletolto.py"
VERZIO_INFO = "version_info.txt"
TAVOLI_URL = ("https://raw.githubusercontent.com/egonixaimgod/"
              "Youtube_lelopas/main/zeneletolto.py")

BUILD_MINTA = re.compile(r"^BUILD_SZAM\s*=\s*(\d+)", re.M)
VERZIO_MINTA = re.compile(r'^VERZIO\s*=\s*"([\d.]+)"', re.M)

with open(FORRAS, "r", encoding="utf-8") as f:
    tartalom = f.read()

talalat = BUILD_MINTA.search(tartalom)
if not talalat:
    raise SystemExit("Nem találom a BUILD_SZAM sort a zeneletolto.py-ban!")
helyi = int(talalat.group(1))

tavoli = helyi
try:
    keres = urllib.request.Request(TAVOLI_URL,
                                   headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(keres, context=ssl.create_default_context(),
                                timeout=15) as valasz:
        tavoli_forras = valasz.read().decode("utf-8", errors="replace")
    tavoli_talalat = BUILD_MINTA.search(tavoli_forras)
    if tavoli_talalat:
        tavoli = int(tavoli_talalat.group(1))
except Exception:
    pass          # nincs net / GitHub elérhetetlen - a helyi számból indulunk ki

uj = max(helyi, tavoli) + 1
tartalom = tartalom[:talalat.start(1)] + str(uj) + tartalom[talalat.end(1):]
with open(FORRAS, "w", encoding="utf-8") as f:
    f.write(tartalom)

# A verzió fő számait (pl. "2.0") a forrásból vesszük át, hogy a
# version_info.txt ne csússzon el tőle.
verzio = (VERZIO_MINTA.search(tartalom).group(1)
          if VERZIO_MINTA.search(tartalom) else "2.0")
fo, _, al = verzio.partition(".")
fo, al = int(fo or 2), int(al or 0)

with open(VERZIO_INFO, "r", encoding="utf-8") as f:
    vi = f.read()
vi = re.sub(r"filevers=\(\d+, \d+, \d+, 0\)", f"filevers=({fo}, {al}, {uj}, 0)", vi)
vi = re.sub(r"prodvers=\(\d+, \d+, \d+, 0\)", f"prodvers=({fo}, {al}, {uj}, 0)", vi)
vi = re.sub(r"(u'FileVersion', u')[\d.]+(')", rf"\g<1>{fo}.{al}.{uj}.0\g<2>", vi)
vi = re.sub(r"(u'ProductVersion', u')[\d.]+(')", rf"\g<1>{fo}.{al}.{uj}.0\g<2>", vi)
with open(VERZIO_INFO, "w", encoding="utf-8") as f:
    f.write(vi)

print(uj)
