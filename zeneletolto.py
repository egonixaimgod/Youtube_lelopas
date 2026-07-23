import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import os
import re
import sys
import time
import json
import struct
import datetime
import traceback
import urllib.request
import urllib.error
import zipfile
import shutil

APP_MAPPA = os.path.join(os.getenv("LOCALAPPDATA", "."), "ZeneLetolto")
YTDLP_EXE = os.path.join(APP_MAPPA, "yt-dlp.exe")
FFMPEG_MAPPA = os.path.join(APP_MAPPA, "ffmpeg")
FFMPEG_EXE = os.path.join(FFMPEG_MAPPA, "ffmpeg.exe")

# ---- Naplózás fájlba (az exe mellé) ----

NAPLO_MAX_MERET = 5 * 1024 * 1024


def _naplo_alapmappa():
    """PyInstaller exe esetén az exe mappája, forrásból futtatva a szkripté."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


NAPLO_FAJL = os.path.join(_naplo_alapmappa(), "zeneletolto.log")
_naplo_zar = threading.Lock()


def fajlba_naplo(szoveg):
    """Minden naplósort kiír a fájlba. Ha az exe mellé nem lehet írni
    (pl. Program Files), átvált a LOCALAPPDATA mappára."""
    global NAPLO_FAJL
    ido = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # A PID nélkül olvashatatlan a napló, ha egyszerre több példány fut:
    # a sorok összekeverednek, és úgy tűnik, mintha egy példány töltene kettőt.
    sor = f"[{ido}][{os.getpid()}] {szoveg}\n"
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


def naplo_parancs(cimke, parancs):
    """Parancssor naplózása. A googlevideo URL-ek aláírt tokent tartalmaznak és
    több ezer karakteresek, ezért azokat rövidítjük."""
    reszek = []
    for r in parancs:
        if len(r) > 160 and r.startswith("http"):
            reszek.append(r[:160] + f"...[+{len(r) - 160} karakter]")
        else:
            reszek.append(r)
    fajlba_naplo(f"{cimke}: {' '.join(reszek)}")


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
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
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
            log_callback("yt-dlp naprakész.")
            # Ne próbálkozzon minden indításkor újra
            os.utime(YTDLP_EXE, None)
        else:
            log_callback("⚠️ A yt-dlp frissítése nem sikerült (a régi verzió marad).")
    except Exception as e:
        log_callback(f"⚠️ A yt-dlp frissítése nem sikerült: {e}")
        fajlba_naplo(traceback.format_exc())


# A szakaszos letöltéshez tartozó beállítások.
# A YouTube a szekvenciális GET-et erősen fojtja (~250 kB/s), a Range kéréseket
# viszont teljes sebességgel szolgálja ki. Az ffmpeg viszont nem küld Range
# kérést seekeléskor, hanem végigolvassa a fájlt ("soft-seeking to offset ... by
# draining ..."), ezért egy 3 órás pozícióig órákig tartana eljutnia. Emiatt a
# szakaszt magunk töltjük le: a fragmentált mp4 `sidx` indexéből kiszámoljuk a
# kért időtartomány byte-tartományát, azt darabolt Range kérésekkel leszedjük egy
# sparse fájlba, és abból vág az ffmpeg.
SZAKASZ_FEJLEC_MERET = 1 * 1024 * 1024   # ennyi byte-ból már kiolvasható a sidx
SZAKASZ_DARAB_MERET = 4 * 1024 * 1024    # egy Range kérés mérete
SZAKASZ_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


class UrlLejartHiba(Exception):
    """A googlevideo URL érvénytelenné vált (403/410) - újat kell kérni."""


def http_tartomany(url, kezd=None, veg=None, timeout=60, probalkozas=3):
    """HTTP Range kérés. `veg` bezárólag értendő, None esetén a fájl végéig.

    Az aláírt googlevideo URL-ek több GB-os letöltés közben érvénytelenné
    válhatnak - ilyenkor UrlLejartHiba jön, hogy a hívó frissíthesse az URL-t.
    A többi hálózati hiba átmeneti lehet, azt itt újrapróbáljuk.
    """
    utolso = None
    for kiserlet in range(probalkozas):
        keres = urllib.request.Request(url, headers={"User-Agent": SZAKASZ_UA})
        if kezd is not None:
            keres.add_header("Range", f"bytes={kezd}-{'' if veg is None else veg}")
        try:
            with urllib.request.urlopen(keres, timeout=timeout) as valasz:
                return valasz.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 410):
                raise UrlLejartHiba(f"HTTP {e.code}") from e
            utolso = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            utolso = e
        if kiserlet < probalkozas - 1:
            time.sleep(2 ** kiserlet)
    raise utolso


def mp4_sidx_olvasas(fejlec):
    """Fragmentált mp4 `sidx` indexének kiolvasása.

    Visszaad: {"timescale", "adat_kezd", "szegmensek": [(byte_meret, idotartam)]}
    vagy None, ha a fájlban nincs sidx (pl. webm, vagy nem fragmentált mp4).
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
            szegmensek = []
            for _ in range(darab):
                r1, tartam, _r3 = struct.unpack(">III", fejlec[p:p + 12])
                p += 12
                szegmensek.append((r1 & 0x7FFFFFFF, tartam))
            return {
                "timescale": timescale,
                "adat_kezd": o + meret + elso_offset,
                "szegmensek": szegmensek,
            }

        # A sidx a moov után, az első moof előtt áll: ha idáig eljutottunk, nincs
        if tipus in (b"moof", b"mdat"):
            return None
        o += meret
    return None


def sidx_byte_tartomany(index, kezd_mp, veg_mp):
    """Időtartomány -> (byte_kezd, byte_veg) a sidx alapján.

    A szegmenshatárok kulcsképkockán vannak, ezért a kezdetet lefelé, a véget
    felfelé kerekítjük egész szegmensre - így az ffmpeg pontosan tud vágni.
    """
    ts = index["timescale"]
    poz = index["adat_kezd"]
    ido = 0
    byte_kezd = None
    byte_veg = None
    for meret, tartam in index["szegmensek"]:
        szeg_kezd = ido / ts
        szeg_veg = (ido + tartam) / ts
        if byte_kezd is None and szeg_veg > kezd_mp:
            byte_kezd = poz
        if byte_kezd is not None:
            byte_veg = poz + meret
            if szeg_kezd >= veg_mp:
                break
        poz += meret
        ido += tartam
    if byte_kezd is None:
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


def ido_ertelmezes(szoveg):
    """'1:20:30' / '20:30' / '90' -> másodperc. None ha érvénytelen."""
    szoveg = (szoveg or "").strip().replace(".", ":")
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


def fajlnev_tisztitas(nev):
    """Windows-on tiltott karakterek cseréje."""
    nev = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nev or "video")
    nev = nev.strip(" .") or "video"
    return nev[:120]


def formatumok_lekerese(url, extra=None):
    """Lekéri az elérhető formátumokat yt-dlp -j segítségével."""
    parancs = [YTDLP_EXE, "-j", "--no-playlist"] + (extra or []) + [url]
    naplo_parancs("yt-dlp lekérdezés", parancs)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            parancs, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, env=env, timeout=60
        )
    except subprocess.TimeoutExpired:
        return None, "Időtúllépés: a yt-dlp nem válaszolt 60 másodpercen belül."
    if result.returncode != 0:
        return None, result.stderr
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return None, f"Érvénytelen válasz a yt-dlp-től: {e}"
    return info, None


def formatum_csoportositas(info):
    """Csoportosítja a formátumokat felbontás szerint, és visszaadja a legjobb videó+audió opciókat."""
    formats = info.get("formats", [])
    
    # Videó formátumok szűrése (van kép vagy van height) - beleértve kombinált formátumokat is
    video_fmts = []
    for f in formats:
        has_video = f.get("vcodec", "none") != "none" or f.get("height")
        if has_video and f.get("height"):
            video_fmts.append(f)
    
    # Legjobb audió keresése (külön audió stream, vagy kombinált formátumból)
    best_audio = None
    for f in formats:
        if f.get("acodec", "none") != "none":
            abr = f.get("abr") or f.get("tbr") or 0
            is_audio_only = f.get("vcodec", "none") == "none"
            best_abr = (best_audio.get("abr") or best_audio.get("tbr") or 0) if best_audio else 0
            best_is_audio_only = (best_audio.get("vcodec", "none") == "none") if best_audio else False
            # Előnyben részesítjük a külön audió streamet
            if best_audio is None or (is_audio_only and not best_is_audio_only) or (not (best_is_audio_only and not is_audio_only) and abr > best_abr):
                best_audio = f

    # Felbontás szerinti csoportosítás - minden felbontásból a legjobb
    felb_map = {}
    for f in video_fmts:
        h = f.get("height", 0)
        tbr = f.get("tbr") or 0
        if h not in felb_map or tbr > (felb_map[h].get("tbr") or 0):
            felb_map[h] = f

    # Rendezés felbontás szerint (növekvő)
    rendezett = sorted(felb_map.items(), key=lambda x: x[0])

    eredmeny = []
    for height, f in rendezett:
        w = f.get("width")
        h = f.get("height", "?")
        vcodec = f.get("vcodec") or None
        if vcodec and vcodec != "none" and "." in vcodec:
            vcodec = vcodec.split(".")[0]
        if not vcodec or vcodec == "none":
            vcodec = None
        fps = f.get("fps")
        if fps and isinstance(fps, float) and fps == int(fps):
            fps = int(fps)
        tbr = f.get("tbr")
        vbr = f.get("vbr")
        filesize = f.get("filesize") or f.get("filesize_approx")

        # Felbontás név
        if h == 720:
            nev = "720p HD"
        elif h == 1080:
            nev = "1080p Full HD"
        elif h == 1440:
            nev = "1440p 2K"
        elif h == 2160:
            nev = "2160p 4K"
        elif h == 4320:
            nev = "4320p 8K"
        else:
            nev = f"{h}p"

        # Leírás összeállítása - csak ami elérhető
        reszek = [nev]
        if w:
            reszek.append(f"{w}x{h}")
        if vcodec:
            reszek.append(vcodec)
        if fps:
            reszek.append(f"{fps}fps")
        if vbr:
            reszek.append(f"{vbr:.0f} kbps video")
        elif tbr:
            reszek.append(f"{tbr:.0f} kbps")

        # Méret
        if filesize:
            mb = filesize / (1024 * 1024)
            if mb >= 1024:
                reszek.append(f"~{mb/1024:.1f} GB")
            else:
                reszek.append(f"~{mb:.0f} MB")

        # Audió infó
        has_own_audio = f.get("acodec", "none") != "none"
        needs_separate_audio = not has_own_audio
        if needs_separate_audio and best_audio:
            ac = best_audio.get("acodec", "?")
            if ac and "." in ac:
                ac = ac.split(".")[0]
            abr = best_audio.get("abr") or best_audio.get("tbr") or 0
            reszek.append(f"+ {ac} {abr:.0f}kbps audio")
        elif has_own_audio:
            ac = f.get("acodec", "?")
            if ac and "." in ac:
                ac = ac.split(".")[0]
            abr = f.get("abr") or 0
            if abr:
                reszek.append(f"+ {ac} {abr:.0f}kbps audio")

        leiras = "  |  ".join(reszek)

        eredmeny.append({
            "leiras": leiras,
            "height": h,
            "format_id": f.get("format_id"),
            "best_audio_id": best_audio.get("format_id") if (needs_separate_audio and best_audio) else None,
        })

    return eredmeny


class ZeneLetolto(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Letöltő")
        self.geometry("780x730")
        self.resizable(True, True)
        self.minsize(680, 600)
        self.configure(bg="#1e1e2e")

        self.letoltes_mappa = os.path.join(os.path.expanduser("~"), "Downloads", "YTLetolto")
        self.fut = False
        self.letolt_fut = False
        self.formatumok = []
        self.process = None
        self._megszakitva = False
        self._destroyed = False
        self.video_hossz = None
        self.lekerdezett_url = None

        self._ui_felepites()
        self.protocol("WM_DELETE_WINDOW", self._kilep)
        threading.Thread(target=self._init_eszközök, daemon=True).start()

    def _kilep(self):
        self._destroyed = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except Exception:
                self.process.kill()
        self.destroy()

    def _ui_felepites(self):
        stilus = {"bg": "#1e1e2e", "fg": "#cdd6f4", "font": ("Segoe UI", 11)}
        cb_stilus = {"font": ("Segoe UI", 11), "bg": "#1e1e2e", "fg": "#cdd6f4",
                     "selectcolor": "#313244", "activebackground": "#1e1e2e",
                     "activeforeground": "#cdd6f4", "cursor": "hand2"}

        tk.Label(self, text="YouTube Letöltő", font=("Segoe UI", 16, "bold"),
                 bg="#1e1e2e", fg="#89b4fa").pack(pady=(14, 8))

        # URL mező + Lekérdezés gomb
        url_keret = tk.Frame(self, bg="#1e1e2e")
        url_keret.pack(fill="x", padx=20)
        tk.Label(url_keret, text="YouTube URL:", **stilus).pack(anchor="w")
        url_sor = tk.Frame(url_keret, bg="#1e1e2e")
        url_sor.pack(fill="x", pady=(2, 8))
        self.url_mezo = tk.Entry(url_sor, font=("Segoe UI", 12), bg="#313244", fg="#cdd6f4",
                                  insertbackground="#cdd6f4", relief="flat", bd=6)
        self.url_mezo.pack(side="left", fill="x", expand=True)
        self.url_mezo.bind("<Return>", lambda e: self._formatumok_lekerese())
        self.lekerdezes_gomb = tk.Button(url_sor, text="🔍 Lekérdezés", font=("Segoe UI", 11, "bold"),
                                          bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                                          relief="flat", bd=0, cursor="hand2",
                                          command=self._formatumok_lekerese)
        self.lekerdezes_gomb.pack(side="right", padx=(8, 0), ipady=4)

        # Mappa választó
        mappa_keret = tk.Frame(self, bg="#1e1e2e")
        mappa_keret.pack(fill="x", padx=20)
        tk.Label(mappa_keret, text="Mentés helye:", **stilus).pack(anchor="w")
        sor = tk.Frame(mappa_keret, bg="#1e1e2e")
        sor.pack(fill="x", pady=(2, 6))
        self.mappa_var = tk.StringVar(value=self.letoltes_mappa)
        tk.Entry(sor, textvariable=self.mappa_var, font=("Segoe UI", 10), bg="#313244",
                 fg="#cdd6f4", insertbackground="#cdd6f4", relief="flat", bd=6).pack(side="left", fill="x", expand=True)
        tk.Button(sor, text="📁", font=("Segoe UI", 12), bg="#45475a", fg="#cdd6f4",
                  relief="flat", command=self._mappa_valasztas, cursor="hand2").pack(side="right", padx=(6, 0))

        # Mód választó (Zene / Videó)
        mod_keret = tk.Frame(self, bg="#1e1e2e")
        mod_keret.pack(fill="x", padx=20, pady=(4, 0))
        self.mod_var = tk.StringVar(value="zene")
        mod_sor = tk.Frame(mod_keret, bg="#1e1e2e")
        mod_sor.pack(anchor="w")
        tk.Radiobutton(mod_sor, text="🎵 Zene (MP3)", variable=self.mod_var, value="zene",
                       command=self._mod_valtozott, **cb_stilus).pack(side="left", padx=(0, 16))
        tk.Radiobutton(mod_sor, text="🎬 Videó (MP4)", variable=self.mod_var, value="video",
                       command=self._mod_valtozott, **cb_stilus).pack(side="left", padx=(0, 16))
        tk.Radiobutton(mod_sor, text="🥽 VR 3D (SBS)", variable=self.mod_var, value="vr",
                       command=self._mod_valtozott, **cb_stilus).pack(side="left")

        # Playlist checkbox
        self.playlist_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Playlist letöltése", variable=self.playlist_var,
                       **cb_stilus).pack(anchor="w", padx=20)

        # Időintervallum (részlet letöltése)
        szakasz_keret = tk.Frame(self, bg="#1e1e2e")
        szakasz_keret.pack(fill="x", padx=20)
        self.szakasz_var = tk.BooleanVar(value=False)
        tk.Checkbutton(szakasz_keret, text="✂️ Csak egy szakasz letöltése",
                       variable=self.szakasz_var, command=self._szakasz_valtozott,
                       **cb_stilus).pack(anchor="w")

        szakasz_sor = tk.Frame(szakasz_keret, bg="#1e1e2e")
        szakasz_sor.pack(anchor="w", padx=(24, 0), pady=(0, 4))
        mezo_stilus = {"font": ("Consolas", 11), "bg": "#313244", "fg": "#cdd6f4",
                       "insertbackground": "#cdd6f4", "relief": "flat", "bd": 5,
                       "width": 9, "justify": "center",
                       "disabledbackground": "#26263a", "disabledforeground": "#585b70"}
        tk.Label(szakasz_sor, text="Ettől:", **stilus).pack(side="left")
        self.szakasz_kezd = tk.Entry(szakasz_sor, state="disabled", **mezo_stilus)
        self.szakasz_kezd.pack(side="left", padx=(6, 14))
        tk.Label(szakasz_sor, text="Eddig:", **stilus).pack(side="left")
        self.szakasz_veg = tk.Entry(szakasz_sor, state="disabled", **mezo_stilus)
        self.szakasz_veg.pack(side="left", padx=(6, 14))
        self.hossz_cimke = tk.Label(szakasz_sor, text="pl. 3:00:00 → 3:20:00",
                                    bg="#1e1e2e", fg="#6c7086", font=("Segoe UI", 10))
        self.hossz_cimke.pack(side="left")

        tk.Label(szakasz_keret,
                 text="Formátum: óra:perc:mp (3:00:00) · perc:mp (20:15) · másodperc (90)",
                 bg="#1e1e2e", fg="#6c7086", font=("Segoe UI", 9)).pack(anchor="w", padx=(24, 0))

        # Formátum választó keret (videó módban, lekérdezés után jelenik meg)
        self.fmt_keret = tk.Frame(self, bg="#1e1e2e")
        tk.Label(self.fmt_keret, text="Elérhető felbontások:", **stilus).pack(anchor="w")

        # Scrollable lista
        self.fmt_lista_keret = tk.Frame(self.fmt_keret, bg="#181825")
        self.fmt_lista_keret.pack(fill="both", expand=True, pady=(4, 0))

        self.fmt_canvas = tk.Canvas(self.fmt_lista_keret, bg="#181825", highlightthickness=0)
        self.fmt_scrollbar = tk.Scrollbar(self.fmt_lista_keret, orient="vertical", command=self.fmt_canvas.yview)
        self.fmt_belso = tk.Frame(self.fmt_canvas, bg="#181825")
        self.fmt_belso.bind("<Configure>", lambda e: self.fmt_canvas.configure(scrollregion=self.fmt_canvas.bbox("all")))
        self.fmt_canvas.create_window((0, 0), window=self.fmt_belso, anchor="nw")
        self.fmt_canvas.configure(yscrollcommand=self.fmt_scrollbar.set)
        self.fmt_canvas.pack(side="left", fill="both", expand=True)
        self.fmt_scrollbar.pack(side="right", fill="y")

        # Egérgörgő támogatás
        def _on_mousewheel(event):
            self.fmt_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.fmt_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.fmt_belso.bind("<MouseWheel>", _on_mousewheel)

        self.fmt_valasztas = tk.StringVar()

        # Letöltés gomb
        self.gomb = tk.Button(self, text="⬇  Letöltés", font=("Segoe UI", 13, "bold"),
                              bg="#89b4fa", fg="#1e1e2e", activebackground="#74c7ec",
                              relief="flat", bd=0, cursor="hand2", height=1,
                              command=self._letoltes_inditasa)
        self.gomb.pack(pady=8)

        # Napló
        naplo_fejlec = tk.Frame(self, bg="#1e1e2e")
        naplo_fejlec.pack(fill="x", padx=20)
        tk.Label(naplo_fejlec, text="Napló:", **stilus).pack(side="left")
        tk.Button(naplo_fejlec, text="📄 Naplófájl megnyitása", font=("Segoe UI", 9),
                  bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
                  relief="flat", bd=0, cursor="hand2",
                  command=self._naplo_megnyitas).pack(side="right", ipadx=6)

        self.naplo = tk.Text(self, height=6, font=("Consolas", 10), bg="#181825", fg="#a6adc8",
                             relief="flat", bd=8, state="disabled", wrap="word")
        self.naplo.pack(fill="both", expand=True, padx=20, pady=(2, 14))

    def _szakasz_valtozott(self):
        allapot = "normal" if self.szakasz_var.get() else "disabled"
        self.szakasz_kezd.config(state=allapot)
        self.szakasz_veg.config(state=allapot)
        if self.szakasz_var.get():
            # Ha üresek a mezők, töltsük ki a teljes hosszal
            if not self.szakasz_kezd.get().strip():
                self.szakasz_kezd.insert(0, "0:00")
            if not self.szakasz_veg.get().strip() and self.video_hossz:
                self.szakasz_veg.insert(0, ido_formazas(self.video_hossz))
            self.szakasz_kezd.focus_set()

    def _hossz_kiiras(self):
        if self.video_hossz:
            self.hossz_cimke.config(
                text=f"Videó hossza: {ido_formazas(self.video_hossz)}", fg="#a6e3a1")
        else:
            self.hossz_cimke.config(text="pl. 3:00:00 → 3:20:00", fg="#6c7086")

    def _hossz_frissites(self):
        """Lekérdezés után: hossz kiírása, és a végpont mező kitöltése ha üres."""
        self._hossz_kiiras()
        if self.szakasz_var.get() and self.video_hossz and not self.szakasz_veg.get().strip():
            self.szakasz_veg.insert(0, ido_formazas(self.video_hossz))

    def _mod_valtozott(self):
        if self.mod_var.get() == "video" and self.formatumok:
            self.fmt_keret.pack(fill="both", expand=True, padx=20, pady=(4, 0),
                                before=self.gomb)
        else:
            self.fmt_keret.pack_forget()

    def _log(self, szoveg, csak_fajlba=False):
        """A napló mindig fájlba is megy - a hibakereséshez ez a forrás."""
        fajlba_naplo(szoveg.strip("\n") if szoveg.strip() else szoveg)
        if csak_fajlba:
            return

        def _frissit():
            if self._destroyed:
                return
            self.naplo.config(state="normal")
            self.naplo.insert("end", szoveg + "\n")
            # Max 500 sor megtartása
            sorok = int(self.naplo.index('end-1c').split('.')[0])
            if sorok > 500:
                self.naplo.delete('1.0', f'{sorok - 500}.0')
            self.naplo.see("end")
            self.naplo.config(state="disabled")
        if self._destroyed:
            return
        try:
            self.after(0, _frissit)
        except RuntimeError:
            pass

    def _init_eszközök(self):
        try:
            self._log("Eszközök ellenőrzése...")
            eszközök_letöltése(self._log)
            ytdlp_frissites(self._log)
            self._log("Kész! Írd be az URL-t és nyomj Lekérdezést.")
            self._verziok_naplozasa()
        except Exception as e:
            self._log(f"HIBA az eszközök letöltésekor: {e}")
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

    def _naplo_megnyitas(self):
        if not os.path.isfile(NAPLO_FAJL):
            messagebox.showinfo("Napló", f"A naplófájl még nem jött létre.\n\n{NAPLO_FAJL}")
            return
        try:
            os.startfile(NAPLO_FAJL)
        except OSError as e:
            messagebox.showerror("Napló", f"Nem sikerült megnyitni:\n{NAPLO_FAJL}\n\n{e}")

    def _mappa_valasztas(self):
        mappa = filedialog.askdirectory(initialdir=self.mappa_var.get())
        if mappa:
            self.mappa_var.set(mappa)

    def _eszkozok_keszen(self):
        """A letöltéshez a yt-dlp és az ffmpeg is kell (merge, mp3, szakasz)."""
        if not os.path.isfile(YTDLP_EXE):
            self._log("⚠️ A yt-dlp még letöltődik, várj!")
            return False
        if not os.path.isfile(FFMPEG_EXE):
            self._log("⚠️ Az ffmpeg még letöltődik, várj!")
            return False
        return True

    def _url_ellenorzes(self, url):
        if not url:
            messagebox.showwarning("Figyelem", "Adj meg egy URL-t!")
            return False
        if not url.startswith("http://") and not url.startswith("https://"):
            messagebox.showwarning("Figyelem", "Adj meg egy érvényes URL-t!\nPélda: https://www.youtube.com/watch?v=XXXXX")
            return False
        return True

    def _formatumok_lekerese(self):
        url = self.url_mezo.get().strip()
        if not self._url_ellenorzes(url):
            return
        if not os.path.isfile(YTDLP_EXE):
            self._log("⚠️ Az eszközök még letöltődnek, várj!")
            return
        if self.fut:
            self._log("⚠️ Már folyamatban van egy művelet, várj!")
            return
        self.fut = True
        self.lekerdezes_gomb.config(state="disabled", text="⏳ Lekérdezés...")
        # Régi formátumok törlése azonnal
        self.formatumok = []
        self.video_hossz = None
        self.lekerdezett_url = None
        self._hossz_kiiras()
        self.fmt_keret.pack_forget()
        for w in self.fmt_belso.winfo_children():
            w.destroy()
        threading.Thread(target=self._formatumok_worker, args=(url, self.mod_var.get()), daemon=True).start()

    def _formatumok_worker(self, url, mod):
        try:
            self._log(f"\nFormátumok lekérdezése: {url}")
            info, hiba = formatumok_lekerese(url)
            if hiba or info is None:
                self._log(f"❌ Hiba: {hiba}")
                return

            cim = info.get("title", "?")
            hossz = info.get("duration")
            hossz_str = ""
            if hossz:
                self.video_hossz = int(hossz)
                hossz_str = f"  |  {ido_formazas(hossz)}"
            self._log(f"📹 {cim}{hossz_str}")
            self.lekerdezett_url = url
            try:
                if not self._destroyed:
                    self.after(0, self._hossz_frissites)
            except RuntimeError:
                pass

            if mod == "zene":
                # Régi formátumok törlése
                self.formatumok = []
                try:
                    if not self._destroyed:
                        self.after(0, lambda: self.fmt_keret.pack_forget())
                except RuntimeError:
                    pass
                # Audió infó kiírása
                formats = info.get("formats", [])
                audio_fmts = [f for f in formats if f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none"]
                if not audio_fmts:
                    # Kombinált formátumokból keressük az audiót
                    audio_fmts = [f for f in formats if f.get("acodec", "none") != "none"]
                if audio_fmts:
                    best = max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0)
                    ac = best.get("acodec", "?")
                    if ac and "." in ac:
                        ac = ac.split(".")[0]
                    abr = best.get("abr") or best.get("tbr") or 0
                    sr = best.get("asr", "?")
                    self._log(f"🎵 Forrás audió: {ac} | {abr:.0f} kbps | {sr} Hz")
                    self._log(f"🎵 MP3 kimenet: legjobb minőség (~245 kbps VBR)")
                self._log("✅ Kész a letöltésre! Nyomj Letöltést.")
                return

            if mod == "vr":
                # VR: legjobb minőség infó kiírása
                self.formatumok = []
                try:
                    if not self._destroyed:
                        self.after(0, lambda: self.fmt_keret.pack_forget())
                except RuntimeError:
                    pass
                formats = info.get("formats", [])
                video_fmts = [f for f in formats if (f.get("vcodec", "none") != "none" or f.get("height")) and f.get("height")]
                if video_fmts:
                    best_v = max(video_fmts, key=lambda f: (f.get("height", 0), f.get("tbr", 0) or 0))
                    h = best_v.get("height", "?")
                    w = best_v.get("width")
                    vcodec = best_v.get("vcodec")
                    if vcodec and vcodec != "none" and "." in vcodec:
                        vcodec = vcodec.split(".")[0]
                    elif not vcodec or vcodec == "none":
                        vcodec = None
                    fps = best_v.get("fps")
                    tbr = best_v.get("tbr") or best_v.get("vbr") or 0
                    filesize = best_v.get("filesize") or best_v.get("filesize_approx")
                    reszek = []
                    if w:
                        reszek.append(f"{w}x{h}")
                    else:
                        reszek.append(f"{h}p")
                    if vcodec:
                        reszek.append(vcodec)
                    if fps:
                        reszek.append(f"{int(fps) if isinstance(fps, float) and fps == int(fps) else fps}fps")
                    if tbr:
                        reszek.append(f"{tbr:.0f} kbps")
                    if filesize:
                        mb = filesize / (1024 * 1024)
                        reszek.append(f"~{mb/1024:.1f} GB" if mb >= 1024 else f"~{mb:.0f} MB")
                    self._log(f"🥽 Legjobb videó: {' | '.join(reszek)}")
                audio_fmts = [f for f in formats if f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none"]
                if not audio_fmts:
                    audio_fmts = [f for f in formats if f.get("acodec", "none") != "none"]
                if audio_fmts:
                    best_a = max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0)
                    ac = best_a.get("acodec", "?")
                    if ac and "." in ac:
                        ac = ac.split(".")[0]
                    abr = best_a.get("abr") or best_a.get("tbr") or 0
                    self._log(f"🥽 Legjobb audió: {ac} | {abr:.0f} kbps")
                self._log("✅ Kész a letöltésre! Nyomj Letöltést.")
                return

            self.formatumok = formatum_csoportositas(info)

            if not self.formatumok:
                self._log("❌ Nem találtam elérhető videó formátumokat.")
                return

            # Legjobb minőség kiírása
            legjobb = self.formatumok[-1]
            self._log(f"🎬 Legjobb: {legjobb['leiras']}")
            self._log(f"✅ {len(self.formatumok)} felbontás elérhető.")

            # UI frissítés a fő szálban
            try:
                if not self._destroyed:
                    self.after(0, self._formatumok_megjelenites)
            except RuntimeError:
                pass
        except Exception as e:
            self._log(f"❌ Hiba: {e}")
        finally:
            self.fut = False
            try:
                if not self._destroyed:
                    self.after(0, lambda: self.lekerdezes_gomb.config(state="normal", text="🔍 Lekérdezés"))
            except RuntimeError:
                pass

    def _formatumok_megjelenites(self):
        # Töröljük a régi opciókat
        for w in self.fmt_belso.winfo_children():
            w.destroy()

        # Utolsót (legmagasabb felbontás) jelöljük ki alapból
        if self.formatumok:
            self.fmt_valasztas.set(str(self.formatumok[-1]["height"]))

        self.fmt_canvas.yview_moveto(0)  # Scroll reset
        for fmt in reversed(self.formatumok):  # Legnagyobb felül
            rb = tk.Radiobutton(
                self.fmt_belso, text=fmt["leiras"],
                variable=self.fmt_valasztas, value=str(fmt["height"]),
                font=("Consolas", 10), bg="#181825", fg="#cdd6f4",
                selectcolor="#313244", activebackground="#181825",
                activeforeground="#cdd6f4", cursor="hand2", anchor="w",
                wraplength=680, justify="left"
            )
            rb.pack(fill="x", anchor="w", padx=4, pady=1)
            rb.bind("<MouseWheel>", lambda e: self.fmt_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        if self.mod_var.get() == "video":
            self.fmt_keret.pack(fill="both", expand=True, padx=20, pady=(4, 0),
                                before=self.gomb)

    def _megallitas(self):
        if not self.letolt_fut:
            return
        # A jelzőt akkor is be kell állítani, ha épp nincs futó folyamat: a saját
        # szakasz-letöltő Range kérések között ezt figyeli.
        self._megszakitva = True
        self._log("⛔ Letöltés megszakítva.")
        try:
            proc = self.process
            if proc and proc.poll() is None:
                proc.terminate()
        except OSError:
            pass

    def _szakasz_ellenorzes(self):
        """None = nincs szakasz, False = hibás megadás, egyébként (kezd_mp, veg_mp)."""
        if not self.szakasz_var.get():
            return None
        kezd_szoveg = self.szakasz_kezd.get().strip()
        veg_szoveg = self.szakasz_veg.get().strip()
        if not kezd_szoveg and not veg_szoveg:
            messagebox.showwarning("Figyelem", "Add meg a szakasz kezdetét és/vagy végét!\nPélda: 3:00:00 és 3:20:00")
            return False

        kezd = 0 if not kezd_szoveg else ido_ertelmezes(kezd_szoveg)
        if kezd is None:
            messagebox.showwarning("Figyelem", f"Érvénytelen kezdő időpont: {kezd_szoveg}\nHasználható formátumok: 3:20:15, 20:15, 15")
            return False

        if not veg_szoveg:
            veg = self.video_hossz
            if not veg:
                messagebox.showwarning("Figyelem", "Add meg a szakasz végét is, vagy nyomj előbb Lekérdezést!")
                return False
        else:
            veg = ido_ertelmezes(veg_szoveg)
            if veg is None:
                messagebox.showwarning("Figyelem", f"Érvénytelen befejező időpont: {veg_szoveg}\nHasználható formátumok: 3:20:15, 20:15, 15")
                return False

        if veg <= kezd:
            messagebox.showwarning("Figyelem", f"A vég ({ido_formazas(veg)}) nem lehet korábban a kezdetnél ({ido_formazas(kezd)})!")
            return False
        if self.video_hossz and kezd >= self.video_hossz:
            messagebox.showwarning("Figyelem", f"A kezdő időpont ({ido_formazas(kezd)}) túl van a videó végén ({ido_formazas(self.video_hossz)})!")
            return False
        if self.video_hossz and veg > self.video_hossz:
            self._log(f"⚠️ A megadott vég ({ido_formazas(veg)}) túllóg a videó hosszán, levágom {ido_formazas(self.video_hossz)}-ra.")
            veg = self.video_hossz
        return (kezd, veg)

    def _letoltes_inditasa(self):
        url = self.url_mezo.get().strip()
        if not self._url_ellenorzes(url):
            return
        if self.letolt_fut:
            if messagebox.askyesno("Megszakítás", "Már fut egy letöltés. Megszakítod?"):
                self._megallitas()
            return
        if self.fut:
            self._log("⚠️ Már folyamatban van egy lekérdezés, várj!")
            return

        if not self._eszkozok_keszen():
            return
        mod = self.mod_var.get()
        if mod == "video" and not self.formatumok:
            messagebox.showwarning("Figyelem", "Először nyomj Lekérdezést a felbontások letöltéséhez!")
            return
        # A format_id-k videónként egyediek: ha az URL a lekérdezés óta
        # megváltozott, a régi azonosítók rossz minőséget eredményeznének.
        if self.lekerdezett_url and url != self.lekerdezett_url:
            if mod == "video":
                messagebox.showwarning("Figyelem", "Az URL megváltozott a lekérdezés óta.\nNyomj újra Lekérdezést!")
                return
            self.video_hossz = None
            self.lekerdezett_url = None
            self._hossz_kiiras()
        if mod == "vr":
            self.formatumok = []
            self.fmt_keret.pack_forget()

        # Playlist figyelmeztetés
        if self.playlist_var.get() and "list=" not in url:
            if not messagebox.askyesno("Figyelem", "A 'Playlist letöltése' be van pipálva, de ez nem playlist link.\nBiztosan folytatod egyedi videóként?"):
                return
            self.playlist_var.set(False)
        elif not self.playlist_var.get() and "list=" in url:
            valasz = messagebox.askyesno("Figyelem", "Ez egy playlist link, de a 'Playlist letöltése' nincs bepipálva.\nLetöltsem az egész playlistet?")
            if valasz:
                self.playlist_var.set(True)

        szakasz = self._szakasz_ellenorzes()
        if szakasz is False:
            return
        if szakasz and self.playlist_var.get():
            if not messagebox.askyesno("Figyelem", "Szakasz letöltésénél a playlist nem támogatott.\nCsak a link szerinti egy videó szakasza töltődik le.\nFolytatod?"):
                return
            self.playlist_var.set(False)

        self.fut = True
        self.letolt_fut = True
        self.gomb.config(state="normal", text="⛔ Megszakítás", command=self._megallitas)
        self.lekerdezes_gomb.config(state="disabled")
        playlist = self.playlist_var.get()
        mappa = self.mappa_var.get()
        fmt_valasztas = self.fmt_valasztas.get()
        formatumok = list(self.formatumok)
        threading.Thread(target=self._letoltes,
                         args=(url, mod, playlist, mappa, fmt_valasztas, formatumok, szakasz),
                         daemon=True).start()

    def _letoltes(self, url, mod, playlist, mappa, fmt_valasztas, formatumok, szakasz=None):
        """Belépési pont: szakasz esetén a saját range-letöltő, egyébként yt-dlp."""
        self.process = None
        self._megszakitva = False
        try:
            os.makedirs(mappa, exist_ok=True)
            if szakasz:
                try:
                    if self._szakasz_letoltes(url, mod, mappa, fmt_valasztas, szakasz):
                        return
                    if self._megszakitva:
                        return
                    self._log("↩️ Visszaesés a yt-dlp beépített szakaszolására "
                              "(ez a YouTube fojtása miatt lassú lehet).")
                except Exception as e:
                    self._log(f"⚠️ A gyors szakaszletöltő hibája: {e}")
                    fajlba_naplo(traceback.format_exc())
                    self._log("↩️ Visszaesés a yt-dlp beépített szakaszolására.")
            self._letoltes_ytdlp(url, mod, playlist, mappa, fmt_valasztas, formatumok, szakasz)
        finally:
            self.process = None
            self.fut = False
            self.letolt_fut = False
            try:
                if not self._destroyed:
                    self.after(0, lambda: self.lekerdezes_gomb.config(state="normal"))
                    self.after(0, lambda: self.gomb.config(state="normal", text="⬇  Letöltés",
                                                           command=self._letoltes_inditasa))
            except RuntimeError:
                pass

    # ---- Saját szakasz-letöltő (Range kérésekkel, csak a kért tartomány) ----

    def _szakasz_formatum(self, mod, fmt_valasztas):
        """A szakaszhoz mp4/m4a kell, mert a byte-index a fragmentált mp4 sidx-e."""
        if mod == "zene":
            return "bestaudio[ext=m4a]"
        if mod == "vr":
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
        try:
            h = int(fmt_valasztas)
        except (ValueError, TypeError):
            h = 0
        if h > 0:
            return (f"bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]")
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]"

    def _boritokep_letoltes(self, info, cel):
        """Videó borítókép letöltése és jpg-vé alakítása. None, ha nem sikerült.

        A YouTube webp-et ad, amit az mp4 nem tud tárolni, ezért az ffmpeg-gel
        jpeg-re konvertáljuk. A kép hiánya nem hiba: a letöltés e nélkül is jó.
        """
        jeloltek = []
        if info.get("thumbnail"):
            jeloltek.append(info["thumbnail"])
        # Tartaléknak a legnagyobb felbontású a lista végéről visszafelé
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
                    fajlba_naplo(f"borítókép kész: {kep_url[:120]}")
                    return cel
            except Exception as e:
                fajlba_naplo(f"borítókép sikertelen ({kep_url[:80]}): {e}")
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
        info, hiba = formatumok_lekerese(url, ["-f", str(format_id)])
        if hiba or not info:
            fajlba_naplo(f"URL frissítés sikertelen ({format_id}): {hiba}")
            return None
        for jelolt in (info.get("requested_formats") or [info]):
            if jelolt.get("url"):
                return jelolt["url"]
        return None

    def _szakasz_stream(self, stream, kezd, veg, cel, cimke, forras_url=None):
        """Egy stream kért szakaszának letöltése sparse fájlba. True = sikerült."""
        stream_url = stream.get("url")
        if not stream_url:
            return False
        format_id = stream.get("format_id")

        fejlec = http_tartomany(stream_url, 0, SZAKASZ_FEJLEC_MERET - 1)
        index = mp4_sidx_olvasas(fejlec)
        if not index:
            self._log(f"⚠️ {cimke}: nincs sidx index a fájlban (nem fragmentált mp4).")
            return False

        fajlba_naplo(f"{cimke}: sidx timescale={index['timescale']} "
                     f"szegmensek={len(index['szegmensek'])} "
                     f"adat_kezd={index['adat_kezd']} url={stream_url[:120]}...")

        tartomany = sidx_byte_tartomany(index, kezd, veg)
        if not tartomany:
            self._log(f"⚠️ {cimke}: a kért időtartomány kívül esik a videón.")
            return False
        byte_kezd, byte_veg = tartomany
        fajlba_naplo(f"{cimke}: byte tartomány {byte_kezd}-{byte_veg} "
                     f"({byte_veg - byte_kezd} byte)")

        teljes = sum(m for m, _ in index["szegmensek"]) + index["adat_kezd"]
        kell = byte_veg - byte_kezd
        self._log(f"   {cimke}: {kell / 1024 / 1024:.0f} MB letöltése "
                  f"(a teljes {teljes / 1024 / 1024:.0f} MB helyett)")

        if not sparse_fajl_letrehozas(cel, teljes):
            self._log(f"⚠️ {cimke}: a célmappa nem támogatja a lyukas (sparse) fájlt, "
                      f"így {teljes / 1024 / 1024 / 1024:.1f} GB helyet foglalna. "
                      f"Válassz NTFS meghajtót.")
            return False

        with open(cel, "r+b") as f:
            # A fejléc (ftyp+moov+sidx) kell, hogy az ffmpeg értelmezni tudja
            f.seek(0)
            f.write(fejlec[:min(index["adat_kezd"], len(fejlec))])
            poz = byte_kezd
            kesz = 0
            utolso_szazalek = -10
            url_frissitve = 0
            while poz < byte_veg:
                if self._megszakitva:
                    return False
                darab_veg = min(poz + SZAKASZ_DARAB_MERET, byte_veg) - 1
                try:
                    adat = http_tartomany(stream_url, poz, darab_veg)
                except UrlLejartHiba as e:
                    # Az aláírt URL több GB letöltése közben lejárhat vagy
                    # tiltásra kerülhet; kérjünk újat és folytassuk onnan.
                    if url_frissitve >= 3 or not (forras_url and format_id):
                        raise
                    url_frissitve += 1
                    self._log(f"   {cimke}: a letöltési link érvénytelen lett ({e}), "
                              f"új link kérése... ({url_frissitve}/3)")
                    uj = self._stream_url_frissites(forras_url, format_id)
                    if not uj:
                        raise
                    stream_url = uj
                    continue
                if not adat:
                    raise RuntimeError(f"{cimke}: üres válasz a szervertől")
                f.seek(poz)
                f.write(adat)
                poz += len(adat)
                kesz += len(adat)
                szazalek = kesz * 100 // max(kell, 1)
                if szazalek >= utolso_szazalek + 10:
                    utolso_szazalek = szazalek
                    self._log(f"   {cimke}: {szazalek}%  "
                              f"({kesz / 1024 / 1024:.0f}/{kell / 1024 / 1024:.0f} MB)")
        return True

    def _szakasz_letoltes(self, url, mod, mappa, fmt_valasztas, szakasz):
        """True = kész. False = nem sikerült, jöhet a yt-dlp tartalék út."""
        kezd, veg = szakasz
        self._log(f"\n✂️ Szakasz letöltése: {ido_formazas(kezd)} – {ido_formazas(veg)} "
                  f"({ido_formazas(veg - kezd)})")
        self._log("   Csak a kért byte-tartomány töltődik le, nem a teljes videó.")

        fmt_spec = self._szakasz_formatum(mod, fmt_valasztas)
        fajlba_naplo(f"Szakasz mód={mod} mappa={mappa} kezd={kezd} veg={veg} "
                     f"formátum-kifejezés={fmt_spec}")
        sort = ["-S", "abr,asr"] if mod == "zene" else ["-S", "res,fps,hdr:12,tbr"]
        info, hiba = formatumok_lekerese(url, sort + ["-f", fmt_spec])
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
            kodek = s.get("vcodec") if s.get("vcodec", "none") != "none" else s.get("acodec")
            kodek = (kodek or "?").split(".")[0]
            if s.get("height"):
                self._log(f"   Formátum: {s.get('height')}p {kodek} "
                          f"{s.get('tbr') or 0:.0f} kbps")
            else:
                self._log(f"   Formátum: {kodek} {s.get('abr') or s.get('tbr') or 0:.0f} kbps")

        cim = fajlnev_tisztitas(info.get("title", "video"))
        cimke_ido = f"{ido_formazas(kezd)}_{ido_formazas(veg)}".replace(":", "-")
        alap = f"{cim}_3D_SBS" if mod == "vr" else cim
        alap = f"{alap}_[{cimke_ido}]"

        ideiglenes = []
        try:
            for i, stream in enumerate(streamek):
                van_video = stream.get("vcodec", "none") != "none"
                cimke = "videó" if van_video else "audió"
                kiterjesztes = stream.get("ext", "mp4")
                tmp = os.path.join(mappa, f".{alap}.{i}.{kiterjesztes}.tmp")
                ideiglenes.append(tmp)
                if not self._szakasz_stream(stream, kezd, veg, tmp, cimke,
                                            forras_url=url):
                    return False

            if self._megszakitva:
                return False

            media_fajlok = list(ideiglenes)
            boritokep = self._boritokep_letoltes(info, os.path.join(mappa, f".{alap}.jpg"))
            if boritokep:
                ideiglenes.append(boritokep)

            # Pontos vágás és összefűzés - újrakódolás nélkül (mp3-nál kódolás)
            if mod == "zene":
                cel = os.path.join(mappa, f"{alap}.mp3")
                args = ["-ss", str(kezd), "-to", str(veg), "-i", media_fajlok[0]]
                if boritokep:
                    args += ["-i", boritokep]
                args += ["-map", "0:a", "-c:a", "libmp3lame", "-q:a", "0"]
                if boritokep:
                    args += ["-map", "1:v", "-c:v", "copy", "-id3v2_version", "3",
                             "-metadata:s:v", "title=Album cover",
                             "-metadata:s:v", "comment=Cover (front)"]
            else:
                cel = os.path.join(mappa, f"{alap}.mp4")
                args = []
                for tmp in media_fajlok:
                    args += ["-ss", str(kezd), "-to", str(veg), "-i", tmp]
                if boritokep:
                    args += ["-i", boritokep]
                args += ["-c", "copy", "-movflags", "+faststart"]
                for i in range(len(media_fajlok)):
                    args += ["-map", f"{i}:0"]
                if boritokep:
                    # Csatolt képként fűzzük be: a .mp4 thumbnail kezelője
                    # ({9DBD2C50-...}) ezt olvassa ki, ettől lesz előnézet az
                    # Explorerben. Az index a VIDEÓ streamek között értendő,
                    # ezért a média-videók számát kell megadni, nem a bemenetét.
                    video_db = sum(1 for s in streamek
                                   if s.get("vcodec", "none") != "none")
                    args += ["-map", f"{len(media_fajlok)}:0",
                             f"-disposition:v:{video_db}", "attached_pic"]

            args += ["-metadata", f"title={info.get('title', '')}", cel]

            self._log("   Vágás és összefűzés...")
            if not self._ffmpeg_futtatas(args):
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

    def _ffmpeg_futtatas(self, args):
        parancs = [FFMPEG_EXE, "-y", "-hide_banner", "-loglevel", "error", "-stats"] + args
        naplo_parancs("ffmpeg parancs", parancs)
        self.process = subprocess.Popen(
            parancs, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        utolso = ""
        for sor in self.process.stdout:
            sor = sor.strip()
            if sor:
                utolso = sor
                self._log(f"[ffmpeg] {sor}", csak_fajlba=True)
        self.process.wait()
        fajlba_naplo(f"ffmpeg kilépési kód: {self.process.returncode}")
        if self.process.returncode != 0:
            if not self._megszakitva:
                self._log(f"❌ ffmpeg hiba: {utolso}")
            return False
        return True

    def _letoltes_ytdlp(self, url, mod, playlist, mappa, fmt_valasztas, formatumok, szakasz=None):
        video_mod = mod == "video"

        parancs = [YTDLP_EXE, "--ffmpeg-location", FFMPEG_MAPPA,
                  "--windows-filenames", "--progress"]

        vr_mod = mod == "vr"

        # Minőségi sorrend: felbontás > fps > HDR > bitráta. A yt-dlp ezek után
        # még alkalmazza a saját alapértelmezett rendezését is.
        video_sort = ["-S", "res,fps,hdr:12,tbr"]

        if vr_mod:
            parancs += video_sort
            parancs += ["-f", "bestvideo*+bestaudio/best",
                        "--merge-output-format", "mp4",
                        "--embed-metadata"]
        elif video_mod:
            # Kiválasztott felbontás alapján format string
            try:
                valasztott_h = int(fmt_valasztas)
            except (ValueError, TypeError):
                valasztott_h = 0
            # Keresés a lekérdezett formátumok között
            valasztott = None
            for fmt in formatumok:
                if fmt["height"] == valasztott_h:
                    valasztott = fmt
                    break

            # Ha a konkrét format_id nem elérhető (pl. lejárt URL), essünk vissza
            # ugyanarra a felbontásra, hogy ne egy alacsonyabb minőség jöjjön.
            if valasztott_h > 0:
                tartalek = f"/bestvideo[height={valasztott_h}]+bestaudio/bestvideo[height<={valasztott_h}]+bestaudio/best[height<={valasztott_h}]/best"
            else:
                tartalek = "/bestvideo*+bestaudio/best"

            if valasztott and valasztott.get("best_audio_id"):
                fmt_str = f"{valasztott['format_id']}+{valasztott['best_audio_id']}{tartalek}"
            elif valasztott:
                fmt_str = f"{valasztott['format_id']}{tartalek}"
            else:
                fmt_str = tartalek.lstrip("/")

            parancs += video_sort
            parancs += ["-f", fmt_str, "--merge-output-format", "mp4",
                        "--embed-thumbnail", "--embed-metadata"]
        else:
            parancs += ["-S", "abr,asr,acodec:opus",
                        "-f", "bestaudio/best",
                        "-x", "--audio-format", "mp3", "--audio-quality", "0",
                        "--embed-thumbnail", "--embed-metadata"]

        # Fájlnév sablon
        if vr_mod:
            nev_sablon = "%(title)s_3D_SBS.%(ext)s"
        else:
            nev_sablon = "%(title)s.%(ext)s"

        # Időszakasz: csak a megadott tartományt tölti le (ffmpeg downloader),
        # nem a teljes videót vágja ki utólag.
        if szakasz:
            kezd, veg = szakasz
            parancs += ["--download-sections", f"*{kezd}-{veg}"]
            # A szakasz a fájlnévbe is bekerül, hogy a különböző részletek
            # ne írják felül egymást
            cimke = f"{ido_formazas(kezd)}_{ido_formazas(veg)}".replace(":", "-")
            nev, kit = nev_sablon.rsplit(".", 1)
            nev_sablon = f"{nev}_[{cimke}].{kit}"
            self._log(f"✂️ Csak a(z) {ido_formazas(kezd)} – {ido_formazas(veg)} "
                      f"szakasz töltődik le ({ido_formazas(veg - kezd)} hossz).")

        if playlist:
            parancs += ["--yes-playlist", "-o", os.path.join(mappa, "%(playlist_title)s", "%(playlist_index)03d - " + nev_sablon)]
        else:
            parancs += ["--no-playlist", "-o", os.path.join(mappa, nev_sablon)]

        parancs += ["--newline", url]

        self._log(f"\nLetöltés: {url}")
        naplo_parancs("yt-dlp parancs", parancs)
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            self.process = subprocess.Popen(
                parancs,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=env
            )
            mar_letoltve = False
            hiba_volt = False
            for sor in self.process.stdout:
                sor = sor.strip()
                if sor:
                    if "has already been downloaded" in sor:
                        mar_letoltve = True
                    if "ERROR:" in sor:
                        hiba_volt = True
                    self._log(sor)
            self.process.wait()

            if self._megszakitva:
                pass  # már logolva
            elif mar_letoltve and not hiba_volt:
                self._log("⚠️ Ez a fájl már le van töltve, nem kell újra letölteni!")
            elif self.process.returncode == 0:
                self._log("✅ Sikeres letöltés!")
            elif hiba_volt:
                self._log("❌ Hiba történt. Ha 'Access is denied' hibát kapsz, zárd be a lejátszót ami használja a fájlt.")
            else:
                self._log("❌ Hiba történt a letöltés során.")
        except Exception as e:
            self._log(f"❌ Hiba: {e}")
            fajlba_naplo(traceback.format_exc())


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
        app = ZeneLetolto()
        app.mainloop()
    except Exception:
        fajlba_naplo("VÉGZETES HIBA:\n" + traceback.format_exc())
        raise
    finally:
        fajlba_naplo("Kilépés.")
