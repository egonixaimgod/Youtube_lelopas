# -*- coding: utf-8 -*-
"""YouTube Letöltő - egyfájlos Tkinter kezelőfelület a yt-dlp köré.

A program magától letölti a yt-dlp-t és az ffmpeg-et, lekérdezi a videó
elérhető minőségeit, és letölti a kiválasztottat - vagy annak csak egy
megadott szakaszát (saját Range-alapú letöltővel, mert a YouTube a
szekvenciális olvasást fojtja).
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import concurrent.futures
import subprocess
import threading
import os
import re
import sys
import time
import json
import locale
import struct
import datetime
import traceback
import urllib.request
import urllib.error
import zipfile
import shutil

BUILD_SZAM = 14                 # a rebuild szkript növeli minden kiadásnál
PROGRAM_NEV = "YouTube Letöltő"

APP_MAPPA = os.path.join(os.getenv("LOCALAPPDATA", "."), "ZeneLetolto")
YTDLP_EXE = os.path.join(APP_MAPPA, "yt-dlp.exe")
FFMPEG_MAPPA = os.path.join(APP_MAPPA, "ffmpeg")
FFMPEG_EXE = os.path.join(FFMPEG_MAPPA, "ffmpeg.exe")
BEALLITAS_FAJL = os.path.join(APP_MAPPA, "beallitasok.json")

# ---------------------------------------------------------------- naplózás --

NAPLO_MAX_MERET = 5 * 1024 * 1024


def _naplo_alapmappa():
    """PyInstaller exe esetén az exe mappája, forrásból futtatva a szkripté."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


NAPLO_FAJL = os.path.join(_naplo_alapmappa(), "zeneletolto.log")
_naplo_zar = threading.Lock()


def fajlba_naplo(szoveg, lap=""):
    """Minden naplósort kiír a fájlba. Ha az exe mellé nem lehet írni
    (pl. Program Files), átvált a LOCALAPPDATA mappára."""
    global NAPLO_FAJL
    ido = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # A PID nélkül olvashatatlan a napló, ha egyszerre több példány fut:
    # a sorok összekeverednek, és úgy tűnik, mintha egy példány töltene kettőt.
    # A lapazonosító ugyanezért kell: egy példányon belül is futhat több
    # letöltés egyszerre, külön lapokon.
    sor = f"[{ido}][{os.getpid()}]{lap} {szoveg}\n"
    with _naplo_zar:
        for _ in range(2):
            try:
                if (os.path.isfile(NAPLO_FAJL)
                        and os.path.getsize(NAPLO_FAJL) > NAPLO_MAX_MERET):
                    regi = NAPLO_FAJL + ".1"
                    if os.path.isfile(regi):
                        os.remove(regi)
                    os.replace(NAPLO_FAJL, regi)
                # Új fájlnál BOM, hogy a Jegyzettömb is helyesen mutassa az ékezeteket
                uj = not os.path.isfile(NAPLO_FAJL) or os.path.getsize(NAPLO_FAJL) == 0
                with open(NAPLO_FAJL, "a",
                          encoding="utf-8-sig" if uj else "utf-8") as f:
                    f.write(sor)
                return
            except OSError:
                tartalek = os.path.join(APP_MAPPA, "zeneletolto.log")
                if NAPLO_FAJL == tartalek:
                    return
                NAPLO_FAJL = tartalek
                try:
                    os.makedirs(APP_MAPPA, exist_ok=True)
                except OSError:
                    return


def naplo_parancs(cimke, parancs, lap=""):
    """Parancssor naplózása. A googlevideo URL-ek aláírt tokent tartalmaznak és
    több ezer karakteresek, ezért azokat rövidítjük."""
    reszek = []
    for r in parancs:
        if len(r) > 160 and r.startswith("http"):
            reszek.append(r[:160] + f"...[+{len(r) - 160} karakter]")
        else:
            reszek.append(r)
    fajlba_naplo(f"{cimke}: {' '.join(reszek)}", lap)


def sor_dekodolas(nyers):
    """Alfolyamat kimenetének dekódolása.

    A yt-dlp néha a Windows kódlapján ír (ilyenkor az ékezetes cím
    olvashatatlan lenne a naplóban), ezért az utf-8 után azzal is próbálkozunk.
    """
    for kodolas in ("utf-8", locale.getpreferredencoding(False) or "cp1252"):
        try:
            return nyers.decode(kodolas)
        except (UnicodeDecodeError, LookupError):
            continue
    return nyers.decode("utf-8", "replace")


# ------------------------------------------------------------- beállítások --


def beallitasok_betoltes():
    """Elmentett felhasználói beállítások. Hiba esetén üres szótár."""
    try:
        with open(BEALLITAS_FAJL, "r", encoding="utf-8") as f:
            adat = json.load(f)
        return adat if isinstance(adat, dict) else {}
    except (OSError, ValueError):
        return {}


def beallitasok_mentes(adat):
    try:
        os.makedirs(APP_MAPPA, exist_ok=True)
        with open(BEALLITAS_FAJL, "w", encoding="utf-8") as f:
            json.dump(adat, f, ensure_ascii=False, indent=1)
    except (OSError, ValueError) as e:
        fajlba_naplo(f"beállítások mentése sikertelen: {e}")


# ------------------------------------------------------ eszközök letöltése --


def eszközök_letöltése(log_callback):
    os.makedirs(APP_MAPPA, exist_ok=True)

    if not os.path.isfile(YTDLP_EXE):
        log_callback("yt-dlp letöltése...")
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        tmp = YTDLP_EXE + ".tmp"
        try:
            urllib.request.urlretrieve(url, tmp)
            shutil.move(tmp, YTDLP_EXE)
        except Exception:
            if os.path.isfile(tmp):
                os.remove(tmp)
            raise
        log_callback("yt-dlp kész.")

    if not os.path.isfile(FFMPEG_EXE):
        log_callback("ffmpeg letöltése (ez eltarthat egy percig)...")
        os.makedirs(FFMPEG_MAPPA, exist_ok=True)
        zip_path = os.path.join(APP_MAPPA, "ffmpeg.zip")
        url = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl.zip")
        try:
            urllib.request.urlretrieve(url, zip_path)
        except Exception:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
            raise
        log_callback("ffmpeg kicsomagolása...")
        # Ideiglenes névre csomagolunk, és csak a végén nevezzük át: így egy
        # megszakadt kicsomagolás nem hagy csonka ffmpeg.exe-t, amit a program
        # legközelebb késznek hinne.
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                kicsomagolt = {}
                for member in z.namelist():
                    filename = os.path.basename(member)
                    if filename in ("ffmpeg.exe", "ffprobe.exe") and filename not in kicsomagolt:
                        cel = os.path.join(FFMPEG_MAPPA, filename)
                        tmp_cel = cel + ".tmp"
                        with z.open(member) as src, open(tmp_cel, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        kicsomagolt[filename] = (tmp_cel, cel)
            if "ffmpeg.exe" not in kicsomagolt:
                raise RuntimeError("A letöltött csomagban nincs ffmpeg.exe")
            for tmp_cel, cel in kicsomagolt.values():
                shutil.move(tmp_cel, cel)
        except Exception:
            for filename in ("ffmpeg.exe", "ffprobe.exe"):
                tmp_cel = os.path.join(FFMPEG_MAPPA, filename + ".tmp")
                if os.path.isfile(tmp_cel):
                    os.remove(tmp_cel)
            raise
        finally:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        log_callback("ffmpeg kész.")

    return True


YTDLP_FRISSITES_NAPOK = 7


def ytdlp_frissites(log_callback):
    """Hetente frissíti a yt-dlp-t.

    A YouTube gyakran változtat, egy hónapokkal régebbi yt-dlp egyszer csak nem
    tölt le semmit - ezért nem elég egyszer letölteni az exe-t.
    """
    if not os.path.isfile(YTDLP_EXE):
        return
    kor = time.time() - os.path.getmtime(YTDLP_EXE)
    if kor < YTDLP_FRISSITES_NAPOK * 86400:
        fajlba_naplo(f"yt-dlp kora {kor / 86400:.1f} nap, frissítés még nem esedékes.")
        return
    log_callback("yt-dlp frissítés keresése...")
    try:
        r = subprocess.run([YTDLP_EXE, "-U"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        kimenet = ((r.stdout or "") + (r.stderr or "")).strip()
        fajlba_naplo(f"yt-dlp -U (kód {r.returncode}): {kimenet}")
        if "Updated yt-dlp" in kimenet:
            log_callback("✅ yt-dlp frissítve a legújabb verzióra.")
        elif r.returncode == 0:
            # Ne próbálkozzon minden indításkor újra
            os.utime(YTDLP_EXE, None)
        else:
            log_callback("⚠️ A yt-dlp frissítése nem sikerült (a régi verzió marad).")
    except Exception as e:
        log_callback(f"⚠️ A yt-dlp frissítése nem sikerült: {e}")
        fajlba_naplo(traceback.format_exc())


# ------------------------------------------------------- szakasz-letöltés ---

# ------------------------------------------------------ önfrissítés ---------

GITHUB_REPO = "egonixaimgod/Youtube_lelopas"
FRISSITES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
FRISSITES_OLDAL = f"https://github.com/{GITHUB_REPO}/releases/latest"
FRISSITES_MAPPA = os.path.join(APP_MAPPA, "frissites")


def legujabb_kiadas():
    """A GitHubon közzétett legfrissebb build. -> (build_szam, exe_url).

    A kiadás címkéje `build-N` (ezt a rebuild szkript adja), az exe pedig
    mellékletként van feltöltve. Ha a címke nem ilyen alakú, inkább nem
    ajánlunk frissítést, mint hogy rosszat találjunk ki belőle.
    """
    keres = urllib.request.Request(FRISSITES_API, headers={
        "User-Agent": "YouTubeLetolto",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(keres, timeout=15) as valasz:
        adat = json.loads(valasz.read().decode("utf-8"))

    talalat = re.fullmatch(r"build-(\d+)", (adat.get("tag_name") or "").strip())
    if not talalat:
        return None, None
    exe_url = None
    for melleklet in adat.get("assets") or []:
        if (melleklet.get("name") or "").lower().endswith(".exe"):
            exe_url = melleklet.get("browser_download_url")
            break
    return int(talalat.group(1)), exe_url


def frissites_letoltes(url, cel, halad=None):
    """Az új exe letöltése. Csak akkor ad vissza sikert, ha tényleg program.

    A `halad` visszahívás 0-100 közötti százalékot kap."""
    os.makedirs(os.path.dirname(cel), exist_ok=True)
    tmp = cel + ".tmp"
    keres = urllib.request.Request(url, headers={"User-Agent": "YouTubeLetolto"})
    with urllib.request.urlopen(keres, timeout=60) as valasz:
        teljes = int(valasz.headers.get("Content-Length") or 0)
        kesz = 0
        with open(tmp, "wb") as f:
            while True:
                darab = valasz.read(256 * 1024)
                if not darab:
                    break
                f.write(darab)
                kesz += len(darab)
                if halad and teljes:
                    halad(kesz * 100.0 / teljes)

    # Mielőtt lecserélnénk vele a futó programot, győződjünk meg róla, hogy
    # nem egy hibaoldal vagy csonka fájl érkezett.
    with open(tmp, "rb") as f:
        eleje = f.read(2)
    meret = os.path.getsize(tmp)
    if eleje != b"MZ" or meret < 1024 * 1024:
        os.remove(tmp)
        raise RuntimeError(
            f"A letöltött fájl nem futtatható program ({meret} byte).")
    os.replace(tmp, cel)
    return cel


def tiszta_kornyezet():
    """Környezet a PyInstaller saját jelölései nélkül.

    A befagyasztott program környezetében ott van a `_PYI_APPLICATION_HOME_DIR`
    (a kicsomagolt `_MEI...` mappa) és a `_PYI_PARENT_PROCESS_LEVEL`. Ezeket a
    segédszkript, és rajta keresztül az általa indított ÚJ exe is örökölné - a
    bootloader ilyenkor azt hiszi, hogy ő egy már kicsomagolt gyerekfolyamat,
    és a régi (időközben törölt) mappából próbálja betölteni a python DLL-t:
    "Failed to load Python DLL ... LoadLibrary: a megadott modul nem található".
    Ezért az újraindításnál ezeket ki kell szedni.
    """
    return {k: v for k, v in os.environ.items()
            if not k.startswith("_PYI") and k != "_MEIPASS2"}


def frissites_telepites(uj_exe, cel_exe, pid=None):
    """A futó exe lecserélése és a program újraindítása.

    Windows alatt a futó exét nem lehet felülírni, ezért egy apró segéd-
    parancsfájl végzi: megvárja, míg ez a folyamat kilép, átmozgatja az új
    fájlt a régi helyére, elindítja, majd törli önmagát.
    """
    os.makedirs(FRISSITES_MAPPA, exist_ok=True)
    bat = os.path.join(FRISSITES_MAPPA, "frissit.bat")
    # A PID-re várni NEM elég: a onefile exe két folyamat (a kicsomagoló
    # bootloader és a Python gyerek), és az os.getpid() a gyereké. A szülő még
    # fogja az exe-t, amikor a gyerek már kilépett - ha ilyenkor cseréljük le,
    # a következő indulás félbeszakadt kicsomagolással és "Failed to load
    # Python DLL" hibával jár. Ezért a valódi feltétel az, hogy a fájl
    # cserélhető-e: addig próbáljuk, amíg sikerül.
    tartalom = (
        "@echo off\r\n"
        "setlocal enabledelayedexpansion\r\n"
        'set "PID=%~1"\r\n'
        "set /a n=0\r\n"
        ":varakozas\r\n"
        'tasklist /fi "pid eq %PID%" /nh 2>nul | find /i "%PID%" >nul\r\n'
        "if errorlevel 1 goto csere\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "set /a n+=1\r\n"
        "if !n! lss 30 goto varakozas\r\n"
        ":csere\r\n"
        "set /a n=0\r\n"
        ":csere_ismet\r\n"
        f'move /y "{uj_exe}" "{cel_exe}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto inditas\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "set /a n+=1\r\n"
        "if !n! lss 30 goto csere_ismet\r\n"
        # Ha egy percig sem engedte el senki, inkább nem indítunk el egy
        # félig lecserélt programot - a letöltött exe a helyén marad.
        "exit /b 1\r\n"
        ":inditas\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'start "" "{cel_exe}"\r\n'
        'del "%~f0"\r\n'
    )
    # A cmd.exe a rendszer kódlapján olvassa a parancsfájlt, nem utf-8-ban.
    # A newline="" nélkülözhetetlen: a szövegmód különben a saját \r\n-jeinket
    # \r\r\n-re fordítaná, amitől a cmd nem találja meg a `goto` címkéjét, és a
    # szkript némán kilép anélkül, hogy bármit lecserélne.
    with open(bat, "w", encoding=locale.getpreferredencoding(False),
              errors="replace", newline="") as f:
        f.write(tartalom)

    fajlba_naplo(f"frissítés telepítése: {uj_exe} -> {cel_exe}")
    # Csak CREATE_NO_WINDOW: a DETACHED_PROCESS-szel együtt a szkript konzol
    # nélkül maradna, és a tasklist/find/ping hívásai megbízhatatlanná
    # válnának. A gyerekfolyamat így is túléli a kilépésünket.
    subprocess.Popen(["cmd", "/c", bat, str(pid or os.getpid())],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
                     env=tiszta_kornyezet())


# A YouTube a szekvenciális GET-et erősen fojtja (~250 kB/s), a Range kéréseket
# viszont teljes sebességgel szolgálja ki. Az ffmpeg nem küld Range kérést
# seekeléskor, hanem végigolvassa a fájlt ("soft-seeking to offset ... by
# draining ..."), ezért egy 3 órás pozícióig órákig tartana eljutnia. Emiatt a
# szakaszt magunk töltjük le: a stream saját indexéből (mp4 -> sidx,
# webm -> Cues) kiszámoljuk a kért időtartomány byte-tartományát, azt darabolt
# Range kérésekkel leszedjük egy sparse fájlba, és abból vág az ffmpeg.
SZAKASZ_FEJLEC_MERET = 1 * 1024 * 1024   # ennyi byte-ból már kiolvasható az index
SZAKASZ_DARAB_MERET = 4 * 1024 * 1024    # egy Range kérés mérete
SZAKASZ_PARHUZAM = 4                     # egyszerre ennyi Range kérés fut
SZAKASZ_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


class UrlLejartHiba(Exception):
    """A googlevideo URL érvénytelenné vált (403/410) - újat kell kérni."""


def http_tartomany_info(url, kezd=None, veg=None, timeout=60, probalkozas=3):
    """HTTP Range kérés. -> (adat, teljes_fájlméret vagy None).

    `veg` bezárólag értendő, None esetén a fájl végéig. Az aláírt googlevideo
    URL-ek több GB-os letöltés közben érvénytelenné válhatnak - ilyenkor
    UrlLejartHiba jön, hogy a hívó frissíthesse az URL-t. A többi hálózati hiba
    átmeneti lehet, azt itt újrapróbáljuk.
    """
    utolso = None
    for kiserlet in range(probalkozas):
        keres = urllib.request.Request(url, headers={"User-Agent": SZAKASZ_UA})
        if kezd is not None:
            keres.add_header("Range", f"bytes={kezd}-{'' if veg is None else veg}")
        try:
            with urllib.request.urlopen(keres, timeout=timeout) as valasz:
                adat = valasz.read()
                teljes = None
                tartomany = valasz.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)\s*$", tartomany)
                if m:
                    teljes = int(m.group(1))
                elif valasz.headers.get("Content-Length") and kezd in (None, 0):
                    teljes = int(valasz.headers["Content-Length"])
                return adat, teljes
        except urllib.error.HTTPError as e:
            if e.code in (403, 410):
                raise UrlLejartHiba(f"HTTP {e.code}") from e
            utolso = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            utolso = e
        if kiserlet < probalkozas - 1:
            time.sleep(2 ** kiserlet)
    raise utolso


def http_tartomany(url, kezd=None, veg=None, timeout=60, probalkozas=3):
    """Mint a http_tartomany_info, de csak az adatot adja vissza."""
    return http_tartomany_info(url, kezd, veg, timeout, probalkozas)[0]


def mp4_sidx_olvasas(fejlec):
    """Fragmentált mp4 `sidx` indexének kiolvasása.

    Visszaad egy egységes indexet: {"tipus", "adat_kezd", "meret",
    "pontok": [(kezdo_ido_mp, byte_offset)]} - vagy None, ha a fájlban nincs
    sidx (pl. webm, vagy nem fragmentált mp4).
    """
    o = 0
    while o + 8 <= len(fejlec):
        (meret,) = struct.unpack(">I", fejlec[o:o + 4])
        tipus = fejlec[o + 4:o + 8]
        fej = 8
        if meret == 1:
            if o + 16 > len(fejlec):
                return None
            (meret,) = struct.unpack(">Q", fejlec[o + 8:o + 16])
            fej = 16
        if meret <= 0:
            return None

        if tipus == b"sidx":
            p = o + fej
            if p + 12 > len(fejlec):
                return None
            verzio = fejlec[p]
            p += 4                                   # verzió + flagek
            _, timescale = struct.unpack(">II", fejlec[p:p + 8])
            p += 8
            if verzio == 0:
                _, elso_offset = struct.unpack(">II", fejlec[p:p + 8])
                p += 8
            else:
                _, elso_offset = struct.unpack(">QQ", fejlec[p:p + 16])
                p += 16
            p += 2                                   # fenntartott
            (darab,) = struct.unpack(">H", fejlec[p:p + 2])
            p += 2
            if not timescale or not darab or p + darab * 12 > len(fejlec):
                return None
            adat_kezd = o + meret + elso_offset
            pontok = []
            poz, ido = adat_kezd, 0
            for _ in range(darab):
                r1, tartam, _r3 = struct.unpack(">III", fejlec[p:p + 12])
                p += 12
                pontok.append((ido / timescale, poz))
                poz += r1 & 0x7FFFFFFF
                ido += tartam
            return {"tipus": "mp4", "adat_kezd": adat_kezd,
                    "meret": poz, "pontok": pontok}

        # A sidx a moov után, az első moof előtt áll: ha idáig eljutottunk, nincs
        if tipus in (b"moof", b"mdat"):
            return None
        o += meret
    return None


# --- WebM / Matroska Cues index (a YouTube 4K/8K VP9 streamjei webm-ek) ---

EBML_SEGMENT = 0x18538067
EBML_INFO = 0x1549A966
EBML_TIMESTAMPSCALE = 0x2AD7B1
EBML_CUES = 0x1C53BB6B
EBML_CUEPOINT = 0xBB
EBML_CUETIME = 0xB3
EBML_CUETRACKPOS = 0xB7
EBML_CUECLUSTERPOS = 0xF1


def _ebml_vint(adat, o, id_mod=False):
    """EBML változó hosszú egész. -> (érték, új offset, ismeretlen_hossz)."""
    if o >= len(adat):
        return None, o, False
    elso = adat[o]
    if elso == 0:
        return None, o, False
    hossz, maszk = 1, 0x80
    while not elso & maszk:
        maszk >>= 1
        hossz += 1
        if hossz > 8:
            return None, o, False
    if o + hossz > len(adat):
        return None, o, False
    if id_mod:                                   # az azonosító a markerrel együtt
        return int.from_bytes(adat[o:o + hossz], "big"), o + hossz, False
    ertek = elso & (maszk - 1)
    ismeretlen = ertek == maszk - 1
    for b in adat[o + 1:o + hossz]:
        ertek = (ertek << 8) | b
        ismeretlen = ismeretlen and b == 0xFF
    return ertek, o + hossz, ismeretlen


def _ebml_elemek(adat, kezd, veg):
    """EBML elemek felsorolása egy szinten: (azonosító, adat_kezd, adat_vég)."""
    o = kezd
    while o < veg:
        azon, o2, _ = _ebml_vint(adat, o, True)
        if azon is None:
            return
        meret, o3, ismeretlen = _ebml_vint(adat, o2)
        if meret is None:
            return
        if ismeretlen:                 # a fájl végéig tart (streamelt Segment)
            yield azon, o3, veg
            return
        vege = o3 + meret
        yield azon, o3, min(vege, veg)
        if vege <= o:                  # védelem a végtelen ciklus ellen
            return
        o = vege


def _ebml_egesz(adat, kezd, veg):
    return int.from_bytes(adat[kezd:veg], "big") if veg > kezd else 0


def webm_cues_olvasas(fejlec):
    """Matroska/WebM `Cues` index kiolvasása, ugyanolyan alakban, mint a sidx.

    A YouTube webm_dash streamjeinél a Cues a fájl elején, az első Cluster
    előtt van, tehát ugyanúgy kiolvasható az első pár tíz kB-ból.
    """
    szegmens = None
    for azon, ek, ev in _ebml_elemek(fejlec, 0, len(fejlec)):
        if azon == EBML_SEGMENT:
            szegmens = (ek, ev)
            break
    if not szegmens:
        return None
    seg_kezd, seg_veg = szegmens

    timescale = 1000000                          # alapértelmezés: 1 ms
    pontok = []
    for azon, ek, ev in _ebml_elemek(fejlec, seg_kezd, seg_veg):
        if azon == EBML_INFO:
            for a2, k2, v2 in _ebml_elemek(fejlec, ek, ev):
                if a2 == EBML_TIMESTAMPSCALE:
                    timescale = _ebml_egesz(fejlec, k2, v2) or timescale
        elif azon == EBML_CUES:
            for a2, k2, v2 in _ebml_elemek(fejlec, ek, ev):
                if a2 != EBML_CUEPOINT:
                    continue
                ido = poz = None
                for a3, k3, v3 in _ebml_elemek(fejlec, k2, v2):
                    if a3 == EBML_CUETIME:
                        ido = _ebml_egesz(fejlec, k3, v3)
                    elif a3 == EBML_CUETRACKPOS and poz is None:
                        for a4, k4, v4 in _ebml_elemek(fejlec, k3, v3):
                            if a4 == EBML_CUECLUSTERPOS:
                                poz = _ebml_egesz(fejlec, k4, v4)
                                break
                if ido is not None and poz is not None:
                    pontok.append((ido * timescale / 1e9, seg_kezd + poz))
    if not pontok:
        return None
    pontok.sort(key=lambda p: p[1])
    return {"tipus": "webm", "adat_kezd": pontok[0][1], "meret": None,
            "pontok": pontok}


def media_index_olvasas(fejlec):
    """A stream időbélyeg -> byte index-e, konténertől függetlenül."""
    return mp4_sidx_olvasas(fejlec) or webm_cues_olvasas(fejlec)


WEBM_ELOLAP_MERET = 6 * 1024 * 1024


def webm_elolap_tartomany(index, byte_kezd):
    """A webm első klaszterének byte-tartománya (vagy None).

    Az mp4 `moov` boxa mindent leír a sávról, a Matroska `Tracks` viszont nem:
    az ffmpeg a fájl elejéről olvasott csomagokból állapítja meg a
    pixelformátumot. A lyukas fájl elején csupa nullát talál
    ("0x00 ... invalid as first byte of an EBML number"), amitől duplikált,
    hiányos streamet hoz létre, és a videósáv kimarad a kimenetből. Ezért az
    első klasztert is letöltjük - pár MB, és ettől a fájl az elejétől
    értelmezhető, a Cues alapján pedig ugyanúgy a kért helyre ugrik.
    """
    adat_kezd = index["adat_kezd"]
    if byte_kezd <= adat_kezd:
        return None                      # a kért szakasz amúgy is az elején van
    veg = None
    for _, offset in index["pontok"]:
        if offset - adat_kezd >= WEBM_ELOLAP_MERET:
            veg = offset                 # egész klaszterhatárig kérünk
            break
    veg = min(veg or adat_kezd + WEBM_ELOLAP_MERET, byte_kezd)
    return (adat_kezd, veg) if veg > adat_kezd else None


def index_byte_tartomany(index, teljes_meret, kezd_mp, veg_mp):
    """Időtartomány -> (byte_kezd, byte_veg).

    A szegmenshatárok kulcsképkockán vannak, ezért a kezdetet lefelé, a véget
    felfelé kerekítjük egész szegmensre - így az ffmpeg pontosan tud vágni.
    """
    pontok = index.get("pontok") or []
    if not pontok:
        return None
    byte_kezd = pontok[0][1]
    for ido, offset in pontok:
        if ido <= kezd_mp:
            byte_kezd = offset
        else:
            break
    byte_veg = index.get("meret") or teljes_meret
    for ido, offset in pontok:
        if ido >= veg_mp:
            byte_veg = offset
            break
    if not byte_veg or byte_veg <= byte_kezd:
        return None
    return byte_kezd, byte_veg


def sparse_fajl_letrehozas(utvonal, meret):
    """Létrehoz egy `meret` méretű sparse fájlt. False, ha nem sikerült.

    Ez nem opcionális optimalizáció: egy 8K videó stream 100 GB is lehet, és
    sparse támogatás nélkül (pl. exFAT pendrive) ennyi nullát írnánk a lemezre.
    Ezért ha a lyukas fájl nem jön össze, inkább jelezzük a hívónak.
    """
    with open(utvonal, "wb"):
        pass
    try:
        r = subprocess.run(["fsutil", "sparse", "setflag", utvonal],
                           capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            fajlba_naplo(f"fsutil sparse setflag sikertelen: {r.returncode}")
            return False
    except Exception as e:
        fajlba_naplo(f"fsutil sparse setflag hiba: {e}")
        return False

    with open(utvonal, "r+b") as f:
        f.seek(meret - 1)
        f.write(b"\0")

    # Ellenőrzés: tényleg lyukas lett? (a fájlrendszer felülbírálhatja a flaget)
    try:
        r = subprocess.run(["fsutil", "sparse", "queryflag", utvonal],
                           capture_output=True, text=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if "sparse" not in (r.stdout or "").lower():
            fajlba_naplo(f"a fájl nem sparse: {r.stdout!r}")
            return False
    except Exception:
        pass
    return True


# ------------------------------------------------------------- segédfüggvények --


def ido_ertelmezes(szoveg):
    """'1:20:30' / '20:30' / '90' -> másodperc. None ha érvénytelen."""
    szoveg = (szoveg or "").strip().replace(".", ":").replace(",", ":")
    if not szoveg:
        return None
    reszek = szoveg.split(":")
    if len(reszek) > 3:
        return None
    try:
        szamok = [int(r) for r in reszek]
    except ValueError:
        return None
    if any(sz < 0 for sz in szamok):
        return None
    # A perc/másodperc mezők nem lehetnek 59-nél nagyobbak (kivéve a legelsőt)
    if len(szamok) > 1 and any(sz > 59 for sz in szamok[1:]):
        return None
    mp = 0
    for sz in szamok:
        mp = mp * 60 + sz
    return mp


def ido_formazas(mp):
    """Másodperc -> '1:20:30' vagy '20:30'."""
    mp = int(mp)
    perc, s = divmod(mp, 60)
    ora, perc = divmod(perc, 60)
    if ora:
        return f"{ora}:{perc:02d}:{s:02d}"
    return f"{perc}:{s:02d}"


def meret_formazas(byte_szam):
    """Byte -> '18.7 GB' / '243 MB' / '-'."""
    if not byte_szam:
        return "–"
    mb = byte_szam / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    if mb >= 10:
        return f"{mb:.0f} MB"
    return f"{mb:.1f} MB"


def fajlnev_tisztitas(nev):
    """Windows-on tiltott karakterek cseréje."""
    nev = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nev or "video")
    nev = nev.strip(" .") or "video"
    return nev[:120]


def url_rendbetetel(url):
    """Elgépelt / séma nélküli linkek megbocsátása."""
    url = (url or "").strip().strip('"').strip("'")
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        if re.match(r"^(www\.|[\w-]+\.[a-z]{2,})", url):
            url = "https://" + url
    return url


# --------------------------------------------------------- yt-dlp lekérdezés --

YTDLP_TIMEOUT = 90


def _ytdlp_kornyezet():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def formatumok_lekerese(url, extra=None, lap=""):
    """Lekéri az elérhető formátumokat yt-dlp -j segítségével.

    `extra` további kapcsolókat fűz be - a szakaszletöltő ezzel adja át a
    -S/-f kapcsolókat, hogy visszakapja a feloldott `requested_formats`
    listát a közvetlen stream URL-ekkel.
    """
    parancs = [YTDLP_EXE, "-j", "--no-playlist"] + (extra or []) + [url]
    naplo_parancs("yt-dlp lekérdezés", parancs, lap)
    try:
        result = subprocess.run(
            parancs, capture_output=True, text=True, encoding="utf-8",
            errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
            env=_ytdlp_kornyezet(), timeout=YTDLP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"A yt-dlp nem válaszolt {YTDLP_TIMEOUT} másodpercen belül."
    if result.returncode != 0:
        return None, ytdlp_hiba_forditas(result.stderr)
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return None, f"Érvénytelen válasz a yt-dlp-től: {e}"
    return info, None


def ytdlp_hiba_forditas(stderr):
    """A yt-dlp hibaüzenetéből érthető magyar mondat."""
    szoveg = (stderr or "").strip()
    minta = [
        ("Video unavailable", "A videó nem elérhető (törölték vagy régiózárt)."),
        ("Private video", "Ez egy privát videó, nem lehet letölteni."),
        ("members-only", "Ez a videó csak csatornatagoknak érhető el."),
        ("age", "Korhatáros videó - bejelentkezés nélkül nem tölthető le."),
        ("confirm you're not a bot", "A YouTube robotellenőrzést kér erre a videóra."),
        ("Sign in", "A YouTube bejelentkezést kér ehhez a videóhoz."),
        ("Unsupported URL", "Ezt az oldalt a yt-dlp nem ismeri."),
        ("Incomplete YouTube ID", "Hiányos a videó azonosítója - ellenőrizd a linket."),
        ("is not a valid URL", "Ez nem érvényes link."),
        ("Requested format is not available", "A kért formátum már nem elérhető."),
        ("HTTP Error 429", "A YouTube átmenetileg letiltotta a gépedet (túl sok kérés). Várj pár percet."),
        ("Temporary failure in name resolution", "Nincs internetkapcsolat."),
        ("getaddrinfo failed", "Nincs internetkapcsolat."),
    ]
    for kulcs, uzenet in minta:
        if kulcs.lower() in szoveg.lower():
            return uzenet
    # Az utolsó ERROR: sor általában a lényeg
    hibak = [s for s in szoveg.splitlines() if "ERROR:" in s]
    if hibak:
        return hibak[-1].split("ERROR:", 1)[1].strip()
    return szoveg.splitlines()[-1] if szoveg.splitlines() else "Ismeretlen hiba."


# ------------------------------------------------------ formátum-elemzés --

# A YouTube minőségi szintje NEM azonos a pixelmagassággal: egy 21:9-es
# ultrawide videónál a 3440x1440 az, amit a YouTube 2160p-nek (4K) hív.
# Ezért a `format_note` mezőt tekintjük mérvadónak, és csak utána a magasságot.
SZINT_NEVEK = {
    4320: "8K", 2880: "5K", 2160: "4K", 1440: "2K",
    1080: "Full HD", 720: "HD",
}


SZABVANYOS_SZINTEK = (144, 240, 360, 480, 720, 1080, 1440, 2160, 2880, 4320)


def _szintre_illesztes(becsles):
    """A becsült szintet a legközelebbi szabványos lépcsőre húzza, ha közel van.

    Egy 3440x1440-es ultrawide 16:9-re vetítve 1935 - ez valójában a 2160p
    lépcső, és a felhasználónak "4K"-ként kell látnia, nem "1935p"-ként.
    """
    if not becsles:
        return 0
    legkozelebbi = min(SZABVANYOS_SZINTEK, key=lambda sz: abs(sz - becsles))
    if abs(legkozelebbi - becsles) <= legkozelebbi * 0.15:
        return legkozelebbi
    return int(becsles)


def minoseg_szint(f):
    """A formátum YouTube szerinti minőségi szintje (pl. 2160).

    A `format_note` a mérvadó: a YouTube egy 21:9-es 3440x1440-es videót
    2160p-nek (4K) hív, hiába 1440 a pixelmagassága."""
    m = re.match(r"\s*(\d{3,4})p", str(f.get("format_note") or ""))
    if m:
        return int(m.group(1))
    magassag = f.get("height") or 0
    szelesseg = f.get("width") or 0
    if not szelesseg or not magassag:
        return magassag
    if magassag > szelesseg:                     # álló videó (pl. Shorts)
        return _szintre_illesztes(szelesseg)
    if szelesseg / magassag > 1.9:               # ultrawide
        return _szintre_illesztes(szelesseg * 9 / 16)
    return magassag


def protokoll_rangsor(f):
    """https/DASH > HLS. A YouTube HLS változata ugyanaz a stream, de a
    bitrátája hamisan magas, nincs benne byte-index, és lassabban is jön."""
    proto = str(f.get("protocol") or "")
    if proto.startswith("http") and "m3u8" not in proto:
        return 2
    if "m3u8" in proto:
        return 0
    return 1


def valodi_video_formatum(f):
    """Igazi videósáv-e? (a storyboard képsorozatokat ki kell szűrni)"""
    if (f.get("ext") or "") == "mhtml" or "mhtml" in str(f.get("protocol") or ""):
        return False
    if str(f.get("format_note") or "").lower() == "storyboard":
        return False
    vcodec = f.get("vcodec")
    if vcodec in ("none", "images"):
        return False
    return bool(f.get("height"))


def valodi_hang_formatum(f):
    if (f.get("ext") or "") == "mhtml":
        return False
    return (f.get("acodec") or "none") != "none"


def kodek_nev(kod):
    """'vp09.00.51.08' -> 'VP9', 'avc1.640028' -> 'H.264'."""
    kod = (kod or "").split(".")[0].lower()
    return {"vp09": "VP9", "vp9": "VP9", "av01": "AV1", "avc1": "H.264",
            "avc3": "H.264", "hev1": "H.265", "hvc1": "H.265",
            "mp4a": "AAC", "opus": "Opus", "vorbis": "Vorbis",
            "ec-3": "E-AC3", "ac-3": "AC3"}.get(kod, kod.upper() or "?")


def legjobb_hang(formats, mp4_baratsagos=True):
    """A legjobb hangsáv kiválasztása.

    - a külön hangsáv jobb, mint a kombinált (Twitch/egyéb oldalak miatt kell)
    - a DRC (dinamikatömörített) változat rosszabb, csak végszükségben
    - mp4 kimenethez az m4a/AAC a biztonságos (az Opus mp4-ben döcög)
    """
    jeloltek = [f for f in formats if valodi_hang_formatum(f)]
    if not jeloltek:
        return None

    def kulcs(f):
        csak_hang = (f.get("vcodec") or "none") == "none"
        drc = "drc" in str(f.get("format_id") or "").lower()
        m4a = (f.get("ext") or "") == "m4a"
        abr = f.get("abr") or f.get("tbr") or 0
        return (csak_hang, protokoll_rangsor(f), not drc,
                m4a if mp4_baratsagos else True, abr)

    return max(jeloltek, key=kulcs)


def formatum_csoportositas(info):
    """A formátumlistából minőségi szintenként egy ajánlott sor.

    Visszaad egy listát (növekvő minőség szerint) ilyen elemekkel:
    {szint, nev, felbontas, fps, kodek, meret, meret_szoveg, format_id,
     height, hang_id, hdr, leiras}
    """
    formats = info.get("formats") or []
    video_fmts = [f for f in formats if valodi_video_formatum(f)]
    hang = legjobb_hang(formats)

    # 1. lépés: felbontásonként (szélesség x magasság x fps) egy győztes.
    # Ugyanaz a stream ugyanis kétszer is szerepel a listában - https DASH és
    # HLS változatban -, és a HLS-nek hamisan magas a bitrátája. Enélkül a
    # kettő két külön "minőségként" jelenne meg.
    rendites_map = {}
    for f in video_fmts:
        kulcs = (f.get("width") or 0, f.get("height") or 0, f.get("fps") or 0)
        # https > HLS, külön videósáv > kombinált (annak rosszabb a hangja),
        # és csak legvégül a bitráta.
        rang = (protokoll_rangsor(f),
                (f.get("acodec") or "none") == "none",
                f.get("tbr") or f.get("vbr") or 0)
        elozo = rendites_map.get(kulcs)
        if elozo is None or rang > elozo[0]:
            rendites_map[kulcs] = (rang, f)

    # 2. lépés: minőségi szintenként a legnagyobb felbontású győztes.
    szint_map = {}
    for _, f in rendites_map.values():
        szint = minoseg_szint(f)
        if not szint:
            continue
        kulcs = ((f.get("width") or 0) * (f.get("height") or 0),
                 f.get("fps") or 0, f.get("tbr") or f.get("vbr") or 0)
        elozo = szint_map.get(szint)
        if elozo is None or kulcs > elozo[0]:
            szint_map[szint] = (kulcs, f)

    eredmeny = []
    for szint in sorted(szint_map):
        f = szint_map[szint][1]
        magassag = f.get("height") or szint
        szelesseg = f.get("width")
        fps = f.get("fps")
        if isinstance(fps, float) and fps == int(fps):
            fps = int(fps)
        meret = f.get("filesize") or f.get("filesize_approx")
        sajat_hang = (f.get("acodec") or "none") != "none"
        hdr = "hdr" in str(f.get("dynamic_range") or "").lower()

        cimke = SZINT_NEVEK.get(szint)
        nev = f"{szint}p" + (f"{fps}" if fps and fps > 30 else "")
        if cimke:
            nev = f"{cimke} · {nev}"
        if hdr:
            nev += " HDR"

        eredmeny.append({
            "szint": szint,
            "nev": nev,
            "felbontas": f"{szelesseg}×{magassag}" if szelesseg else f"{magassag}p",
            "fps": str(fps) if fps else "–",
            "kodek": kodek_nev(f.get("vcodec")),
            "meret": meret,
            "meret_szoveg": meret_formazas(meret),
            "format_id": f.get("format_id"),
            "height": magassag,
            "ext": f.get("ext"),          # a forrás konténere (webm / mp4)
            "hdr": hdr,
            "hang_id": None if sajat_hang or not hang else hang.get("format_id"),
            "hang_leiras": hang_leiras(hang if not sajat_hang else f),
            "leiras": (f"{nev} · {szelesseg or '?'}×{magassag} · "
                       f"{kodek_nev(f.get('vcodec'))}"
                       + (f" · {fps}fps" if fps else "")
                       + f" · {meret_formazas(meret)}"),
        })
    return eredmeny


def hang_leiras(f):
    if not f:
        return "–"
    abr = f.get("abr") or f.get("tbr") or 0
    return f"{kodek_nev(f.get('acodec'))} {abr:.0f} kbps" if abr else kodek_nev(f.get("acodec"))


# Az MP3 kimenet választható bitrátái. A 320 kbps az MP3 szabvány maximuma.
# A valódi korlát viszont a forrás (a YouTube Opusban ~130 kbps-t ad): efölött a
# nagyobb bitráta már csak a fájlt hizlalja, minőséget nem ad hozzá - ezért
# szerepel a forrás is a listában, hogy a választás tájékozott legyen.
MP3_FOKOZATOK = (
    (128, "128 kbps", "128K", "CBR"),
    (192, "192 kbps", "192K", "CBR"),
    (245, "V0 · ~245 kbps", "0", "VBR"),
    (256, "256 kbps", "256K", "CBR"),
    (320, "320 kbps · maximum", "320K", "CBR"),
)


def mp3_valasztekok(info):
    """Az MP3 kimenet minőségei, növekvő sorrendben (a legjobb az utolsó).

    A forrás hangsávot mindig a legjobb elérhető adja - abból rosszabbat
    választani semmilyen célt nem szolgálna -, itt csak a kimenet bitrátája
    választható.
    """
    forras = legjobb_hang(info.get("formats") or [], mp4_baratsagos=False)
    forras_szoveg = hang_leiras(forras)
    hossz = info.get("duration") or 0
    eredmeny = []
    for kbps, nev, minoseg, tipus in MP3_FOKOZATOK:
        meret = int(kbps * 1000 / 8 * hossz) if hossz else None
        eredmeny.append({
            "nev": nev,
            "tipus": tipus,
            "minoseg": minoseg,          # yt-dlp --audio-quality / ffmpeg érték
            "kbps": kbps,
            "forras": forras_szoveg,
            "meret": meret,
            "meret_szoveg": meret_formazas(meret),
            "leiras": f"MP3 {nev} ({tipus}) · forrás: {forras_szoveg}",
        })
    return eredmeny


# ------------------------------------------------------ haladás-értelmezés --

# [download]  42.3% of ~  43.14GiB at    5.43MiB/s ETA 03:21
HALADAS_MINTA = re.compile(
    r"\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+~?\s*([\d.]+\s*\w+)"
    r"(?:\s+at\s+([\d.]+\s*\w+/s))?(?:\s+ETA\s+([\d:]+))?", re.I)


def haladas_ertelmezes(sor):
    """yt-dlp haladás-sor -> (százalék, méret, sebesség, hátralévő) vagy None."""
    m = HALADAS_MINTA.search(sor)
    if not m:
        return None
    szazalek = float(m.group(1))
    return szazalek, m.group(2), (m.group(3) or "").strip(), (m.group(4) or "").strip()


# =============================================================== FELÜLET ====

SZIN = {
    "hatter":   "#0e1015",
    "panel":    "#161922",
    "panel2":   "#1d2130",
    "mezo":     "#0b0d13",
    "keret":    "#272c3a",
    "keret2":   "#343a4d",
    "szoveg":   "#e7eaf3",
    "halvany":  "#98a2b8",
    "halvany2": "#6b7488",
    "kiemel":   "#4f8cff",
    "kiemel2":  "#6d9fff",
    "siker":    "#3ddc97",
    "figyelem": "#f7b955",
    "hiba":     "#ff6b6b",
    "lila":     "#a78bfa",
}

BETU = "Segoe UI"


class Gomb(tk.Button):
    """Lapos, hover-effektes gomb - a tk.Button az egyetlen, aminek Windows
    alatt megbízhatóan állítható minden színe."""

    STILUSOK = {
        "elsodleges": (SZIN["kiemel"], "#ffffff", SZIN["kiemel2"]),
        "masodlagos": (SZIN["panel2"], SZIN["szoveg"], SZIN["keret2"]),
        "csendes":    (SZIN["panel"], SZIN["halvany"], SZIN["panel2"]),
        "veszely":    ("#3a2028", SZIN["hiba"], "#4d2a34"),
        "siker":      (SZIN["siker"], "#0b2b1e", "#5ce8ac"),
    }

    def __init__(self, szulo, szoveg, parancs=None, stilus="masodlagos",
                 meret=10, vastag=False, **kw):
        hatter, elotr, hover = self.STILUSOK[stilus]
        self._hatter, self._hover = hatter, hover
        kw.setdefault("padx", 14)
        kw.setdefault("pady", 7)
        super().__init__(szulo, text=szoveg, command=parancs,
                         font=(BETU, meret, "bold" if vastag else "normal"),
                         bg=hatter, fg=elotr, activebackground=hover,
                         activeforeground=elotr, relief="flat", bd=0,
                         highlightthickness=0, cursor="hand2",
                         disabledforeground=SZIN["halvany2"], **kw)
        self.bind("<Enter>", self._be)
        self.bind("<Leave>", self._ki)

    def configure(self, cnf=None, **kw):
        # Letiltva a saját (pl. kék) háttér félrevezető: tompítjuk, hogy
        # ránézésre is látszódjon, hogy a gomb most nem használható.
        if "state" in kw and "bg" not in kw:
            kw["bg"] = (SZIN["panel2"] if str(kw["state"]) == "disabled"
                        else self._hatter)
        return super().configure(cnf, **kw)

    config = configure

    def _be(self, _=None):
        if str(self["state"]) != "disabled":
            self.config(bg=self._hover)

    def _ki(self, _=None):
        self.config(bg=self._hatter)

    def stilus_valt(self, stilus):
        hatter, elotr, hover = self.STILUSOK[stilus]
        self._hatter, self._hover = hatter, hover
        self.config(bg=hatter, fg=elotr, activebackground=hover,
                    activeforeground=elotr)


class Mezo(tk.Frame):
    """Beviteli mező fókuszgyűrűvel (a keretet a szülő Frame adja)."""

    def __init__(self, szulo, szoveg_valtozo=None, meret=11, mono=False, **kw):
        super().__init__(szulo, bg=SZIN["keret"], padx=1, pady=1,
                         highlightthickness=0)
        self.entry = tk.Entry(
            self, textvariable=szoveg_valtozo, relief="flat", bd=0,
            font=("Consolas" if mono else BETU, meret),
            bg=SZIN["mezo"], fg=SZIN["szoveg"], insertbackground=SZIN["kiemel"],
            disabledbackground=SZIN["panel"], disabledforeground=SZIN["halvany2"],
            selectbackground=SZIN["kiemel"], selectforeground="#ffffff", **kw)
        self.entry.pack(fill="both", expand=True, ipady=6, padx=8)
        self.entry.bind("<FocusIn>", lambda e: self.config(bg=SZIN["kiemel"]))
        self.entry.bind("<FocusOut>", lambda e: self.config(bg=SZIN["keret"]))

    # kényelmi átvezetések
    def get(self):
        return self.entry.get()

    def set(self, ertek):
        allapot = str(self.entry["state"])
        self.entry.config(state="normal")
        self.entry.delete(0, "end")
        self.entry.insert(0, ertek)
        self.entry.config(state=allapot)

    def allapot(self, allapot):
        self.entry.config(state=allapot)


class TartomanySav(tk.Canvas):
    """Két fogantyús csúszka: ezzel jelöli ki a felhasználó a részletet.

    A Tk-ban nincs ilyen elem, ezért vászonra rajzoljuk. A két fogantyú nem
    tudja átlépni egymást, és mindig marad köztük legalább egy másodperc.
    """

    FOGANTYU = 9
    PEREM = 18
    SAV_Y = 28

    def __init__(self, szulo, valtozas=None):
        super().__init__(szulo, bg=SZIN["panel"], height=62, highlightthickness=0,
                         bd=0)
        self.hossz = 0
        self.kezd = 0
        self.veg = 0
        self.aktiv = False
        self._huzott = None
        self._valtozas = valtozas

        self.bind("<Configure>", lambda e: self._rajzol())
        self.bind("<Button-1>", self._lenyomas)
        self.bind("<B1-Motion>", self._huzas)
        self.bind("<ButtonRelease-1>", self._elenged)
        self.bind("<Key-Left>", lambda e: self._leptet(-1))
        self.bind("<Key-Right>", lambda e: self._leptet(1))

    # -- állapot --

    def beallit(self, hossz, kezd=None, veg=None, ertesit=True):
        self.hossz = max(int(hossz or 0), 0)
        if self.hossz:
            self.kezd = max(0, min(int(kezd if kezd is not None else 0), self.hossz - 1))
            self.veg = max(self.kezd + 1,
                           min(int(veg if veg is not None else self.hossz), self.hossz))
        else:
            self.kezd = self.veg = 0
        self._rajzol()
        if ertesit and self._valtozas:
            self._valtozas(self.kezd, self.veg)

    def engedelyez(self, aktiv):
        self.aktiv = bool(aktiv) and self.hossz > 0
        self.config(cursor="hand2" if self.aktiv else "")
        self._rajzol()

    # -- koordináta-átváltás --

    def _x(self, mp):
        szel = max(self.winfo_width(), 2 * self.PEREM + 10)
        if not self.hossz:
            return self.PEREM
        return self.PEREM + (szel - 2 * self.PEREM) * mp / self.hossz

    def _mp(self, x):
        szel = max(self.winfo_width(), 2 * self.PEREM + 10)
        arany = (x - self.PEREM) / max(szel - 2 * self.PEREM, 1)
        return int(round(max(0.0, min(1.0, arany)) * self.hossz))

    # -- egérkezelés --

    def _lenyomas(self, esemeny):
        if not self.aktiv:
            return
        self.focus_set()
        tav_kezd = abs(esemeny.x - self._x(self.kezd))
        tav_veg = abs(esemeny.x - self._x(self.veg))
        self._huzott = "kezd" if tav_kezd <= tav_veg else "veg"
        self._huzas(esemeny)

    def _huzas(self, esemeny):
        if not self.aktiv or not self._huzott:
            return
        ertek = self._mp(esemeny.x)
        if self._huzott == "kezd":
            self.kezd = min(ertek, self.veg - 1)
        else:
            self.veg = max(ertek, self.kezd + 1)
        self._rajzol()
        if self._valtozas:
            self._valtozas(self.kezd, self.veg)

    def _elenged(self, _=None):
        self._huzott = None

    def _leptet(self, irany):
        if not self.aktiv:
            return
        lepes = max(1, self.hossz // 200) * irany
        if self._huzott == "veg":
            self.veg = max(self.kezd + 1, min(self.hossz, self.veg + lepes))
        else:
            self.kezd = max(0, min(self.veg - 1, self.kezd + lepes))
        self._rajzol()
        if self._valtozas:
            self._valtozas(self.kezd, self.veg)

    # -- rajzolás --

    def _rajzol(self):
        self.delete("all")
        szel = self.winfo_width()
        if szel < 30:
            return
        y = self.SAV_Y
        bal, jobb = self.PEREM, szel - self.PEREM
        sav_szin = SZIN["panel2"] if self.aktiv else "#191c25"
        self.create_line(bal, y, jobb, y, fill=sav_szin, width=6,
                         capstyle="round")
        if not self.hossz:
            self.create_text(szel / 2, y - 16, text="előbb elemezd a videót",
                             fill=SZIN["halvany2"], font=(BETU, 8))
            return

        x1, x2 = self._x(self.kezd), self._x(self.veg)
        self.create_line(x1, y, x2, y, width=6, capstyle="round",
                         fill=SZIN["kiemel"] if self.aktiv else SZIN["keret2"])

        # feliratok: középen a szakasz hossza, a fogantyúk alatt a két időpont
        szoveg_szin = SZIN["szoveg"] if self.aktiv else SZIN["halvany2"]
        self.create_text(min(max((x1 + x2) / 2, bal + 24), jobb - 24), y - 17,
                         text=ido_formazas(self.veg - self.kezd),
                         fill=SZIN["kiemel"] if self.aktiv else SZIN["halvany2"],
                         font=(BETU, 8, "bold"))
        # Ha a két fogantyú közel van, a feliratok egymásra csúsznának: ilyenkor
        # kifelé húzzuk őket.
        szuk = (x2 - x1) < 110
        self.create_text(max(x1 - (8 if szuk else 0), bal), y + 17,
                         text=ido_formazas(self.kezd), fill=szoveg_szin,
                         font=("Consolas", 8), anchor="e" if szuk else "center")
        self.create_text(min(x2 + (8 if szuk else 0), jobb), y + 17,
                         text=ido_formazas(self.veg), fill=szoveg_szin,
                         font=("Consolas", 8), anchor="w" if szuk else "center")
        for x in (x1, x2):
            r = self.FOGANTYU
            self.create_oval(x - r, y - r, x + r, y + r,
                             fill=SZIN["szoveg"] if self.aktiv else SZIN["keret2"],
                             outline=SZIN["kiemel"] if self.aktiv else SZIN["keret"],
                             width=2)


class FrissitesAblak(tk.Toplevel):
    """Értesítés új buildről, letöltés és telepítés."""

    def __init__(self, alk, uj_build, exe_url):
        super().__init__(alk, bg=SZIN["panel"])
        self.alk = alk
        self.uj_build = uj_build
        self.exe_url = exe_url
        self.folyamatban = False

        self.title("Frissítés")
        self.configure(padx=24, pady=20)
        self.resizable(False, False)
        self.transient(alk)

        tk.Label(self, text="Új verzió érhető el", bg=SZIN["panel"],
                 fg=SZIN["szoveg"], font=(BETU, 14, "bold")).pack(anchor="w")
        tk.Label(self, bg=SZIN["panel"], fg=SZIN["halvany"], font=(BETU, 10),
                 justify="left",
                 text=(f"Jelenlegi: build {BUILD_SZAM}\n"
                       f"Elérhető:  build {uj_build}")).pack(anchor="w", pady=(10, 0))

        self.allapot = tk.Label(self, bg=SZIN["panel"], fg=SZIN["halvany2"],
                                font=(BETU, 9), anchor="w", justify="left")
        self.allapot.pack(anchor="w", fill="x", pady=(12, 0))

        self.sav = ttk.Progressbar(self, style="Fo.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100, length=360)

        # A jelölő a mentett beállítást tükrözi: ha egyszer bepipálták, akkor
        # legközelebb - például a lábléc jelzéséről előhozva - is bepipálva
        # nyílik. Kipipálva viszont vissza is kapcsolható az értesítés.
        self.mellozes = tk.BooleanVar(
            value=not alk.beallitas.get("frissites_ertesites", True))
        tk.Checkbutton(self, text="  Ne jelenjen meg többé",
                       variable=self.mellozes, bg=SZIN["panel"],
                       activebackground=SZIN["panel"], fg=SZIN["halvany"],
                       activeforeground=SZIN["szoveg"], selectcolor=SZIN["panel2"],
                       bd=0, highlightthickness=0, cursor="hand2",
                       font=(BETU, 9)).pack(anchor="w", pady=(14, 0))

        gombok = tk.Frame(self, bg=SZIN["panel"])
        gombok.pack(anchor="e", pady=(16, 0))
        self.kesobb_gomb = Gomb(gombok, "Később", self._bezar, "masodlagos")
        self.kesobb_gomb.pack(side="right")
        self.frissit_gomb = Gomb(gombok, "Telepítés", self._inditas,
                                 "elsodleges", vastag=True)
        self.frissit_gomb.pack(side="right", padx=(0, 8))
        if not getattr(sys, "frozen", False):
            # Forrásból futtatva nincs mit lecserélni.
            self.allapot.config(
                text="Forrásból futtatva a csere nem automatikus –\n"
                     "a Telepítés a letöltési oldalt nyitja meg.")

        self.protocol("WM_DELETE_WINDOW", self._bezar)
        self.update_idletasks()
        self._kozepre()
        self.grab_set()

    def _kozepre(self):
        sz, m = self.winfo_width(), self.winfo_height()
        x = self.alk.winfo_rootx() + (self.alk.winfo_width() - sz) // 2
        y = self.alk.winfo_rooty() + (self.alk.winfo_height() - m) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _mellozes_mentese(self):
        """A jelölő állapotát mindkét gomb menti (Telepítés és Később).

        Nem csak bekapcsolni lehet vele: a pipa kivétele visszakapcsolja az
        értesítést."""
        ertesites = not self.mellozes.get()
        if ertesites != self.alk.beallitas.get("frissites_ertesites", True):
            self.alk.beallitasok_mentese(frissites_ertesites=ertesites)
            fajlba_naplo("frissítési értesítés: "
                         + ("bekapcsolva" if ertesites else "kikapcsolva"))

    def _bezar(self, mentes=True):
        """A `mentes=False` csak akkor kell, ha a hívó már mentett."""
        if self.folyamatban:
            return
        if mentes:
            self._mellozes_mentese()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _inditas(self):
        # A jelölő a Telepítés gombnál is számít.
        self._mellozes_mentese()
        if not getattr(sys, "frozen", False):
            os.startfile(FRISSITES_OLDAL)
            self._bezar(mentes=False)
            return
        if not self.exe_url:
            messagebox.showwarning(
                "Frissítés",
                "A kiadáshoz nincs feltöltve program-fájl.\n"
                "Töltsd le kézzel a GitHubról.", parent=self)
            return
        futo = [l for l in self.alk.lapok if l.letolt_fut]
        if futo and not messagebox.askyesno(
                "Frissítés",
                f"{len(futo)} lapon még fut letöltés, és a frissítéshez a "
                f"program újraindul.\nMegszakítod és frissítesz?", parent=self):
            return

        self.folyamatban = True
        self.frissit_gomb.config(state="disabled")
        self.kesobb_gomb.config(state="disabled")
        self.sav.pack(fill="x", pady=(8, 0), before=self.allapot)
        self.allapot.config(text="Letöltés…", fg=SZIN["halvany"])
        threading.Thread(target=self._letoltes_worker, daemon=True).start()

    def _letoltes_worker(self):
        cel = os.path.join(FRISSITES_MAPPA, "YouTube Letolto.exe")
        try:
            frissites_letoltes(self.exe_url, cel, self._halad)
        except Exception as e:
            fajlba_naplo(f"frissítés letöltése sikertelen: {e}")
            self.alk._fo_szalon(self._hiba, str(e))
            return
        self.alk._fo_szalon(self._telepites, cel)

    def _halad(self, szazalek):
        self.alk._fo_szalon(self._halad_kiiras, szazalek)

    def _halad_kiiras(self, szazalek):
        try:
            self.sav["value"] = szazalek
            self.allapot.config(text=f"Letöltés… {szazalek:.0f}%")
        except tk.TclError:
            pass

    def _hiba(self, uzenet):
        self.folyamatban = False
        try:
            self.allapot.config(text=f"Nem sikerült: {uzenet}", fg=SZIN["hiba"])
            self.frissit_gomb.config(state="normal")
            self.kesobb_gomb.config(state="normal")
            self.sav.pack_forget()
        except tk.TclError:
            pass

    def _telepites(self, uj_exe):
        self.allapot.config(text="Telepítés – a program újraindul…",
                            fg=SZIN["siker"])
        self.update_idletasks()
        try:
            frissites_telepites(uj_exe, os.path.abspath(sys.executable))
        except Exception as e:
            fajlba_naplo(f"frissítés telepítése sikertelen: {e}")
            self._hiba(str(e))
            return
        self.alk.kilepes_frissiteshez()


class FeluletSegedek:
    """Az ablak és a lapok közös felületi apróságai."""

    def _cimke(self, szulo, szoveg, meret=10, szin=None, vastag=False, **kw):
        return tk.Label(szulo, text=szoveg, bg=szulo["bg"],
                        fg=szin or SZIN["halvany"],
                        font=(BETU, meret, "bold" if vastag else "normal"), **kw)

    def _kartya(self, szulo):
        """Panel 1 pixeles kerettel - ettől tagolt a felület."""
        kulso = tk.Frame(szulo, bg=SZIN["keret"], padx=1, pady=1)
        belso = tk.Frame(kulso, bg=SZIN["panel"])
        belso.pack(fill="both", expand=True)
        kulso.belso = belso
        return kulso


class Lap(tk.Frame, FeluletSegedek):
    """Egy önálló letöltési munkamenet: saját URL, formátumlista, folyamat és
    napló. Több lap futhat egyszerre, egymástól függetlenül."""

    def __init__(self, alkalmazas, szulo, azonosito):
        super().__init__(szulo, bg=SZIN["hatter"])
        self.alk = alkalmazas
        self.beallitas = alkalmazas.beallitas
        self.azonosito = azonosito
        self.naplo_cimke = f"[lap {azonosito}]"

        # --- állapot ---
        self.formatumok = []
        self.mp3_lista = []
        self.info = None
        self.lekerdezett_url = None
        self.video_hossz = None
        self.utolso_mappa = None
        self.elemezve = False
        self.fut = False                  # bármilyen művelet
        self.letolt_fut = False           # kifejezetten letöltés
        self.process = None
        self._megszakitva = False
        self._lezarva = False
        self._elemzes_idozito = None
        self._elozo_url_szoveg = ""
        self._haladas_utolso = 0.0
        self._boritokep = None            # PhotoImage referencia kell, hogy megmaradjon

        # --- a lapfülön látszó állapot ---
        self.lap_cim = ""
        self.lap_allapot = "ures"         # ures/elemzes/kesz_elemzes/letoltes/kesz/hiba/megszakitva
        self.lap_szazalek = 0.0

        self._ui_felepites()
        self._beallitasok_alkalmazasa()

    # -- a lapfül tartalma --

    def jelzes(self):
        """A lapfülön látható kis állapotjelző: (jel, szín)."""
        return {
            "kesz": ("✓", "siker"),
            "hiba": ("✕", "hiba"),
            "megszakitva": ("■", "figyelem"),
            "letoltes": (f"{self.lap_szazalek:.0f}%", "kiemel"),
            "elemzes": ("◌", "figyelem"),
            "kesz_elemzes": ("●", "siker"),
        }.get(self.lap_allapot, ("●", "halvany2"))

    def ful_felirat(self):
        """A fülön látszó név.

        Cím nélkül a lap SORSZÁMÁT mutatjuk, nem a belső azonosítóját: lapok
        bezárása után a maradék átszámozódik, tehát egyetlen nyitott lap
        mindig "Új lap" marad. (A belső azonosító közben végig egyedi, mert
        arra hivatkozik a napló és az ideiglenes fájlnevek is.)"""
        cim = self.lap_cim
        if not cim:
            try:
                sorszam = self.alk.lapok.index(self) + 1
            except ValueError:
                sorszam = 1
            cim = "Új lap" if sorszam == 1 else f"Új lap {sorszam}"
        return cim if len(cim) <= 26 else cim[:25] + "…"

    def _ful_frissites(self):
        self.alk.lap_jelzes_frissites(self)

    def lezaras(self):
        """A lap bezárásakor: futó munka leállítása."""
        self._lezarva = True
        self._megszakitva = True
        self._folyamat_leallitas()

    def fokusz(self):
        try:
            self.url_mezo.entry.focus_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ felület --

    def _ui_felepites(self):
        # A lap három sorból áll: törzs, akciósáv, napló. A sor-minsize-ok
        # tartják bent a naplót és a haladásjelzőt kis ablakban is.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=4, minsize=240)   # törzs
        self.grid_rowconfigure(2, weight=1, minsize=124)   # napló

        self._torzs_epites()
        self._akciosav_epites()
        self._naplo_epites()

    def _allapot(self, szoveg, szin="halvany", allapot=None):
        """A lap állapota: a lapfülön látszik, hogy épp mi történik rajta."""
        if allapot:
            self.lap_allapot = allapot
        self._fo_szalon(self._ful_frissites)

    # -- törzs ---------------------------------------------------------------

    def _torzs_epites(self):
        self.torzs = torzs = tk.Frame(self, bg=SZIN["hatter"], padx=20, pady=10)
        torzs.grid(row=0, column=0, sticky="nsew")
        torzs.grid_columnconfigure(0, weight=1)
        torzs.grid_rowconfigure(3, weight=1)          # a minőséglista nyúlik

        self._link_sor(torzs, 0)
        self._info_kartya(torzs, 1)
        self._mod_sor(torzs, 2)
        self._minoseg_lista(torzs, 3)
        self._szakasz_sor(torzs, 4)
        self._mappa_sor(torzs, 5)

    def _link_sor(self, szulo, sor):
        keret = tk.Frame(szulo, bg=SZIN["hatter"])
        keret.grid(row=sor, column=0, sticky="ew")
        keret.grid_columnconfigure(0, weight=1)

        self._cimke(keret, "VIDEÓ LINKJE", 8, SZIN["halvany2"], True).grid(
            row=0, column=0, sticky="w", pady=(0, 5))

        bal = tk.Frame(keret, bg=SZIN["hatter"])
        bal.grid(row=1, column=0, sticky="ew")
        self.url_mezo = Mezo(bal, meret=11)
        self.url_mezo.pack(fill="x")
        self.url_mezo.entry.bind("<Return>", lambda e: self._elemzes_inditasa())
        self.url_mezo.entry.bind("<KeyRelease>", self._url_valtozott)
        self.url_mezo.entry.bind("<<Paste>>",
                                 lambda e: self.after(30, self._url_valtozott))

        gombok = tk.Frame(keret, bg=SZIN["hatter"])
        gombok.grid(row=1, column=1, sticky="e", padx=(8, 0))
        Gomb(gombok, "Beillesztés", self._vagolap_beillesztes,
             "masodlagos").pack(side="left")
        self.elemzes_gomb = Gomb(gombok, "Elemzés", self._elemzes_inditasa,
                                 "elsodleges", vastag=True)
        self.elemzes_gomb.pack(side="left", padx=(6, 0))

    def _info_kartya(self, szulo, sor):
        self.info_kartya = self._kartya(szulo)
        belso = self.info_kartya.belso
        belso.configure(padx=12, pady=10)

        self.kep_cimke = tk.Label(belso, bg=SZIN["panel2"], width=17, height=4,
                                  text="🎬", fg=SZIN["halvany2"], font=(BETU, 16))
        self.kep_cimke.pack(side="left", padx=(0, 14))

        szoveges = tk.Frame(belso, bg=SZIN["panel"])
        szoveges.pack(side="left", fill="both", expand=True)
        self.cim_cimke = self._cimke(szoveges, "", 11, SZIN["szoveg"], True,
                                     anchor="w", justify="left", wraplength=700)
        self.cim_cimke.pack(fill="x")
        self.alcim_cimke = self._cimke(szoveges, "", 9, SZIN["halvany"],
                                       anchor="w", justify="left")
        self.alcim_cimke.pack(fill="x", pady=(3, 0))
        self.jelveny_sor = tk.Frame(szoveges, bg=SZIN["panel"])
        self.jelveny_sor.pack(fill="x", pady=(6, 0))

        self._info_sor = sor          # a grid csak elemzés után jelenik meg

    def _jelveny(self, szoveg, szin):
        keret = tk.Frame(self.jelveny_sor, bg=SZIN["panel2"], padx=8, pady=3)
        keret.pack(side="left", padx=(0, 6))
        tk.Label(keret, text=szoveg, bg=SZIN["panel2"], fg=szin,
                 font=(BETU, 8, "bold")).pack()

    def _mod_sor(self, szulo, sor):
        keret = tk.Frame(szulo, bg=SZIN["hatter"])
        keret.grid(row=sor, column=0, sticky="ew", pady=(10, 0))

        fej = tk.Frame(keret, bg=SZIN["hatter"])
        fej.pack(fill="x", pady=(0, 5))
        self._cimke(fej, "MIT TÖLTSÜNK LE?", 8, SZIN["halvany2"], True).pack(side="left")
        self.konteneres_cimke = self._cimke(fej, "KONTÉNER", 8, SZIN["halvany2"], True)
        self.konteneres_cimke.pack(side="right")

        also = tk.Frame(keret, bg=SZIN["hatter"])
        also.pack(fill="x")

        valaszto = tk.Frame(also, bg=SZIN["keret"], padx=1, pady=1)
        valaszto.pack(side="left")
        belso = tk.Frame(valaszto, bg=SZIN["panel"])
        belso.pack()

        self.mod = "video"
        self.mod_gombok = {}
        for ertek, szoveg in (("zene", "🎵  Zene (MP3)"),
                              ("video", "🎬  Videó"),
                              ("vr", "🥽  VR 3D (SBS)")):
            g = tk.Button(belso, text=szoveg, font=(BETU, 10),
                          relief="flat", bd=0, highlightthickness=0,
                          cursor="hand2", padx=18, pady=8,
                          command=lambda e=ertek: self._mod_valtas(e))
            g.pack(side="left")
            self.mod_gombok[ertek] = g

        # Konténerválasztó. FONTOS: egyik változat sem kódol újra semmit, a
        # videósáv bitre azonos - csak a "doboz" más. Alapból a forrás szerinti
        # megy (VP9/AV1 webm -> MKV), mert az a sáv natív otthona; MP4-et azért
        # lehet kérni, mert ahhoz ad az Explorer előnézetet, és régebbi
        # lejátszók/TV-k is jobban szeretik.
        self.konteneres = "auto"
        # Az "auto" felirata elemzés után kiegészül a tényleges gyári
        # konténerrel ("Gyári formátum – MKV"), hogy ne kelljen kitalálni,
        # mit jelent éppen.
        self.konteneres_nevek = {
            "auto": "Gyári formátum",
            "mkv": "MKV",
            "mp4": "MP4",
        }
        self.konteneres_valtozo = tk.StringVar(
            value=self.konteneres_nevek[self.konteneres])
        self.konteneres_lista = ttk.Combobox(
            also, textvariable=self.konteneres_valtozo, state="readonly",
            values=list(self.konteneres_nevek.values()), width=24,
            font=(BETU, 9), style="Sotet.TCombobox")
        self.konteneres_lista.pack(side="right", ipady=4)
        self.konteneres_lista.bind("<<ComboboxSelected>>", self._konteneres_valtas)

    def _konteneres_valtas(self, _=None):
        valasztott = self.konteneres_valtozo.get()
        for kulcs, nev in self.konteneres_nevek.items():
            if nev == valasztott:
                self.konteneres = kulcs
                break
        self._beallitasok_mentese()
        self._minoseg_osszefoglalo()

    def _gyari_konteneres(self, valasztott):
        """A kiválasztott sáv gyári konténere - amit a YouTube tényleg ad.

        A VP9 (és a legtöbb 4K/8K) sáv webm-ként érkezik, aminek az MKV a
        természetes doboza; az AV1/H.264 sávok mp4-ként."""
        return "mkv" if (valasztott or {}).get("ext") == "webm" else "mp4"

    def _konteneres_feloldas(self, valasztott):
        """A ténylegesen használt konténer: a kézi választás, vagy a gyári.

        Egyik ág sem kódol újra semmit - a sávok -c copy-val kerülnek át,
        csak a konténer más."""
        if self.konteneres != "auto":
            return self.konteneres
        return self._gyari_konteneres(valasztott)

    def _konteneres_felirat_frissites(self):
        """A "Gyári formátum" sorba beírjuk, hogy épp mit jelent."""
        if self.elemezve and self.mod != "zene":
            gyari = self._gyari_konteneres(self._valasztott_formatum())
            self.konteneres_nevek["auto"] = f"Gyári formátum – {gyari.upper()}"
        else:
            self.konteneres_nevek["auto"] = "Gyári formátum"
        self.konteneres_lista.config(values=list(self.konteneres_nevek.values()))
        if self.konteneres == "auto":
            # A .set() nem vált ki <<ComboboxSelected>>-et, tehát nem hurkol.
            self.konteneres_valtozo.set(self.konteneres_nevek["auto"])

    def _mod_valtas(self, uj):
        self.mod = uj
        el = getattr(self, "elemezve", False)
        for ertek, gomb in self.mod_gombok.items():
            aktiv = ertek == uj
            if aktiv:
                hatter = SZIN["kiemel"] if el else SZIN["keret2"]
                elotr = "#ffffff" if el else SZIN["halvany2"]
            else:
                hatter = SZIN["panel"]
                elotr = SZIN["halvany"] if el else SZIN["halvany2"]
            gomb.config(bg=hatter, fg=elotr,
                        activebackground=SZIN["kiemel2"] if aktiv else SZIN["panel2"],
                        activeforeground="#ffffff" if aktiv else SZIN["szoveg"],
                        disabledforeground=elotr,
                        font=(BETU, 10, "bold" if aktiv else "normal"))
        # Az MP3 kimenet konténere adott, ott nincs mit választani.
        if uj == "zene":
            self.konteneres_lista.pack_forget()
            self.konteneres_cimke.pack_forget()
        else:
            self.konteneres_cimke.pack(side="right")
            self.konteneres_lista.pack(side="right", ipady=4)
        self._minoseg_lathatosag()
        # A táblázat tartalma módfüggő (felbontások vagy MP3 bitráták), ezért
        # módváltáskor újra kell tölteni - és CSAK utána szabad az összefoglalót
        # frissíteni, különben a régi mód kijelölésével számolna (a konténer
        # ilyenkor mp4-et mutatna webm forrásra is).
        if getattr(self, "elemezve", False):
            self._lista_feltoltes()
        self._minoseg_osszefoglalo()

    def _szakasz_sor(self, szulo, sor):
        """Részlet-kijelölő: kapcsoló + csúszka + két időmező (kétirányban
        szinkronban)."""
        kartya = self._kartya(szulo)
        kartya.grid(row=sor, column=0, sticky="ew", pady=(10, 0))
        belso = kartya.belso
        belso.configure(padx=12, pady=10)
        self.szakasz_kartya = kartya

        fej = tk.Frame(belso, bg=SZIN["panel"])
        fej.pack(fill="x")
        self.szakasz_valtozo = tk.BooleanVar(value=False)
        self.szakasz_kapcsolo = tk.Checkbutton(
            fej, text="  Csak egy részlet letöltése", variable=self.szakasz_valtozo,
            command=self._szakasz_valtozott, bg=SZIN["panel"],
            activebackground=SZIN["panel"], fg=SZIN["szoveg"],
            activeforeground=SZIN["szoveg"], selectcolor=SZIN["panel2"], bd=0,
            highlightthickness=0, cursor="hand2", font=(BETU, 10, "bold"))
        self.szakasz_kapcsolo.pack(side="left")

        mezok = tk.Frame(fej, bg=SZIN["panel"])
        mezok.pack(side="right")
        self.szakasz_kezd = Mezo(mezok, meret=10, mono=True, width=8, justify="center")
        self.szakasz_kezd.pack(side="left")
        self._cimke(mezok, "→", 11, SZIN["halvany2"]).pack(side="left", padx=6)
        self.szakasz_veg = Mezo(mezok, meret=10, mono=True, width=8, justify="center")
        self.szakasz_veg.pack(side="left")
        for mezo in (self.szakasz_kezd, self.szakasz_veg):
            mezo.allapot("disabled")
            mezo.entry.bind("<KeyRelease>", self._szakasz_mezo_valtozott)
            mezo.entry.bind("<FocusOut>", self._szakasz_mezo_valtozott)

        self.szakasz_sav = TartomanySav(belso, valtozas=self._szakasz_csuszka)
        self.szakasz_sav.pack(fill="x", pady=(6, 0))
        self._szakasz_frissul = False

    def _mappa_sor(self, szulo, sor):
        keret = tk.Frame(szulo, bg=SZIN["hatter"])
        keret.grid(row=sor, column=0, sticky="ew", pady=(10, 0))
        keret.grid_columnconfigure(1, weight=1)

        fej = tk.Frame(keret, bg=SZIN["hatter"])
        fej.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        self._cimke(fej, "MENTÉS IDE", 8, SZIN["halvany2"], True).pack(side="left")
        self.playlist_valtozo = tk.BooleanVar(value=False)
        self.playlist_kapcsolo = tk.Checkbutton(
            fej, text="  Teljes lejátszási lista letöltése",
            variable=self.playlist_valtozo,
            bg=SZIN["hatter"], activebackground=SZIN["hatter"], fg=SZIN["halvany"],
            activeforeground=SZIN["szoveg"], selectcolor=SZIN["panel2"], bd=0,
            highlightthickness=0, cursor="hand2", font=(BETU, 9))
        self.playlist_kapcsolo.pack(side="right")

        self.mappa_valtozo = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Downloads", "YTLetolto"))
        self.mappa_mezo = Mezo(keret, self.mappa_valtozo, meret=9)
        self.mappa_mezo.grid(row=1, column=0, columnspan=2, sticky="ew")
        Gomb(keret, "Tallózás", self._mappa_valasztas, "masodlagos", meret=9).grid(
            row=1, column=2, padx=(6, 0))

    # -- részlet: csúszka és mezők kétirányú szinkronja --

    def _szakasz_csuszka(self, kezd, veg):
        if self._szakasz_frissul:
            return
        self._szakasz_frissul = True
        try:
            self.szakasz_kezd.set(ido_formazas(kezd))
            self.szakasz_veg.set(ido_formazas(veg))
        finally:
            self._szakasz_frissul = False
        # Húzás közben élőben követi a méret a kijelölt hosszt.
        self._meret_oszlop_frissites()

    def _szakasz_mezo_valtozott(self, _=None):
        if self._szakasz_frissul or not self.szakasz_valtozo.get():
            return
        kezd = ido_ertelmezes(self.szakasz_kezd.get())
        veg = ido_ertelmezes(self.szakasz_veg.get())
        if kezd is None or veg is None or not self.video_hossz:
            return
        self._szakasz_frissul = True
        try:
            self.szakasz_sav.beallit(self.video_hossz, kezd, veg, ertesit=False)
        finally:
            self._szakasz_frissul = False
        self._meret_oszlop_frissites()

    def _minoseg_lista(self, szulo, sor):
        self.minoseg_keret = tk.Frame(szulo, bg=SZIN["hatter"])
        self.minoseg_keret.grid(row=sor, column=0, sticky="nsew", pady=(10, 0))
        self.minoseg_keret.grid_columnconfigure(0, weight=1)
        self.minoseg_keret.grid_rowconfigure(1, weight=1)

        fej = tk.Frame(self.minoseg_keret, bg=SZIN["hatter"])
        fej.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self._cimke(fej, "MINŐSÉG", 8, SZIN["halvany2"], True).pack(side="left")
        self.minoseg_info = self._cimke(fej, "", 9, SZIN["halvany"])
        self.minoseg_info.pack(side="right")

        doboz = tk.Frame(self.minoseg_keret, bg=SZIN["keret"], padx=1, pady=1)
        doboz.grid(row=1, column=0, sticky="nsew")
        doboz.grid_columnconfigure(0, weight=1)
        doboz.grid_rowconfigure(0, weight=1)

        oszlopok = ("nev", "felbontas", "fps", "kodek", "meret")
        self.fa = ttk.Treeview(doboz, columns=oszlopok, show="headings",
                               selectmode="browse", height=5)
        fejlecek = (("nev", "Minőség", 190, "w"), ("felbontas", "Felbontás", 110, "center"),
                    ("fps", "FPS", 60, "center"), ("kodek", "Kodek", 90, "center"),
                    ("meret", "Méret", 90, "e"))
        for azon, cim, szel, igazit in fejlecek:
            self.fa.heading(azon, text=cim, anchor="w" if igazit == "w" else "center")
            self.fa.column(azon, width=szel, anchor=igazit,
                           stretch=(azon == "nev"))
        self.fa.grid(row=0, column=0, sticky="nsew")
        self.fa.tag_configure("ajanlott", foreground=SZIN["siker"])
        self.fa.bind("<Double-1>", lambda e: self._letoltes_inditasa())
        self.fa.bind("<Configure>", lambda e: self._fa_szelesseg_igazitas())
        # Más felbontás más forráskonténert jelenthet (webm vs mp4), amitől az
        # "Automatikus" beállítás eredménye is változik.
        self.fa.bind("<<TreeviewSelect>>", lambda e: self._minoseg_osszefoglalo())

        gorgeto = ttk.Scrollbar(doboz, orient="vertical", command=self.fa.yview)
        gorgeto.grid(row=0, column=1, sticky="ns")
        self.fa.configure(yscrollcommand=gorgeto.set)

        self.minoseg_ures = tk.Label(
            doboz, bg=SZIN["mezo"], fg=SZIN["halvany2"], font=(BETU, 10),
            justify="center",
            text="Illeszd be a videó linkjét,\n"
                 "és itt megjelenik minden elérhető minőség.")
        self.minoseg_ures.place(relx=0.5, rely=0.5, anchor="center")

    def _minoseg_lathatosag(self):
        # Mindhárom módban van mit választani, csak mást: felbontást vagy
        # MP3 bitrátát. A minsize garantálja, hogy a lista sose lapuljon a
        # fejlécsorra akkor sem, ha az ablak szűk.
        self.minoseg_keret.grid()
        # A minsize nem lehet túl nagy: ha a törzs nem fér ki, a grid a
        # súlyozott sort húzza össze - ha az nem enged, az ALATTA lévő sorok
        # (mentés helye) csúsznak ki az ablakból.
        self.torzs.grid_rowconfigure(3, weight=1, minsize=150)

    def _minoseg_osszefoglalo(self):
        self._konteneres_felirat_frissites()
        if self.mod == "zene":
            self._allapot_sugo("MP3 · a forrás hangsáv mindig a legjobb elérhető")
            return
        # A konténer sosem jelent újrakódolást, ezt érdemes kiírni: enélkül
        # könnyű azt hinni, hogy az mp4 "átkonvertálás" és minőségvesztés.
        valasztott = self._valasztott_formatum()
        elozetes = self._konteneres_feloldas(valasztott)
        if elozetes == self._gyari_konteneres(valasztott):
            szoveg = f"{elozetes.upper()} · gyári formátum, érintetlen sávok"
        else:
            szoveg = f"{elozetes.upper()} · újracsomagolás, nem újrakódolás"
        if self.mod == "vr":
            szoveg += " · _3D_SBS jelölés"
        self._allapot_sugo(szoveg)

    def _allapot_sugo(self, szoveg):
        self.sugo_cimke.config(text=szoveg)

    # -- akciósáv ------------------------------------------------------------

    def _akciosav_epites(self):
        keret = tk.Frame(self, bg=SZIN["panel"], padx=20, pady=12)
        keret.grid(row=1, column=0, sticky="ew")
        keret.grid_columnconfigure(1, weight=1)
        tk.Frame(self, bg=SZIN["keret"], height=1).grid(row=1, column=0, sticky="new")

        self.letoltes_gomb = Gomb(keret, "↓   Letöltés", self._letoltes_inditasa,
                                  "elsodleges", meret=12, vastag=True,
                                  padx=26, pady=11)
        self.letoltes_gomb.grid(row=0, column=0, rowspan=2, sticky="w")

        jobb = tk.Frame(keret, bg=SZIN["panel"])
        jobb.grid(row=0, column=1, rowspan=2, sticky="ew", padx=(18, 0))
        jobb.grid_columnconfigure(0, weight=1)

        felso = tk.Frame(jobb, bg=SZIN["panel"])
        felso.grid(row=0, column=0, sticky="ew")
        self.haladas_cimke = self._cimke(felso, "Készen áll.", 10, SZIN["halvany"])
        self.haladas_cimke.pack(side="left")
        self.sugo_cimke = self._cimke(felso, "", 9, SZIN["halvany2"])
        self.sugo_cimke.pack(side="right")

        self.haladas = ttk.Progressbar(jobb, style="Fo.Horizontal.TProgressbar",
                                       mode="determinate", maximum=100)
        self.haladas.grid(row=1, column=0, sticky="ew", pady=(7, 0))

        self.mappa_gomb = Gomb(keret, "📂  Mappa", self._mappa_megnyitas,
                               "masodlagos", meret=10)
        self.mappa_gomb.grid(row=0, column=2, rowspan=2, sticky="e", padx=(14, 0))

    # -- napló ---------------------------------------------------------------

    def _naplo_epites(self):
        # A tk.Frame padx/pady-ja egyetlen távolság (a pack/grid-é lehet tuple),
        # ezért az alsó margót a grid adja.
        self.naplo_keret = tk.Frame(self, bg=SZIN["hatter"], padx=20, pady=10)
        self.naplo_keret.grid(row=2, column=0, sticky="nsew", pady=(0, 4))
        self.naplo_keret.grid_columnconfigure(0, weight=1)
        self.naplo_keret.grid_rowconfigure(1, weight=1)

        fej = tk.Frame(self.naplo_keret, bg=SZIN["hatter"])
        fej.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self._cimke(fej, "NAPLÓ", 8, SZIN["halvany2"], True).pack(side="left")
        Gomb(fej, "Naplófájl", self._naplo_megnyitas, "csendes", meret=8,
             padx=8, pady=3).pack(side="right")
        Gomb(fej, "Törlés", self._naplo_torles, "csendes", meret=8,
             padx=8, pady=3).pack(side="right", padx=(0, 6))

        doboz = tk.Frame(self.naplo_keret, bg=SZIN["keret"], padx=1, pady=1)
        doboz.grid(row=1, column=0, sticky="nsew")
        doboz.grid_columnconfigure(0, weight=1)
        doboz.grid_rowconfigure(0, weight=1)

        self.naplo = tk.Text(doboz, font=("Consolas", 9), bg=SZIN["mezo"],
                             fg=SZIN["halvany"], relief="flat", bd=0,
                             padx=10, pady=8, state="disabled", wrap="word",
                             height=6, insertbackground=SZIN["kiemel"],
                             selectbackground=SZIN["keret2"])
        self.naplo.grid(row=0, column=0, sticky="nsew")
        gorgeto = ttk.Scrollbar(doboz, orient="vertical", command=self.naplo.yview)
        gorgeto.grid(row=0, column=1, sticky="ns")
        self.naplo.configure(yscrollcommand=gorgeto.set)

        for tag, szin in (("siker", SZIN["siker"]), ("hiba", SZIN["hiba"]),
                          ("figyelem", SZIN["figyelem"]), ("info", SZIN["kiemel"]),
                          ("halvany", SZIN["halvany2"])):
            self.naplo.tag_configure(tag, foreground=szin)

    def _beallitasok_alkalmazasa(self):
        b = self.beallitas
        if b.get("mappa"):
            self.mappa_valtozo.set(b["mappa"])
        self.mod = b.get("mod") if b.get("mod") in ("zene", "video", "vr") else "video"
        if b.get("konteneres") in self.konteneres_nevek:
            self.konteneres = b["konteneres"]
            self.konteneres_valtozo.set(self.konteneres_nevek[self.konteneres])
        self._felulet_allapot(False)
        self.url_mezo.entry.focus_set()

    def _beallitasok_mentese(self):
        # Az ablakon keresztül megy, hogy a többi beállítás (pl. a frissítési
        # értesítés kapcsolója) ne vesszen el a mentéskor.
        self.alk.beallitasok_mentese(mappa=self.mappa_valtozo.get(),
                                     mod=self.mod, konteneres=self.konteneres)

    # ------------------------------------------------------- alap segédek --

    def _leallt(self):
        """A lapot bezárták, vagy az egész ablak megszűnt."""
        return self._lezarva or self.alk._destroyed

    def _fo_szalon(self, fv, *argumentumok):
        """Tkinter nem szálbiztos: minden widget-művelet a fő szálon fut.

        Az ellenőrzés és a hívás között is leállhat az értelmező, ezért a
        RuntimeError-t is el kell kapni."""
        if self._leallt():
            return
        try:
            self.after(0, lambda: None if self._leallt() else fv(*argumentumok))
        except RuntimeError:
            pass

    def _fajlnaplo(self, szoveg):
        """Csak a naplófájlba, a lap azonosítójával megjelölve."""
        fajlba_naplo(szoveg, self.naplo_cimke)

    def _log(self, szoveg, csak_fajlba=False):
        """A napló mindig fájlba is megy - a hibakereséshez ez a forrás.

        A lapazonosító azért kerül a fájlba, mert több lap egyszerre is
        tölthet, és a sorok különben összekeverednének."""
        self._fajlnaplo(szoveg.strip("\n") if szoveg.strip() else szoveg)
        if not csak_fajlba:
            self.naplo_kiiras(szoveg)

    def naplo_kiiras(self, szoveg):
        """Csak a lap napló-ablakába ír; a fájlba a hívó írt már.

        Az ablak ezen keresztül tudja minden nyitott lapra kiírni a közös
        üzeneteket (pl. az eszközök letöltését)."""
        def frissit():
            tag = ""
            if szoveg.startswith(("✅", "🎉")):
                tag = "siker"
            elif szoveg.startswith(("❌", "⛔")):
                tag = "hiba"
            elif szoveg.startswith("⚠️"):
                tag = "figyelem"
            elif szoveg.startswith(("📹", "🎬", "🎵", "🥽", "✂️", "📂")):
                tag = "info"
            elif szoveg.startswith("   ") or szoveg.startswith("["):
                tag = "halvany"
            self.naplo.config(state="normal")
            self.naplo.insert("end", szoveg + "\n", tag)
            sorok = int(self.naplo.index("end-1c").split(".")[0])
            if sorok > 500:
                self.naplo.delete("1.0", f"{sorok - 500}.0")
            self.naplo.see("end")
            self.naplo.config(state="disabled")
        self._fo_szalon(frissit)

    def _naplo_torles(self):
        self.naplo.config(state="normal")
        self.naplo.delete("1.0", "end")
        self.naplo.config(state="disabled")

    def _haladas_beallit(self, szazalek=None, szoveg=None, stilus=None):
        """Haladásjelző frissítése (a hívó lehet háttérszál)."""
        def frissit():
            if stilus:
                self.haladas.config(style=stilus)
            if szazalek is None:
                if str(self.haladas["mode"]) != "indeterminate":
                    self.haladas.config(mode="indeterminate")
                    self.haladas.start(14)
            else:
                if str(self.haladas["mode"]) == "indeterminate":
                    self.haladas.stop()
                    self.haladas.config(mode="determinate")
                self.haladas["value"] = max(0, min(100, szazalek))
            if szoveg is not None:
                self.haladas_cimke.config(text=szoveg)
            # A lapfül is mutassa a százalékot, hogy másik lapról is látszódjon.
            if szazalek is not None and self.letolt_fut:
                self.lap_szazalek = max(0.0, min(100.0, szazalek))
            self._ful_frissites()
        self._fo_szalon(frissit)

    def _haladas_fojtott(self, szazalek, szoveg):
        """Másodpercenként legfeljebb ~8 frissítés, hogy ne fulladjon a felület."""
        most = time.monotonic()
        if most - self._haladas_utolso < 0.12:
            return
        self._haladas_utolso = most
        self._haladas_beallit(szazalek, szoveg)

    # ------------------------------------------------------------ eszközök --

    def _eszkozok_keszen(self):
        """A letöltéshez a yt-dlp és az ffmpeg is kell (merge, mp3, szakasz)."""
        if not os.path.isfile(YTDLP_EXE):
            self._log("⚠️ A yt-dlp még letöltődik, várj pár másodpercet!")
            return False
        if not os.path.isfile(FFMPEG_EXE):
            self._log("⚠️ Az ffmpeg még letöltődik, várj pár másodpercet!")
            return False
        return True

    # -------------------------------------------------------- felhasználói --

    def _vagolap_beillesztes(self):
        try:
            szoveg = self.clipboard_get()
        except tk.TclError:
            self._log("⚠️ A vágólap üres.")
            return
        self.url_mezo.set(url_rendbetetel(szoveg))
        self._elemzes_inditasa()

    def _url_valtozott(self, _=None):
        """Gépelés/beillesztés után rövid szünettel magától elemez.

        Így nem lehet elfelejteni az Elemzés gombot - ez volt a felület
        legnagyobb csapdája."""
        szoveg = self.url_mezo.get().strip()
        if szoveg == self._elozo_url_szoveg:
            return
        self._elozo_url_szoveg = szoveg
        if self._elemzes_idozito:
            try:
                self.after_cancel(self._elemzes_idozito)
            except (ValueError, tk.TclError):
                pass
            self._elemzes_idozito = None
        url = url_rendbetetel(szoveg)
        if not url.startswith("http") or len(url) < 15:
            return
        if url == self.lekerdezett_url or self.fut:
            return
        self._elemzes_idozito = self.after(900, self._elemzes_inditasa)

    def _mappa_valasztas(self):
        mappa = filedialog.askdirectory(initialdir=self.mappa_valtozo.get())
        if mappa:
            self.mappa_valtozo.set(os.path.normpath(mappa))
            self._beallitasok_mentese()

    def _mappa_megnyitas(self):
        mappa = self.utolso_mappa or self.mappa_valtozo.get()
        try:
            os.makedirs(mappa, exist_ok=True)
            os.startfile(mappa)
        except OSError as e:
            messagebox.showerror("Mappa", f"Nem sikerült megnyitni:\n{mappa}\n\n{e}")

    def _naplo_megnyitas(self):
        if not os.path.isfile(NAPLO_FAJL):
            messagebox.showinfo("Napló", f"A naplófájl még nem jött létre.\n\n{NAPLO_FAJL}")
            return
        try:
            os.startfile(NAPLO_FAJL)
        except OSError as e:
            messagebox.showerror("Napló", f"Nem sikerült megnyitni:\n{NAPLO_FAJL}\n\n{e}")

    def _szakasz_valtozott(self):
        be = self.szakasz_valtozo.get() and self.elemezve
        self.szakasz_kezd.allapot("normal" if be else "disabled")
        self.szakasz_veg.allapot("normal" if be else "disabled")

        hossz = self.video_hossz or 0 if self.elemezve else 0
        kezd = ido_ertelmezes(self.szakasz_kezd.get())
        veg = ido_ertelmezes(self.szakasz_veg.get())
        if kezd is None or veg is None or not hossz or veg > hossz or kezd >= veg:
            # Alapból a videó egy negyedét ajánljuk fel - így rögtön látszik,
            # mit csinál a csúszka.
            kezd, veg = int(hossz * 0.25), int(hossz * 0.5)
        # A sáv a bekapcsolt jelölőnégyzet nélkül is mutatja a videó
        # idővonalát (szürkén), csak nem lehet húzni.
        self.szakasz_sav.beallit(hossz, kezd, veg, ertesit=be)
        self.szakasz_sav.engedelyez(be)
        # Ki-/bekapcsoláskor a méret oszlop is visszavált a teljes méretre.
        self._meret_oszlop_frissites()

    def _felulet_allapot(self, elemezve):
        """Elemzés előtt minden vezérlő szürke: nincs mit beállítani rajtuk."""
        self.elemezve = elemezve
        allapot = "normal" if elemezve else "disabled"
        for gomb in self.mod_gombok.values():
            gomb.config(state=allapot)
        self._mod_valtas(self.mod)              # színek újrafestése
        self.fa.state(("!disabled",) if elemezve else ("disabled",))
        self.szakasz_kapcsolo.config(
            state=allapot, fg=SZIN["szoveg"] if elemezve else SZIN["halvany2"])
        self.playlist_kapcsolo.config(
            state=allapot, fg=SZIN["halvany"] if elemezve else SZIN["halvany2"])
        self.letoltes_gomb.config(state=allapot)
        self.konteneres_lista.config(state="readonly" if elemezve else "disabled")
        self._szakasz_valtozott()

    # --------------------------------------------------------- elemzés -----

    def _elemzes_inditasa(self):
        if self._elemzes_idozito:
            try:
                self.after_cancel(self._elemzes_idozito)
            except (ValueError, tk.TclError):
                pass
            self._elemzes_idozito = None

        url = url_rendbetetel(self.url_mezo.get())
        if not url:
            messagebox.showwarning("Hiányzó link", "Illeszd be a videó linkjét!")
            return
        self.url_mezo.set(url)
        self._elozo_url_szoveg = url
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Hibás link",
                                   "Ez nem érvényes link.\n"
                                   "Példa: https://www.youtube.com/watch?v=XXXXXXXX")
            return
        if not os.path.isfile(YTDLP_EXE):
            self._log("⚠️ Az eszközök még letöltődnek, várj!")
            return
        if self.fut:
            self._log("⚠️ Már fut egy művelet, várj!")
            return

        self.fut = True
        self.elemzes_gomb.config(state="disabled", text="Elemzés…")
        self._allapot("elemzés…", "kiemel", "elemzes")
        self._haladas_beallit(None, "Videó adatainak lekérdezése…")
        self._formatumok_torlese()
        threading.Thread(target=self._elemzes_worker, args=(url,), daemon=True).start()

    def _formatumok_torlese(self):
        self.formatumok = []
        self.mp3_lista = []
        self.info = None
        self.video_hossz = None
        self.lekerdezett_url = None
        for elem in self.fa.get_children():
            self.fa.delete(elem)
        self.minoseg_info.config(text="")
        self._felulet_allapot(False)

    def _elemzes_worker(self, url):
        try:
            self._log(f"\n🔍 Elemzés: {url}")
            info, hiba = formatumok_lekerese(url, lap=self.naplo_cimke)
            if hiba or not info:
                self._log(f"❌ {hiba}")
                self._allapot("sikertelen elemzés", "hiba", "hiba")
                self._haladas_beallit(0, "Az elemzés nem sikerült.")
                return

            self.info = info
            hossz = info.get("duration")
            self.video_hossz = int(hossz) if hossz else None
            self.lekerdezett_url = url
            self.formatumok = formatum_csoportositas(info)
            self.mp3_lista = mp3_valasztekok(info)

            cim = info.get("title") or "?"
            self.lap_cim = cim                 # ez látszik majd a lapfülön
            self._log(f"📹 {cim}"
                      + (f"  |  {ido_formazas(hossz)}" if hossz else ""))

            if self.formatumok:
                legjobb = self.formatumok[-1]
                self._log(f"🎬 Legjobb: {legjobb['leiras']}")
                self._log(f"🎵 Hang: {legjobb['hang_leiras']}")
                self._log(f"✅ {len(self.formatumok)} minőség elérhető.")
            else:
                self._log("⚠️ Nem találtam letölthető videósávot "
                          "(lehet, hogy ez csak hang).")

            self._fo_szalon(self._elemzes_megjelenites)
            self._allapot("kész az elemzés", "siker", "kesz_elemzes")
            threading.Thread(target=self._boritokep_elonezet,
                             args=(info,), daemon=True).start()
        except Exception as e:
            self._log(f"❌ Váratlan hiba az elemzés közben: {e}")
            fajlba_naplo(traceback.format_exc(), self.naplo_cimke)
            self._allapot("hiba", "hiba", "hiba")
        finally:
            self.fut = False
            self._fo_szalon(lambda: self.elemzes_gomb.config(
                state="normal", text="Elemzés"))

    def _elemzes_megjelenites(self):
        info = self.info or {}
        self.cim_cimke.config(text=info.get("title") or "–")

        reszek = []
        if info.get("uploader"):
            reszek.append(info["uploader"])
        if self.video_hossz:
            reszek.append(ido_formazas(self.video_hossz))
        if info.get("view_count"):
            reszek.append(f"{info['view_count']:,}".replace(",", " ") + " megtekintés")
        self.alcim_cimke.config(text="  ·  ".join(reszek))

        for w in self.jelveny_sor.winfo_children():
            w.destroy()
        if self.formatumok:
            legjobb = self.formatumok[-1]
            self._jelveny(legjobb["nev"].split(" · ")[0].upper(), SZIN["siker"])
            self._jelveny(legjobb["felbontas"], SZIN["halvany"])
            self._jelveny(legjobb["kodek"], SZIN["halvany"])
            if legjobb["hdr"]:
                self._jelveny("HDR", SZIN["lila"])

        self.info_kartya.grid(row=self._info_sor, column=0, sticky="ew", pady=(10, 0))

        self._lista_feltoltes()
        self._felulet_allapot(True)
        self._haladas_beallit(0, "Készen áll a letöltésre.")

    # -- minőséglista (módtól függő tartalommal) --

    def _aktualis_lista(self):
        """A most érvényes választéklista: videónál a felbontások, zenénél az
        MP3 bitráták."""
        # A VR ugyanazokból a felbontásokból választ, mint a videó mód - csak a
        # fájlnév kap _3D_SBS jelölést.
        return self.mp3_lista if self.mod == "zene" else self.formatumok

    def _lista_feltoltes(self):
        """A táblázat feltöltése - a legjobb legfelül, eleve kijelölve."""
        for elem in self.fa.get_children():
            self.fa.delete(elem)
        lista = self._aktualis_lista()

        if self.mod == "zene":
            # Ugyanaz a táblázat, más jelentésű oszlopokkal (az FPS itt semmit
            # nem mondana, ezért elrejtjük).
            self.fa.configure(displaycolumns=("nev", "felbontas", "kodek", "meret"))
            fejlecek = (("nev", "MP3 minőség", 190), ("felbontas", "Típus", 80),
                        ("kodek", "Forrás", 140), ("meret", "Becsült méret", 110))
            sorok = [(f["nev"], f["tipus"], "", f["forras"], f["meret_szoveg"])
                     for f in lista]
        else:
            self.fa.configure(displaycolumns="#all")
            fejlecek = (("nev", "Minőség", 190), ("felbontas", "Felbontás", 110),
                        ("fps", "FPS", 60), ("kodek", "Kodek", 90),
                        # a "Részlet mérete" fejléc hosszabb, mint a "Méret"
                        ("meret", "Méret", 115))
            sorok = [(f["nev"], f["felbontas"], f["fps"], f["kodek"],
                      f["meret_szoveg"]) for f in lista]
        for azon, cim, szelesseg in fejlecek:
            self.fa.heading(azon, text=cim)
            # A stretch-et minden feltöltésnél újra ki kell mondani: a lap
            # elrejtése/visszahozása után különben nem tölti ki a szélességet.
            self.fa.column(azon, width=szelesseg, stretch=(azon == "nev"))

        for i, ertekek in enumerate(reversed(sorok)):
            elem = self.fa.insert("", "end", iid=str(len(sorok) - 1 - i),
                                  values=ertekek,
                                  tags=("ajanlott",) if i == 0 else ())
            if i == 0:
                self.fa.selection_set(elem)
                self.fa.focus(elem)
                self.fa.see(elem)

        if sorok:
            self.minoseg_ures.place_forget()
            if self.mod == "zene":
                self.minoseg_info.config(
                    text=f"forrás: {lista[-1]['forras']} · efölött a nagyobb "
                         f"bitráta már nem ad többletet")
            else:
                self.minoseg_info.config(
                    text=f"{len(lista)} minőség · hang: {lista[-1]['hang_leiras']}")
        else:
            self.minoseg_info.config(text="")
            self.minoseg_ures.config(text="Ehhez a linkhez nem találtam\n"
                                          "letölthető formátumot.")
            self.minoseg_ures.place(relx=0.5, rely=0.5, anchor="center")
        self._meret_oszlop_frissites()
        self.after_idle(self._fa_szelesseg_igazitas)

    def _fa_szelesseg_igazitas(self):
        """A névoszlop töltse ki a maradék helyet.

        A ttk.Treeview csak <Configure> eseményre osztja újra a szélességet, a
        lap elrejtése és visszahozása viszont nem vált ki ilyet - ezért a
        stretch magától nem elég, ki kell számolni."""
        try:
            teljes = self.fa.winfo_width()
            if teljes <= 10:
                return
            oszlopok = self.fa.cget("displaycolumns")
            if not oszlopok or oszlopok[0] == "#all":
                oszlopok = self.fa.cget("columns")
            tobbi = sum(int(self.fa.column(o, "width"))
                        for o in oszlopok if o != "nev")
            kell = max(150, teljes - tobbi - 4)
            # Csak érdemi eltérésnél írunk, különben a Configure önmagát hívná.
            if abs(int(self.fa.column("nev", "width")) - kell) > 2:
                self.fa.column("nev", width=kell)
        except (tk.TclError, ValueError):
            pass

    def _szakasz_arany(self):
        """A kijelölt részlet hossza a teljes videóhoz képest (0-1)."""
        if not self.szakasz_valtozo.get() or not self.elemezve:
            return 1.0
        hossz = self.video_hossz or 0
        kezd, veg = self.szakasz_sav.kezd, self.szakasz_sav.veg
        if not hossz or veg <= kezd:
            return 1.0
        return min(1.0, (veg - kezd) / hossz)

    def _meret_oszlop_frissites(self):
        """A méret oszlop mindig a TÉNYLEG letöltendő adagot mutassa.

        Egy 24 órás videónál a 421 GB-os teljes méret semmit nem mond arról,
        hogy a kért 5 perc mekkora lesz - ezért a csúszka húzása közben élőben
        újraszámoljuk. (Becslés: a bitráta nem egyenletes a videó hosszában.)"""
        arany = self._szakasz_arany()
        reszlet = arany < 0.999
        self.fa.heading("meret", text="Részlet mérete" if reszlet else "Méret")
        for i, fmt in enumerate(self._aktualis_lista()):
            if not self.fa.exists(str(i)):
                continue
            teljes = fmt.get("meret")
            self.fa.set(str(i), "meret",
                        meret_formazas(teljes * arany) if teljes else "–")

    def _boritokep_elonezet(self, info):
        """Kis előnézeti kép az info-kártyára (ffmpeg-gel png-vé alakítva,
        mert a Tk csak png/gif-et tud). Ha nem sikerül, marad az ikon."""
        try:
            if not os.path.isfile(FFMPEG_EXE):
                return
            url = None
            for t in reversed(info.get("thumbnails") or []):
                if t.get("url") and (t.get("width") or 0) >= 320:
                    url = t["url"]
            url = url or info.get("thumbnail")
            if not url:
                return
            # Laponként külön fájl: két lap egyszerre is elemezhet.
            nyers = os.path.join(APP_MAPPA, f"elonezet_{self.azonosito}.nyers")
            png = os.path.join(APP_MAPPA, f"elonezet_{self.azonosito}.png")
            with open(nyers, "wb") as f:
                f.write(http_tartomany(url, timeout=20, probalkozas=2))
            r = subprocess.run(
                [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", nyers, "-vf", "scale=140:79:force_original_aspect_ratio=increase,"
                 "crop=140:79", "-frames:v", "1", "-update", "1", png],
                capture_output=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                os.remove(nyers)
            except OSError:
                pass
            if r.returncode != 0 or not os.path.isfile(png):
                return

            def beallit():
                try:
                    self._boritokep = tk.PhotoImage(file=png)
                    self.kep_cimke.config(image=self._boritokep, text="",
                                          width=140, height=79)
                except tk.TclError as e:
                    self._fajlnaplo(f"előnézeti kép betöltése sikertelen: {e}")
            self._fo_szalon(beallit)
        except Exception as e:
            self._fajlnaplo(f"előnézeti kép hiba: {e}")

    # -------------------------------------------------------- letöltés -----

    def _valasztott_formatum(self):
        lista = self._aktualis_lista()
        if not lista:
            return None
        kijelolt = self.fa.selection()
        if not kijelolt:
            return lista[-1]
        try:
            return lista[int(kijelolt[0])]
        except (ValueError, IndexError):
            return lista[-1]

    def _szakasz_ellenorzes(self):
        """None = nincs szakasz, False = hibás megadás, egyébként (kezd, veg).

        A hívó `is False`-szal tesztel: a (0, 120) tuple igaz, de a 0 kezdet
        miatt könnyű elrontani."""
        if not self.szakasz_valtozo.get():
            return None
        kezd_szoveg = self.szakasz_kezd.get().strip()
        veg_szoveg = self.szakasz_veg.get().strip()
        if not kezd_szoveg and not veg_szoveg:
            messagebox.showwarning("Részlet", "Add meg a szakasz kezdetét és végét!\n"
                                              "Például: 3:00:00  →  3:20:00")
            return False

        kezd = 0 if not kezd_szoveg else ido_ertelmezes(kezd_szoveg)
        if kezd is None:
            messagebox.showwarning("Részlet", f"Érvénytelen kezdő időpont: {kezd_szoveg}\n"
                                              "Használható: 3:20:15 · 20:15 · 15")
            return False
        if not veg_szoveg:
            veg = self.video_hossz
            if not veg:
                messagebox.showwarning("Részlet", "Add meg a szakasz végét is!")
                return False
        else:
            veg = ido_ertelmezes(veg_szoveg)
            if veg is None:
                messagebox.showwarning("Részlet", f"Érvénytelen befejező időpont: {veg_szoveg}\n"
                                                  "Használható: 3:20:15 · 20:15 · 15")
                return False
        if veg <= kezd:
            messagebox.showwarning("Részlet", f"A vég ({ido_formazas(veg)}) nem lehet "
                                              f"korábban a kezdetnél ({ido_formazas(kezd)})!")
            return False
        if self.video_hossz and kezd >= self.video_hossz:
            messagebox.showwarning("Részlet", f"A kezdet ({ido_formazas(kezd)}) túl van "
                                              f"a videó végén ({ido_formazas(self.video_hossz)})!")
            return False
        if self.video_hossz and veg > self.video_hossz:
            self._log(f"⚠️ A megadott vég túllóg a videó hosszán, "
                      f"levágom {ido_formazas(self.video_hossz)}-ra.")
            veg = self.video_hossz
        return (kezd, veg)

    def _letoltes_inditasa(self):
        if self.letolt_fut:
            self._megallitas()
            return
        if self.fut:
            self._log("⚠️ Még fut az elemzés, egy pillanat…")
            return

        url = url_rendbetetel(self.url_mezo.get())
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Hiányzó link", "Illeszd be a videó linkjét!")
            return
        self.url_mezo.set(url)
        if not self._eszkozok_keszen():
            return

        mappa = self.mappa_valtozo.get().strip()
        if not mappa:
            messagebox.showwarning("Mentés helye", "Válaszd ki, hova mentsem a fájlt!")
            return
        try:
            os.makedirs(mappa, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Mentés helye", f"Nem tudok írni ebbe a mappába:\n{mappa}\n\n{e}")
            return

        szakasz = self._szakasz_ellenorzes()
        if szakasz is False:
            return

        playlist = self.playlist_valtozo.get()
        if playlist and "list=" not in url:
            if not messagebox.askyesno(
                    "Lejátszási lista",
                    "A teljes lista be van pipálva, de ez nem listás link.\n"
                    "Letöltsem egyetlen videóként?"):
                return
            playlist = False
            self.playlist_valtozo.set(False)
        elif not playlist and "list=" in url:
            if messagebox.askyesno(
                    "Lejátszási lista",
                    "Ez egy lejátszási lista linkje.\nLetöltsem az egész listát?"):
                playlist = True
                self.playlist_valtozo.set(True)
        if szakasz and playlist:
            if not messagebox.askyesno(
                    "Részlet",
                    "Részlet letöltésénél a lista nem támogatott.\n"
                    "Csak a link szerinti egy videó részlete töltődik le. Folytatod?"):
                return
            playlist = False
            self.playlist_valtozo.set(False)

        # A formátumazonosítók videónként egyediek: ha közben más linket írtak
        # be, a munkaszál újra lekérdezi őket - nem kell a felhasználót
        # visszaküldeni az Elemzés gombhoz.
        terv = {
            "url": url,
            "mod": self.mod,
            "mappa": mappa,
            "playlist": playlist,
            "szakasz": szakasz,
            "formatum": self._valasztott_formatum(),
            "friss": self.lekerdezett_url == url,
        }
        terv["konteneres"] = self._konteneres_feloldas(terv["formatum"])
        self.utolso_mappa = mappa
        self._beallitasok_mentese()

        self.fut = True
        self.letolt_fut = True
        self._megszakitva = False
        self.letoltes_gomb.config(text="⛔   Megszakítás")
        self.letoltes_gomb.stilus_valt("veszely")
        self.elemzes_gomb.config(state="disabled")
        self.lap_szazalek = 0.0
        self._haladas_beallit(None, "Indítás…", "Fo.Horizontal.TProgressbar")
        self._allapot("letöltés…", "kiemel", "letoltes")
        threading.Thread(target=self._letoltes, args=(terv,), daemon=True).start()

    def _megallitas(self):
        if not self.letolt_fut:
            return
        # A jelzőt akkor is be kell állítani, ha épp nincs futó folyamat: a
        # saját szakasz-letöltő a Range kérések között ezt figyeli.
        self._megszakitva = True
        self._log("⛔ Letöltés megszakítva.")
        self._allapot("megszakítva", "figyelem", "megszakitva")
        self._folyamat_leallitas()

    def _folyamat_leallitas(self):
        """A futó alfolyamat és minden gyereke kilövése.

        A yt-dlp külön ffmpeg folyamatot indít; sima terminate() után az
        árván maradt ffmpeg tovább töltene a háttérben."""
        proc = self.process
        if not proc or proc.poll() is not None:
            return
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self._fajlnaplo(f"taskkill sikertelen: {e}")
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _letoltes(self, terv):
        """Belépési pont: szakasz esetén a saját Range-letöltő, egyébként yt-dlp."""
        self.process = None
        siker = False
        try:
            # Videó módban kellenek a formátumazonosítók; ha nincsenek
            # (vagy más linkhez tartoznak), itt kérjük le őket.
            if terv["mod"] in ("video", "vr") and (not terv["friss"]
                                                   or not terv["formatum"]):
                terv["formatum"] = self._formatum_biztositas(terv["url"])

            if terv["szakasz"]:
                try:
                    if self._szakasz_letoltes(terv):
                        siker = True
                        return
                    if self._megszakitva:
                        return
                    self._log("↩️ Visszaesés a yt-dlp beépített szakaszolására "
                              "(ez a YouTube fojtása miatt lassabb).")
                except Exception as e:
                    self._log(f"⚠️ A gyors szakaszletöltő hibája: {e}")
                    self._fajlnaplo(traceback.format_exc())
                    self._log("↩️ Visszaesés a yt-dlp beépített szakaszolására.")
                if self._megszakitva:
                    return
            siker = self._letoltes_ytdlp(terv)
        except Exception as e:
            self._log(f"❌ Váratlan hiba: {e}")
            self._fajlnaplo(traceback.format_exc())
        finally:
            self.process = None
            self.fut = False
            self.letolt_fut = False
            self._letoltes_vege(siker)

    def _letoltes_vege(self, siker):
        if self._megszakitva:
            self._haladas_beallit(0, "Megszakítva.", "Fo.Horizontal.TProgressbar")
            self._allapot("megszakítva", "figyelem", "megszakitva")
        elif siker:
            self.lap_szazalek = 100.0
            self._haladas_beallit(100, "Kész! ✅", "Siker.Horizontal.TProgressbar")
            self._allapot("kész", "siker", "kesz")
        else:
            self._haladas_beallit(0, "Nem sikerült – nézd meg a naplót.",
                                  "Fo.Horizontal.TProgressbar")
            self._allapot("hiba", "hiba", "hiba")

        def gombok():
            self.letoltes_gomb.config(state="normal", text="↓   Letöltés")
            self.letoltes_gomb.stilus_valt("elsodleges")
            self.elemzes_gomb.config(state="normal")
        self._fo_szalon(gombok)

    def _formatum_biztositas(self, url):
        """Letöltés előtti utolsó pillanatos lekérdezés, ha nincs friss lista."""
        self._log("🔍 Formátumok frissítése a letöltés előtt…")
        info, hiba = formatumok_lekerese(url, lap=self.naplo_cimke)
        if hiba or not info:
            self._log(f"⚠️ Nem sikerült frissíteni a formátumokat: {hiba}")
            return None
        lista = formatum_csoportositas(info)
        if not lista:
            return None
        self.info = info
        self.formatumok = lista
        self.lekerdezett_url = url
        hossz = info.get("duration")
        self.video_hossz = int(hossz) if hossz else self.video_hossz
        self._fo_szalon(self._elemzes_megjelenites)
        valasztott = lista[-1]
        self._log(f"🎬 A legjobb minőséget használom: {valasztott['leiras']}")
        return valasztott

    # ---- saját szakasz-letöltő (Range kérésekkel, csak a kért tartomány) ----

    def _szakasz_formatum(self, mod, valasztott):
        """Formátum-kifejezés a szakaszhoz.

        Fontos: csak https/DASH stream jöhet szóba, mert a HLS (m3u8)
        változatban nincs byte-index, és a YouTube amúgy is lassabban adja."""
        hang = ("bestaudio[ext=m4a][protocol^=http]/bestaudio[protocol^=http]/"
                "bestaudio")
        if mod == "zene":
            return "bestaudio[protocol^=http]/bestaudio"
        if valasztott and valasztott.get("format_id"):
            azon = valasztott["format_id"]
            magassag = valasztott.get("height") or 0
            valtozatok = [f"{azon}+bestaudio[ext=m4a][protocol^=http]",
                          f"{azon}+bestaudio[protocol^=http]"]
            if magassag:
                valtozatok.append(
                    f"bestvideo[height={magassag}][protocol^=http]+"
                    f"bestaudio[ext=m4a][protocol^=http]")
            valtozatok.append(f"bestvideo[protocol^=http]+{hang}")
            return "/".join(valtozatok)
        return f"bestvideo[protocol^=http]+{hang}"

    def _boritokep_letoltes(self, info, cel):
        """Videó borítókép letöltése és jpg-vé alakítása. None, ha nem sikerült.

        A YouTube webp-et ad, amit az mp4 nem tud tárolni, ezért az ffmpeg-gel
        jpeg-re konvertáljuk. A kép hiánya nem hiba: a letöltés e nélkül is jó.
        """
        jeloltek = []
        if info.get("thumbnail"):
            jeloltek.append(info["thumbnail"])
        for t in reversed(info.get("thumbnails") or []):
            if t.get("url") and t["url"] not in jeloltek:
                jeloltek.append(t["url"])

        nyers = cel + ".nyers"
        for kep_url in jeloltek[:4]:
            try:
                adat = http_tartomany(kep_url, timeout=30, probalkozas=2)
                if not adat:
                    continue
                with open(nyers, "wb") as f:
                    f.write(adat)
                # A -update 1 kell, hogy egyetlen képet írjon, ne képsorozatot.
                # A páratlan méretet a jpeg nem szereti, ezért lefelé kerekítünk.
                if self._ffmpeg_futtatas(["-i", nyers, "-vf",
                                          "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                                          "-frames:v", "1", "-update", "1",
                                          "-q:v", "2", cel]):
                    self._fajlnaplo(f"borítókép kész: {kep_url[:120]}")
                    return cel
            except Exception as e:
                self._fajlnaplo(f"borítókép sikertelen ({kep_url[:80]}): {e}")
            finally:
                if os.path.isfile(nyers):
                    try:
                        os.remove(nyers)
                    except OSError:
                        pass
        self._log("   ⚠️ Borítóképet nem sikerült beágyazni (a videó rendben van).")
        return None

    def _stream_url_frissites(self, url, format_id):
        """Új aláírt URL kérése ugyanarra a formátumra (403/410 után)."""
        info, hiba = formatumok_lekerese(url, ["-f", str(format_id)],
                                         lap=self.naplo_cimke)
        if hiba or not info:
            self._fajlnaplo(f"URL frissítés sikertelen ({format_id}): {hiba}")
            return None
        for jelolt in (info.get("requested_formats") or [info]):
            if jelolt.get("url"):
                return jelolt["url"]
        return None

    def _szakasz_stream(self, stream, kezd, veg, cel, cimke, forras_url=None,
                        sulyok=(0.0, 1.0)):
        """Egy stream kért szakaszának letöltése sparse fájlba. True = sikerült."""
        stream_url = stream.get("url")
        if not stream_url:
            return False
        format_id = stream.get("format_id")
        alap_arany, arany_hossz = sulyok

        fejlec, teljes = http_tartomany_info(stream_url, 0, SZAKASZ_FEJLEC_MERET - 1)
        index = media_index_olvasas(fejlec)
        if not index:
            self._log(f"⚠️ {cimke}: nincs byte-index a streamben "
                      f"({stream.get('ext')}), így nem tudok részletet vágni.")
            return False

        # A fejlécnek (mp4: ftyp+moov+sidx, webm: EBML+Tracks+Cues) teljesen
        # meg kell lennie, hogy az ffmpeg értelmezni tudja a fájlt.
        if index["adat_kezd"] > len(fejlec):
            fejlec += http_tartomany(stream_url, len(fejlec), index["adat_kezd"] - 1)
        teljes = teljes or index.get("meret") or stream.get("filesize")
        if not teljes:
            self._log(f"⚠️ {cimke}: nem derül ki a stream mérete.")
            return False

        self._fajlnaplo(f"{cimke}: index={index['tipus']} pontok={len(index['pontok'])} "
                     f"adat_kezd={index['adat_kezd']} teljes={teljes} "
                     f"url={stream_url[:120]}...")

        tartomany = index_byte_tartomany(index, teljes, kezd, veg)
        if not tartomany:
            self._log(f"⚠️ {cimke}: a kért időtartomány kívül esik a videón.")
            return False
        byte_kezd, byte_veg = tartomany

        tartomanyok = []
        if index["tipus"] == "webm":
            elolap = webm_elolap_tartomany(index, byte_kezd)
            if elolap:
                tartomanyok.append(elolap)
        tartomanyok.append((byte_kezd, byte_veg))
        kell = sum(v - k for k, v in tartomanyok)

        self._fajlnaplo(f"{cimke}: byte tartomány {byte_kezd}-{byte_veg}, "
                     f"letöltendő szakaszok: {tartomanyok} ({kell} byte)")
        self._log(f"   {cimke}: {kell / 1024 / 1024:.0f} MB letöltése "
                  f"(a teljes {teljes / 1024 / 1024:.0f} MB helyett)")

        if not sparse_fajl_letrehozas(cel, teljes):
            self._log(f"⚠️ {cimke}: a célmappa nem támogatja a lyukas (sparse) fájlt, "
                      f"így {teljes / 1024 / 1024 / 1024:.1f} GB helyet foglalna. "
                      f"Válassz NTFS meghajtót!")
            return False

        kezdet_ido = time.monotonic()
        darabok = [(p, min(p + SZAKASZ_DARAB_MERET, szakasz_veg) - 1)
                   for szakasz_kezd, szakasz_veg in tartomanyok
                   for p in range(szakasz_kezd, szakasz_veg, SZAKASZ_DARAB_MERET)]
        # A darabokat párhuzamosan kérjük le: egy Range kérés önmagában nem
        # meríti ki a sávszélességet, több egyszerre viszont igen.
        kozos = {"url": stream_url, "frissitve": 0, "kesz": 0}
        url_zar = threading.Lock()
        iro_zar = threading.Lock()
        hibak = []

        with open(cel, "r+b") as f:
            f.seek(0)
            f.write(fejlec[:min(index["adat_kezd"], len(fejlec))])

            def egy_darab(tartomany):
                darab_kezd, darab_veg = tartomany
                for _ in range(5):
                    if self._megszakitva or hibak:
                        return
                    with url_zar:
                        aktualis = kozos["url"]
                    try:
                        adat = http_tartomany(aktualis, darab_kezd, darab_veg)
                    except UrlLejartHiba as e:
                        # Az aláírt URL több GB letöltése közben lejárhat vagy
                        # tiltásra kerülhet; kérjünk újat és folytassuk onnan.
                        # A zár alatt frissítünk, hogy a többi szál se
                        # kérjen közben feleslegesen újabb linket.
                        with url_zar:
                            if kozos["url"] == aktualis:
                                if kozos["frissitve"] >= 3 or not (forras_url and format_id):
                                    hibak.append(e)
                                    return
                                kozos["frissitve"] += 1
                                self._log(f"   {cimke}: a letöltési link lejárt ({e}), "
                                          f"új link kérése… ({kozos['frissitve']}/3)")
                                uj = self._stream_url_frissites(forras_url, format_id)
                                if not uj:
                                    hibak.append(e)
                                    return
                                kozos["url"] = uj
                        continue
                    except Exception as e:                # hálózati hiba
                        hibak.append(e)
                        return
                    if not adat:
                        hibak.append(RuntimeError(f"{cimke}: üres válasz a szervertől"))
                        return
                    with iro_zar:
                        f.seek(darab_kezd)
                        f.write(adat)
                        kozos["kesz"] += len(adat)
                        kesz = kozos["kesz"]
                    eltelt = max(time.monotonic() - kezdet_ido, 0.001)
                    arany = kesz / max(kell, 1)
                    self._haladas_fojtott(
                        (alap_arany + arany * arany_hossz) * 100,
                        f"{cimke.capitalize()} · {arany * 100:.0f}%  ·  "
                        f"{kesz / 1048576:.0f}/{kell / 1048576:.0f} MB  ·  "
                        f"{kesz / eltelt / 1048576:.1f} MB/s")
                    return
                hibak.append(RuntimeError(f"{cimke}: a darab letöltése "
                                          f"többszöri próbálkozásra sem sikerült"))

            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=SZAKASZ_PARHUZAM) as pool:
                list(pool.map(egy_darab, darabok))

        if hibak:
            raise hibak[0]
        if self._megszakitva:
            return False
        eltelt = time.monotonic() - kezdet_ido
        self._fajlnaplo(f"{cimke}: kész, {kozos['kesz']} byte {eltelt:.1f} s alatt "
                     f"({kozos['kesz'] / max(eltelt, 0.001) / 1048576:.1f} MB/s)")
        return True

    def _szakasz_letoltes(self, terv):
        """True = kész. False = nem sikerült, jöhet a yt-dlp tartalék út."""
        url, mod, mappa = terv["url"], terv["mod"], terv["mappa"]
        kezd, veg = terv["szakasz"]
        self._log(f"\n✂️ Részlet: {ido_formazas(kezd)} – {ido_formazas(veg)} "
                  f"({ido_formazas(veg - kezd)})")
        self._log("   Csak a kért byte-tartomány töltődik le, nem a teljes videó.")

        fmt_spec = self._szakasz_formatum(mod, terv.get("formatum"))
        self._fajlnaplo(f"Szakasz mód={mod} mappa={mappa} kezd={kezd} veg={veg} "
                     f"formátum-kifejezés={fmt_spec}")
        sort = (["-S", "abr,asr,proto"] if mod == "zene"
                else ["-S", "res,fps,hdr:12,proto,tbr"])
        self._haladas_beallit(None, "Stream URL-ek lekérdezése…")
        info, hiba = formatumok_lekerese(url, sort + ["-f", fmt_spec],
                                         lap=self.naplo_cimke)
        if hiba or not info:
            self._log(f"⚠️ Nem sikerült lekérni a stream URL-eket: {hiba}")
            return False

        streamek = info.get("requested_formats")
        if not streamek:
            if info.get("url"):
                streamek = [info]
            else:
                self._log("⚠️ A yt-dlp nem adott vissza közvetlen stream URL-t.")
                return False

        for s in streamek:
            van_kep = (s.get("vcodec") or "none") != "none"
            kodek = kodek_nev(s.get("vcodec") if van_kep else s.get("acodec"))
            if van_kep:
                self._log(f"   Videó: {s.get('height')}p {kodek} "
                          f"{s.get('tbr') or 0:.0f} kbps ({s.get('ext')})")
            else:
                self._log(f"   Hang: {kodek} "
                          f"{s.get('abr') or s.get('tbr') or 0:.0f} kbps ({s.get('ext')})")

        cim = fajlnev_tisztitas(info.get("title", "video"))
        cimke_ido = f"{ido_formazas(kezd)}_{ido_formazas(veg)}".replace(":", "-")
        alap = f"{cim}_3D_SBS" if mod == "vr" else cim
        alap = f"{alap}_[{cimke_ido}]"

        ideiglenes = []
        try:
            # A haladásjelzőn a streamek a méretük arányában osztoznak, a
            # végén 10% marad a vágásra/összefűzésre.
            meretek = [max(s.get("filesize") or s.get("filesize_approx") or 1, 1)
                       for s in streamek]
            osszes = sum(meretek)
            alap_arany = 0.0
            for i, stream in enumerate(streamek):
                van_kep = (stream.get("vcodec") or "none") != "none"
                cimke = "videó" if van_kep else "hang"
                kiterjesztes = stream.get("ext", "mp4")
                # A lapazonosító a névben azért kell, mert két lap ugyanazt a
                # videót és szakaszt is töltheti - azonos név esetén egymás
                # ideiglenes fájljába írnának.
                tmp = os.path.join(
                    mappa, f".{alap}.l{self.azonosito}.{i}.{kiterjesztes}.tmp")
                ideiglenes.append(tmp)
                arany_hossz = 0.9 * meretek[i] / osszes
                if not self._szakasz_stream(stream, kezd, veg, tmp, cimke,
                                            forras_url=url,
                                            sulyok=(alap_arany, arany_hossz)):
                    return False
                alap_arany += arany_hossz

            if self._megszakitva:
                return False

            media_fajlok = list(ideiglenes)
            self._haladas_beallit(92, "Borítókép…")
            boritokep = self._boritokep_letoltes(
                info, os.path.join(mappa, f".{alap}.l{self.azonosito}.jpg"))
            if boritokep:
                ideiglenes.append(boritokep)

            # Pontos vágás és összefűzés - újrakódolás nélkül (mp3-nál kódolás)
            if mod == "zene":
                cel = os.path.join(mappa, f"{alap}.mp3")
                args = ["-ss", str(kezd), "-to", str(veg), "-i", media_fajlok[0]]
                if boritokep:
                    args += ["-i", boritokep]
                # A szakasz-út nem a yt-dlp-n megy, ezért a választott MP3
                # bitrátát itt is alkalmazni kell (különben mindig V0 lenne).
                minoseg = (terv.get("formatum") or {}).get("minoseg", "0")
                args += ["-map", "0:a", "-c:a", "libmp3lame"]
                args += (["-q:a", "0"] if minoseg == "0"
                         else ["-b:a", minoseg.lower()])
                if boritokep:
                    args += ["-map", "1:v", "-c:v", "copy", "-id3v2_version", "3",
                             "-metadata:s:v", "title=Album cover",
                             "-metadata:s:v", "comment=Cover (front)"]
            else:
                # A konténer csak "doboz": a sávok -c copy-val kerülnek át,
                # újrakódolás sehol nincs.
                konteneres = terv.get("konteneres", "mp4")
                mp4_kimenet = konteneres != "mkv"
                cel = os.path.join(mappa, f"{alap}.{konteneres}")
                args = []
                for tmp in media_fajlok:
                    args += ["-ss", str(kezd), "-to", str(veg), "-i", tmp]
                if boritokep and mp4_kimenet:
                    args += ["-i", boritokep]
                args += ["-c", "copy"]
                if mp4_kimenet:
                    args += ["-movflags", "+faststart"]
                for i in range(len(media_fajlok)):
                    args += ["-map", f"{i}:0"]
                if boritokep and mp4_kimenet:
                    # Csatolt képként fűzzük be: a .mp4 thumbnail kezelője
                    # ({9DBD2C50-...}) ezt olvassa ki, ettől lesz előnézet az
                    # Explorerben. Az index a VIDEÓ streamek között értendő,
                    # ezért a média-videók számát kell megadni, nem a bemenetét.
                    video_db = sum(1 for s in streamek
                                   if (s.get("vcodec") or "none") != "none")
                    args += ["-map", f"{len(media_fajlok)}:0",
                             f"-disposition:v:{video_db}", "attached_pic"]
                elif boritokep:
                    # A Matroska a borítót nem streamként, hanem csatolmányként
                    # tárolja ("cover.jpg" néven ismerik fel a lejátszók).
                    args += ["-attach", boritokep,
                             "-metadata:s:t", "mimetype=image/jpeg",
                             "-metadata:s:t", "filename=cover.jpg"]

            args += ["-metadata", f"title={info.get('title', '')}", cel]

            self._log("   Vágás és összefűzés…")
            self._haladas_beallit(94, "Vágás és összefűzés…")
            if not self._ffmpeg_futtatas(args, hossz=veg - kezd, alap_szazalek=94):
                return False

            meret = os.path.getsize(cel) / 1024 / 1024
            self._log(f"✅ Kész: {os.path.basename(cel)}  ({meret:.0f} MB)")
            return True
        finally:
            for tmp in ideiglenes:
                try:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                except OSError:
                    pass

    def _ffmpeg_futtatas(self, args, hossz=None, alap_szazalek=None):
        """ffmpeg futtatása. `hossz` esetén a haladást is jelzi."""
        parancs = [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error"]
        if hossz:
            parancs += ["-progress", "pipe:1", "-nostats"]
        parancs += args
        naplo_parancs("ffmpeg parancs", parancs, self.naplo_cimke)
        self.process = subprocess.Popen(
            parancs, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        utolso = ""
        for nyers in self.process.stdout:
            sor = sor_dekodolas(nyers).strip()
            if not sor:
                continue
            if hossz and sor.startswith("out_time_us="):
                try:
                    mp = int(sor.split("=", 1)[1]) / 1e6
                    arany = max(0.0, min(1.0, mp / max(hossz, 0.001)))
                    self._haladas_fojtott(
                        alap_szazalek + arany * (100 - alap_szazalek),
                        f"Vágás és összefűzés · {arany * 100:.0f}%")
                except ValueError:
                    pass
                continue
            if "=" in sor and sor.split("=", 1)[0].islower() and " " not in sor:
                continue                       # egyéb -progress kulcs=érték sor
            utolso = sor
            self._log(f"[ffmpeg] {sor}", csak_fajlba=True)
        self.process.wait()
        self._fajlnaplo(f"ffmpeg kilépési kód: {self.process.returncode}")
        if self.process.returncode != 0:
            if not self._megszakitva:
                self._log(f"❌ ffmpeg hiba: {utolso}")
            return False
        return True

    # ------------------------------------------------------- yt-dlp letöltés --

    def _letoltes_ytdlp(self, terv):
        url, mod, mappa = terv["url"], terv["mod"], terv["mappa"]
        szakasz, playlist = terv["szakasz"], terv["playlist"]
        valasztott = terv.get("formatum")

        parancs = [YTDLP_EXE, "--ffmpeg-location", FFMPEG_MAPPA,
                   "--windows-filenames", "--newline", "--progress",
                   "--progress-delta", "0.3", "--no-warnings",
                   # A DASH darabokat párhuzamosan szedi le: egyetlen kapcsolat
                   # nem meríti ki a sávszélességet, négy viszont igen.
                   "--concurrent-fragments", "4",
                   "--retries", "10", "--fragment-retries", "10",
                   "--file-access-retries", "5",
                   "--embed-chapters"]

        # Minőségi sorrend: felbontás > fps > HDR > protokoll > bitráta.
        # A protokoll azért van a bitráta ELŐTT, mert a YouTube HLS
        # változatának hamisan magas a bitrátája: nélküle mindig az nyerne,
        # pedig ugyanaz a stream, csak lassabb és nincs byte-indexe.
        video_sort = ["-S", "res,fps,hdr:12,proto,tbr"]

        if mod in ("video", "vr"):
            # A VR ugyanaz a letöltés, mint a videó - a különbség csak a
            # fájlnév _3D_SBS jelölése (sztereó átalakítás nem történik).
            # A --merge-output-format CSAK a konténert szabja meg: a videó- és
            # hangsáv másolódik (-c copy), nincs újrakódolás.
            parancs += video_sort
            parancs += ["-f", self._video_formatum_kifejezes(valasztott),
                        "--merge-output-format", terv.get("konteneres", "mp4"),
                        "--embed-thumbnail", "--embed-metadata"]
        else:
            # A forrás mindig a legjobb hangsáv; a választás csak a kimenet
            # bitrátáját érinti ("0" = V0 VBR, egyébként pl. "320K").
            minoseg = (valasztott or {}).get("minoseg", "0")
            parancs += ["-S", "abr,asr,acodec:opus,proto",
                        "-f", "bestaudio[protocol^=http]/bestaudio/best",
                        "-x", "--audio-format", "mp3", "--audio-quality", minoseg,
                        "--embed-thumbnail", "--embed-metadata"]

        nev_sablon = "%(title)s_3D_SBS.%(ext)s" if mod == "vr" else "%(title)s.%(ext)s"

        if szakasz:
            kezd, veg = szakasz
            parancs += ["--download-sections", f"*{kezd}-{veg}"]
            # A szakasz a fájlnévbe is bekerül, hogy a különböző részletek ne
            # írják felül egymást (és ne higgye a yt-dlp már letöltöttnek).
            cimke = f"{ido_formazas(kezd)}_{ido_formazas(veg)}".replace(":", "-")
            nev, kit = nev_sablon.rsplit(".", 1)
            nev_sablon = f"{nev}_[{cimke}].{kit}"

        if playlist:
            parancs += ["--yes-playlist", "-o",
                        os.path.join(mappa, "%(playlist_title)s",
                                     "%(playlist_index)03d - " + nev_sablon)]
        else:
            parancs += ["--no-playlist", "-o", os.path.join(mappa, nev_sablon)]

        parancs += [url]

        self._log(f"\n⬇️ Letöltés indul: {url}")
        naplo_parancs("yt-dlp parancs", parancs, self.naplo_cimke)
        allapot = {"fazis": 0, "mar_letoltve": False, "hiba": False, "fajl": None}
        try:
            self.process = subprocess.Popen(
                parancs, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW, env=_ytdlp_kornyezet())
            for nyers in self.process.stdout:
                sor = sor_dekodolas(nyers).rstrip()
                if sor.strip():
                    self._ytdlp_sor(sor, allapot, mod)
            self.process.wait()

            if self._megszakitva:
                return False
            if allapot["hiba"]:
                self._log("❌ A letöltés hibára futott. Részletek a naplóban.")
                return False
            if allapot["mar_letoltve"]:
                self._log("⚠️ Ez a fájl már le van töltve ebbe a mappába.")
                return True
            if self.process.returncode == 0:
                if allapot["fajl"]:
                    self._log(f"✅ Kész: {os.path.basename(allapot['fajl'])}")
                else:
                    self._log("✅ Sikeres letöltés!")
                return True
            self._log(f"❌ A yt-dlp hibakóddal állt le ({self.process.returncode}).")
            return False
        except Exception as e:
            self._log(f"❌ Hiba: {e}")
            self._fajlnaplo(traceback.format_exc())
            return False

    def _video_formatum_kifejezes(self, valasztott):
        """A kiválasztott minőséghez tartozó -f kifejezés.

        Ha a konkrét formátumazonosító már nem érvényes, ugyanarra a
        felbontásra esünk vissza - nem egy alacsonyabbra."""
        if not valasztott or not valasztott.get("format_id"):
            return ("bestvideo[protocol^=http]+bestaudio[protocol^=http]/"
                    "bestvideo*+bestaudio/best")
        azon = valasztott["format_id"]
        magassag = valasztott.get("height") or 0
        hang = valasztott.get("hang_id")
        valtozatok = []
        if hang:
            valtozatok.append(f"{azon}+{hang}")
        valtozatok.append(f"{azon}+bestaudio[protocol^=http]")
        valtozatok.append(azon)                       # ha saját hangja van
        if magassag:
            valtozatok += [
                f"bestvideo[height={magassag}][protocol^=http]+bestaudio[protocol^=http]",
                f"bestvideo[height={magassag}]+bestaudio",
                f"bestvideo[height<={magassag}]+bestaudio",
                f"best[height<={magassag}]",
            ]
        valtozatok.append("bestvideo*+bestaudio/best")
        return "/".join(valtozatok)

    def _ytdlp_sor(self, sor, allapot, mod):
        """Egy yt-dlp kimeneti sor feldolgozása: haladás vagy naplósor."""
        haladas = haladas_ertelmezes(sor)
        if haladas:
            szazalek, meret, sebesseg, hatra = haladas
            fazis = {0: "Letöltés", 1: "Videó", 2: "Hang"}.get(
                allapot["fazis"] if mod != "zene" else 0, "Letöltés")
            reszek = [f"{fazis} · {szazalek:.1f}%"]
            if meret:
                reszek.append(f"{meret.strip()}")
            if sebesseg:
                reszek.append(sebesseg)
            if hatra:
                reszek.append(f"hátra {hatra}")
            # A két sáv (videó, hang) a teljes sáv felén-felén osztozik.
            if mod != "zene" and allapot["fazis"] == 2:
                teljes = 50 + szazalek / 2
            elif mod != "zene" and allapot["fazis"] == 1:
                teljes = szazalek / 2
            else:
                teljes = szazalek
            self._haladas_fojtott(teljes, "  ·  ".join(reszek))
            self._log(sor, csak_fajlba=True)
            return

        if "ERROR:" in sor:
            allapot["hiba"] = True
            self._log(f"❌ {ytdlp_hiba_forditas(sor)}")
            return
        if "has already been downloaded" in sor:
            allapot["mar_letoltve"] = True

        # A célfájl neve többször is változik (letöltés -> egyesítés -> mp3),
        # mindig az utolsó a végleges - azt jelentjük a felhasználónak.
        if "Destination:" in sor:
            allapot["fajl"] = sor.split("Destination:", 1)[1].strip()
            if sor.lstrip().startswith("[download]"):
                allapot["fazis"] += 1
                self._log(f"   → {os.path.basename(allapot['fajl'])}")
            self._log(sor, csak_fajlba=True)
            return
        egyesites = re.search(r'Merging formats into "(.+)"', sor)
        if egyesites:
            allapot["fajl"] = egyesites.group(1)

        # Az utómunka-fázisok: érthető magyar állapot a nyers címkék helyett.
        for cimke, szazalek, uzenet in (
                ("[Merger]", 96, "Videó és hang egyesítése…"),
                ("[ExtractAudio]", 90, "MP3 készítése…"),
                ("[ThumbnailsConvertor]", 97, "Borítókép előkészítése…"),
                ("[EmbedThumbnail]", 98, "Borítókép beágyazása…"),
                ("[Metadata]", 98, "Adatok beágyazása…")):
            if cimke in sor:
                self._haladas_beallit(szazalek, uzenet)
                self._log(f"   {uzenet}")
                self._log(sor, csak_fajlba=True)
                return

        # A yt-dlp belső üzenetei (kliens, formátumválasztás, takarítás) csak
        # a naplófájlba valók - a felületen csak zajt csinálnának.
        if sor.lstrip().startswith("[") or sor.startswith("Deleting original file"):
            self._log(sor, csak_fajlba=True)
            return
        self._log(sor)


class Alkalmazas(tk.Tk, FeluletSegedek):
    """Az ablak: lapfülek, közös stílus, ikon, eszközök letöltése.

    A tényleges munkamenet a Lap osztályban van - abból több is futhat
    egyszerre, egymástól függetlenül, saját letöltőszállal."""

    LAP_MAX = 8            # ennél több egyidejű letöltés már a sávot fojtaná

    def __init__(self):
        super().__init__()
        self.beallitas = beallitasok_betoltes()
        self.title(PROGRAM_NEV)
        self.configure(bg=SZIN["hatter"])

        self._destroyed = False
        self.lapok = []
        self.aktiv_lap = None
        self._lap_szamlalo = 0
        self._frissites_ablak_nyitva = None
        self.elerheto_frissites = None      # (build, exe_url), ha van újabb
        # A közös (nem laphoz kötött) üzenetek, hogy a később nyitott lapok
        # naplójában is ott legyenek - pl. az eszközök ellenőrzése.
        self.kozos_naplo = []

        self._ablak_meret()
        self._ikon_beallitas()
        self._stilus_beallitas()
        self._ui_felepites()
        self.uj_lap()

        self.protocol("WM_DELETE_WINDOW", self._kilep)
        self.bind_all("<Control-t>", lambda e: self.uj_lap())
        self.bind_all("<Control-w>", lambda e: self.lap_bezaras(self.aktiv_lap))
        self.bind_all("<Control-Tab>", lambda e: self.kovetkezo_lap())
        threading.Thread(target=self._init_eszkozok, daemon=True).start()
        # Kis késleltetéssel, hogy a főablak biztosan előbb megjelenjen.
        self.after(1500, self.frissites_kereses)

    # ------------------------------------------------------------ felület --

    def _ikon_beallitas(self):
        """Ablak- és tálcaikon. Exe-ből futva a PyInstaller kicsomagolt
        mappájából, forrásból a szkript mellől."""
        alap = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        utvonal = os.path.join(alap, "icon_youtube_letolto.ico")
        if os.path.isfile(utvonal):
            try:
                self.iconbitmap(utvonal)
            except tk.TclError as e:
                fajlba_naplo(f"ikon betöltése sikertelen: {e}")

    def _ablak_meret(self):
        sz = min(1000, self.winfo_screenwidth() - 80)
        m = min(950, self.winfo_screenheight() - 110)
        self.geometry(f"{max(sz, 860)}x{max(m, 620)}")
        # A minimum azért kell, hogy a napló és a haladásjelző soha ne
        # szoruljon ki az ablakból (kis ablakban korábban eltűnt).
        self.minsize(840, 600)

    def _stilus_beallitas(self):
        st = ttk.Style(self)
        st.theme_use("clam")

        st.configure("Treeview",
                     background=SZIN["mezo"], fieldbackground=SZIN["mezo"],
                     foreground=SZIN["szoveg"], borderwidth=0, relief="flat",
                     rowheight=30, font=(BETU, 10))
        st.map("Treeview",
               background=[("selected", SZIN["kiemel"])],
               foreground=[("selected", "#ffffff")])
        st.configure("Treeview.Heading",
                     background=SZIN["panel2"], foreground=SZIN["halvany"],
                     relief="flat", borderwidth=0, padding=(10, 7),
                     font=(BETU, 9, "bold"))
        st.map("Treeview.Heading", background=[("active", SZIN["keret2"])])

        # Legördülő: a mező a clam témában külön színezhető, a lenyíló lista
        # viszont egy sima Listbox - azt csak option_add-dal lehet elérni.
        st.configure("Sotet.TCombobox",
                     fieldbackground=SZIN["mezo"], background=SZIN["panel2"],
                     foreground=SZIN["szoveg"], arrowcolor=SZIN["halvany"],
                     bordercolor=SZIN["keret"], lightcolor=SZIN["keret"],
                     darkcolor=SZIN["keret"], relief="flat", padding=(8, 2))
        st.map("Sotet.TCombobox",
               fieldbackground=[("readonly", SZIN["mezo"]),
                                ("disabled", SZIN["panel"])],
               foreground=[("disabled", SZIN["halvany2"])],
               arrowcolor=[("disabled", SZIN["halvany2"])],
               bordercolor=[("focus", SZIN["kiemel"])])
        self.option_add("*TCombobox*Listbox.background", SZIN["mezo"])
        self.option_add("*TCombobox*Listbox.foreground", SZIN["szoveg"])
        self.option_add("*TCombobox*Listbox.selectBackground", SZIN["kiemel"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", (BETU, 9))

        st.configure("Fo.Horizontal.TProgressbar",
                     troughcolor=SZIN["panel2"], bordercolor=SZIN["panel2"],
                     background=SZIN["kiemel"], lightcolor=SZIN["kiemel"],
                     darkcolor=SZIN["kiemel"], thickness=10)
        st.configure("Siker.Horizontal.TProgressbar",
                     troughcolor=SZIN["panel2"], bordercolor=SZIN["panel2"],
                     background=SZIN["siker"], lightcolor=SZIN["siker"],
                     darkcolor=SZIN["siker"], thickness=10)

        # Nyilak nélküli, keskeny görgetősáv - a rendszer alapértelmezett
        # nyilai világosak, és elrontanák a sötét felületet.
        st.layout("Vertical.TScrollbar",
                  [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                      ("Vertical.Scrollbar.thumb",
                       {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Vertical.TScrollbar",
                     background=SZIN["keret2"], troughcolor=SZIN["mezo"],
                     bordercolor=SZIN["mezo"], darkcolor=SZIN["keret2"],
                     lightcolor=SZIN["keret2"], arrowcolor=SZIN["halvany"],
                     relief="flat", borderwidth=0, width=10)
        st.map("Vertical.TScrollbar",
               background=[("active", SZIN["halvany2"])])

    def _ui_felepites(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)         # a lapok területe nyúlik

        self._fejlec_epites()
        self._lapcsik_epites()

        self.lap_tarolo = tk.Frame(self, bg=SZIN["hatter"])
        self.lap_tarolo.grid(row=2, column=0, sticky="nsew")
        self.lap_tarolo.grid_columnconfigure(0, weight=1)
        self.lap_tarolo.grid_rowconfigure(0, weight=1)

        self._lablec_epites()

    def _fejlec_epites(self):
        keret = tk.Frame(self, bg=SZIN["panel"], height=52)
        keret.grid(row=0, column=0, sticky="ew")
        keret.grid_propagate(False)

        bal = tk.Frame(keret, bg=SZIN["panel"])
        bal.pack(side="left", padx=(20, 0), pady=10)
        tk.Frame(bal, bg=SZIN["kiemel"], width=4).pack(side="left", fill="y",
                                                       pady=2, padx=(0, 12))
        self._cimke(bal, PROGRAM_NEV, 14, SZIN["szoveg"], True).pack(side="left")

        jobb = tk.Frame(keret, bg=SZIN["panel"])
        jobb.pack(side="right", padx=20, pady=10)
        self.allapot_pont = tk.Label(jobb, text="●", bg=SZIN["panel"],
                                     fg=SZIN["figyelem"], font=(BETU, 11))
        self.allapot_pont.pack(side="left", padx=(0, 6))
        self.allapot_cimke = self._cimke(jobb, "indulás…", 10, SZIN["halvany"])
        self.allapot_cimke.pack(side="left")

    def _lapcsik_epites(self):
        keret = tk.Frame(self, bg=SZIN["panel2"])
        keret.grid(row=1, column=0, sticky="ew")
        tk.Frame(self, bg=SZIN["keret"], height=1).grid(row=1, column=0,
                                                        sticky="sew")

        # A "+" gomb a fülök közé, a jobb szélső mögé kerül (lásd
        # _lapcsik_ujraepites) - ahogy a böngészőkben megszokott.
        self.lap_csik = tk.Frame(keret, bg=SZIN["panel2"])
        self.lap_csik.pack(side="left", fill="both", expand=True,
                           padx=16, pady=6)

    def _lablec_epites(self):
        """Build szám kicsiben, bal alul - hibabejelentéskor ez az első kérdés,
        de ne foglalja az ablak címsorát."""
        keret = tk.Frame(self, bg=SZIN["hatter"], padx=20)
        keret.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        self._cimke(keret, f"build {BUILD_SZAM}", 8,
                    SZIN["halvany2"]).pack(side="left")
        self._cimke(keret, "·", 8, SZIN["halvany2"]).pack(side="left", padx=6)
        # Kézi keresés: akkor is működik, ha az értesítést kikapcsolták.
        link = self._cimke(keret, "Frissítés keresése", 8, SZIN["halvany2"],
                           cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: self.frissites_kereses(kezi=True))
        link.bind("<Enter>", lambda e: link.config(fg=SZIN["kiemel"]))
        link.bind("<Leave>", lambda e: link.config(fg=SZIN["halvany2"]))

        # Ha van újabb build, itt marad egy kis jelzés akkor is, ha a felugró
        # ablakot elnyomták - erre kattintva bármikor előhozható.
        self.frissites_jelzo = self._cimke(keret, "", 8, SZIN["siker"],
                                           cursor="hand2")
        self.frissites_jelzo.bind("<Button-1>", lambda e: self._frissites_elohozas())

    # ----------------------------------------------------------- lapfülek --

    def uj_lap(self):
        """Új, üres lap - minden érték alapállapotban."""
        if len(self.lapok) >= self.LAP_MAX:
            messagebox.showinfo(
                "Lapok", f"Egyszerre legfeljebb {self.LAP_MAX} lap lehet nyitva.")
            return None
        self._lap_szamlalo += 1
        lap = Lap(self, self.lap_tarolo, self._lap_szamlalo)
        self.lapok.append(lap)
        # A közös üzenetek (eszközök ellenőrzése stb.) az új lap naplójába is
        # kerüljenek be, különben üresen indul.
        for sor in self.kozos_naplo:
            lap.naplo_kiiras(sor)
        self.lap_valtas(lap)
        return lap

    def lap_valtas(self, lap):
        if lap is None or lap not in self.lapok:
            return
        for l in self.lapok:
            if l is lap:
                l.grid(row=0, column=0, sticky="nsew")
            else:
                l.grid_remove()
        self.aktiv_lap = lap
        self._lapcsik_ujraepites()
        lap.fokusz()
        # Az újra megjelenített lapon a táblázat szélességét igazítani kell.
        lap.after_idle(lap._fa_szelesseg_igazitas)

    def kovetkezo_lap(self):
        if len(self.lapok) > 1 and self.aktiv_lap in self.lapok:
            i = self.lapok.index(self.aktiv_lap)
            self.lap_valtas(self.lapok[(i + 1) % len(self.lapok)])

    def lap_bezaras(self, lap):
        if lap is None or lap not in self.lapok:
            return
        if lap.letolt_fut and not messagebox.askyesno(
                "Lap bezárása",
                "Ezen a lapon még fut egy letöltés.\nMegszakítod és bezárod?"):
            return
        lap.lezaras()
        self.lapok.remove(lap)
        lap.destroy()
        if not self.lapok:
            self.uj_lap()                      # mindig maradjon egy lap
        elif self.aktiv_lap is lap:
            self.lap_valtas(self.lapok[-1])
        else:
            self._lapcsik_ujraepites()

    FUL_SZELES = 220           # ennyi hely jut egy fülnek, amíg belefér
    FUL_FER = 4                # ennyi fül fér ki normál ablakszélességben

    def _lapcsik_ujraepites(self):
        """A fülek teljes újrarajzolása (nyitás/zárás/váltás után).

        Kevés fül fix szélességű - egyetlen lap ne nyúljon végig a sávon -,
        soknál viszont egyenletesen összébb húzódnak, hogy mind kiférjen."""
        for w in self.lap_csik.winfo_children():
            w.destroy()
        # A korábbi oszlopbeállítások megmaradnának lapok bezárása után is,
        # ezért előbb mindet nullázzuk.
        for i in range(self.LAP_MAX + 2):
            self.lap_csik.grid_columnconfigure(i, weight=0, uniform="", minsize=0)
        szuk = len(self.lapok) > self.FUL_FER
        for i, lap in enumerate(self.lapok):
            if szuk:
                self.lap_csik.grid_columnconfigure(
                    i, weight=1, uniform="ful", minsize=0)
            else:
                self.lap_csik.grid_columnconfigure(
                    i, weight=0, uniform="", minsize=self.FUL_SZELES)
            self._ful_epites(lap, i)

        # "+" közvetlenül a jobb szélső fül mögött, mint a böngészőkben.
        n = len(self.lapok)
        self.uj_gomb = Gomb(self.lap_csik, "+", self.uj_lap, "masodlagos",
                            meret=13, vastag=True, padx=11, pady=2)
        self.uj_gomb.grid(row=0, column=n, sticky="w", padx=(2, 0))
        # A maradék helyet egy üres oszlop nyeli el, hogy a fülek ne nyúljanak.
        # Ha viszont szűkösen vagyunk, minden hely a füleké.
        self.lap_csik.grid_columnconfigure(
            n + 1, weight=0 if szuk else 1, uniform="", minsize=0)

    def _ful_epites(self, lap, oszlop):
        aktiv = lap is self.aktiv_lap
        hatter = SZIN["hatter"] if aktiv else SZIN["panel"]
        ful = tk.Frame(self.lap_csik, bg=hatter, padx=10, pady=5, cursor="hand2")
        ful.grid(row=0, column=oszlop, sticky="ew", padx=(0, 3))

        lap.ful_ikon = tk.Label(ful, bg=hatter, font=(BETU, 9, "bold"), width=4)
        lap.ful_ikon.pack(side="left", padx=(0, 6))
        lap.ful_cimke = tk.Label(ful, bg=hatter, anchor="w",
                                 fg=SZIN["szoveg"] if aktiv else SZIN["halvany"],
                                 font=(BETU, 9, "bold" if aktiv else "normal"))
        lap.ful_cimke.pack(side="left", fill="x", expand=True)

        zar = tk.Label(ful, text="✕", bg=hatter, fg=SZIN["halvany2"],
                       font=(BETU, 8), cursor="hand2")
        zar.pack(side="right", padx=(6, 0))
        zar.bind("<Button-1>", lambda e, l=lap: self.lap_bezaras(l))
        zar.bind("<Enter>", lambda e, w=zar: w.config(fg=SZIN["hiba"]))
        zar.bind("<Leave>", lambda e, w=zar: w.config(fg=SZIN["halvany2"]))

        # A gyerek widgetek elnyelnék a kattintást, ezért mindegyikre kell.
        for w in (ful, lap.ful_ikon, lap.ful_cimke):
            w.bind("<Button-1>", lambda e, l=lap: self.lap_valtas(l))
        self.lap_jelzes_frissites(lap)

    def lap_jelzes_frissites(self, lap):
        """Egy fül állapotjelzőjének frissítése - ezt hívja a Lap, amikor
        elemez, tölt vagy elkészül."""
        ikon = getattr(lap, "ful_ikon", None)
        if ikon is None:
            return
        try:
            if not ikon.winfo_exists():
                return
            jel, szin = lap.jelzes()
            ikon.config(text=jel, fg=SZIN[szin])
            lap.ful_cimke.config(text=lap.ful_felirat())
        except tk.TclError:
            pass

    # ------------------------------------------------------------ közösek --

    def _fo_szalon(self, fv, *argumentumok):
        if self._destroyed:
            return
        try:
            self.after(0, lambda: None if self._destroyed else fv(*argumentumok))
        except RuntimeError:
            pass

    def _allapot(self, szoveg, szin="halvany"):
        """A fejléc jobb oldalán látszó közös állapot (eszközök, hibák)."""
        def frissit():
            self.allapot_cimke.config(text=szoveg, fg=SZIN[szin])
            self.allapot_pont.config(fg=SZIN[szin if szin != "halvany" else "halvany2"])
        self._fo_szalon(frissit)

    def _log(self, szoveg, csak_fajlba=False):
        """Közös üzenet: a fájlba egyszer, a naplóablakba minden nyitott lapon.

        Megjegyezzük is, hogy a később nyitott lapok naplója se legyen üres."""
        fajlba_naplo(szoveg)
        if csak_fajlba:
            return
        self.kozos_naplo.append(szoveg)
        del self.kozos_naplo[:-50]
        for lap in list(self.lapok):
            lap.naplo_kiiras(szoveg)

    # ------------------------------------------------------------ eszközök --

    def _init_eszkozok(self):
        try:
            self._allapot("eszközök ellenőrzése…", "figyelem")
            self._log("Eszközök ellenőrzése…")
            eszközök_letöltése(self._log)
            ytdlp_frissites(self._log)
            self._verziok_naplozasa()
            self._log("✅ Kész. Illeszd be a videó linkjét.")
            self._allapot("készen áll", "siker")
        except Exception as e:
            self._log(f"❌ Hiba az eszközök letöltésekor: {e}")
            self._allapot("eszközhiba", "hiba")
            fajlba_naplo(traceback.format_exc())

    def _verziok_naplozasa(self):
        """Verziók a naplóba - hibakereséskor ez az első kérdés."""
        for cimke, parancs in (("yt-dlp", [YTDLP_EXE, "--version"]),
                               ("ffmpeg", [FFMPEG_EXE, "-version"])):
            try:
                r = subprocess.run(parancs, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                elso = (r.stdout or r.stderr or "").strip().splitlines()
                fajlba_naplo(f"{cimke} verzió: {elso[0] if elso else '?'}")
            except Exception as e:
                fajlba_naplo(f"{cimke} verzió lekérése sikertelen: {e}")

    def beallitasok_mentese(self, **valtozasok):
        """Beolvasztja a változásokat a közös beállításokba, majd ment.

        Azért nem felülírással, mert több forrás is ír bele (lapok, frissítési
        párbeszéd) - a teljes szótár kiírása eltüntetné a másik beállításait."""
        self.beallitas.update(valtozasok)
        beallitasok_mentes(self.beallitas)

    # --------------------------------------------------------- frissítés --

    def frissites_kereses(self, kezi=False):
        """Van-e újabb build a GitHubon. Induláskor automatikusan fut, de a
        lábléc linkjéről kézzel is indítható.

        A keresés akkor is lefut, ha a felugró ablakot kikapcsolták - a
        láblécbeli jelzés ilyenkor is megjelenik."""
        threading.Thread(target=self._frissites_worker, args=(kezi,),
                         daemon=True).start()

    def _frissites_worker(self, kezi):
        try:
            uj, exe_url = legujabb_kiadas()
        except Exception as e:
            fajlba_naplo(f"frissítés-ellenőrzés sikertelen: {e}")
            if kezi:
                self._fo_szalon(messagebox.showwarning, "Frissítés",
                                f"Nem sikerült ellenőrizni a frissítést:\n{e}")
            return
        fajlba_naplo(f"frissítés-ellenőrzés: helyi build {BUILD_SZAM}, "
                     f"GitHubon {uj}")
        if uj and uj > BUILD_SZAM:
            self.elerheto_frissites = (uj, exe_url)
            self._fo_szalon(self._frissites_jelzo_frissites)
            if kezi or self.beallitas.get("frissites_ertesites", True):
                self._fo_szalon(self._frissites_ablak, uj, exe_url)
        elif kezi:
            self._fo_szalon(messagebox.showinfo, "Frissítés",
                            f"A legfrissebb verziót használod (build {BUILD_SZAM}).")

    def _frissites_jelzo_frissites(self):
        if not self.elerheto_frissites:
            return
        uj = self.elerheto_frissites[0]
        self.frissites_jelzo.config(text=f"●  Új verzió: build {uj}")
        self.frissites_jelzo.pack(side="left", padx=(10, 0))

    def _frissites_elohozas(self):
        if self.elerheto_frissites:
            self._frissites_ablak(*self.elerheto_frissites)

    def _frissites_ablak(self, uj, exe_url):
        if getattr(self, "_frissites_ablak_nyitva", None):
            return
        ablak = FrissitesAblak(self, uj, exe_url)
        self._frissites_ablak_nyitva = ablak
        ablak.bind("<Destroy>",
                   lambda e: setattr(self, "_frissites_ablak_nyitva", None))

    def kilepes_frissiteshez(self):
        """Kilépés kérdés nélkül: a segédszkript már vár a folyamat végére."""
        self._destroyed = True
        for lap in self.lapok:
            lap.lezaras()
        self.destroy()

    def _kilep(self):
        futo = [l for l in self.lapok if l.letolt_fut]
        if futo and not messagebox.askyesno(
                "Kilépés",
                f"{len(futo)} lapon még fut letöltés.\nMegszakítod és kilépsz?"):
            return
        self._destroyed = True
        if self.aktiv_lap:
            try:
                self.aktiv_lap._beallitasok_mentese()
            except Exception:
                pass
        for lap in self.lapok:
            lap.lezaras()
        self.destroy()


def _globalis_hibakezeles():
    """Minden elkapatlan kivétel a naplóba kerül - GUI alkalmazásnál nincs
    konzol, ami kiírná őket."""
    def hook(tipus, ertek, tb):
        fajlba_naplo("ELKAPATLAN KIVÉTEL:\n"
                     + "".join(traceback.format_exception(tipus, ertek, tb)))
        sys.__excepthook__(tipus, ertek, tb)

    sys.excepthook = hook
    if hasattr(threading, "excepthook"):
        def szal_hook(args):
            fajlba_naplo(f"KIVÉTEL A(Z) {args.thread.name} SZÁLBAN:\n"
                         + "".join(traceback.format_exception(
                             args.exc_type, args.exc_value, args.exc_traceback)))
        threading.excepthook = szal_hook


if __name__ == "__main__":
    _globalis_hibakezeles()
    fajlba_naplo("=" * 60)
    fajlba_naplo(f"Indulás | Python {sys.version.split()[0]} | "
                 f"{'exe' if getattr(sys, 'frozen', False) else 'forrás'} | "
                 f"napló: {NAPLO_FAJL}")
    try:
        app = Alkalmazas()
        app.mainloop()
    except Exception:
        fajlba_naplo("VÉGZETES HIBA:\n" + traceback.format_exc())
        raise
    finally:
        fajlba_naplo("Kilépés.")
