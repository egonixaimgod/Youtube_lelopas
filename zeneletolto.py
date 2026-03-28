import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import os
import json
import urllib.request
import zipfile
import shutil

APP_MAPPA = os.path.join(os.getenv("LOCALAPPDATA", "."), "ZeneLetolto")
YTDLP_EXE = os.path.join(APP_MAPPA, "yt-dlp.exe")
FFMPEG_MAPPA = os.path.join(APP_MAPPA, "ffmpeg")
FFMPEG_EXE = os.path.join(FFMPEG_MAPPA, "ffmpeg.exe")


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
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.namelist():
                filename = os.path.basename(member)
                if filename in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(member) as src, open(os.path.join(FFMPEG_MAPPA, filename), "wb") as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(zip_path)
        log_callback("ffmpeg kész.")

    return True


def formatumok_lekerese(url):
    """Lekéri az elérhető formátumokat yt-dlp -j segítségével."""
    parancs = [YTDLP_EXE, "-j", "--no-playlist", url]
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
        self.geometry("750x650")
        self.resizable(True, True)
        self.minsize(650, 500)
        self.configure(bg="#1e1e2e")

        self.letoltes_mappa = os.path.join(os.path.expanduser("~"), "Downloads", "YTLetolto")
        self.fut = False
        self.letolt_fut = False
        self.formatumok = []
        self.process = None
        self._megszakitva = False
        self._destroyed = False

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
        self.naplo = tk.Text(self, height=6, font=("Consolas", 10), bg="#181825", fg="#a6adc8",
                             relief="flat", bd=8, state="disabled", wrap="word")
        self.naplo.pack(fill="both", expand=True, padx=20, pady=(0, 14))

    def _mod_valtozott(self):
        if self.mod_var.get() == "video" and self.formatumok:
            self.fmt_keret.pack(fill="both", expand=True, padx=20, pady=(4, 0),
                                before=self.gomb)
        else:
            self.fmt_keret.pack_forget()

    def _log(self, szoveg):
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
            self._log("Kész! Írd be az URL-t és nyomj Lekérdezést.")
        except Exception as e:
            self._log(f"HIBA az eszközök letöltésekor: {e}")

    def _mappa_valasztas(self):
        mappa = filedialog.askdirectory(initialdir=self.mappa_var.get())
        if mappa:
            self.mappa_var.set(mappa)

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
                perc, mp = divmod(int(hossz), 60)
                ora, perc = divmod(perc, 60)
                if ora:
                    hossz_str = f"  |  {ora}:{perc:02d}:{mp:02d}"
                else:
                    hossz_str = f"  |  {perc}:{mp:02d}"
            self._log(f"📹 {cim}{hossz_str}")

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
        try:
            proc = self.process
            if proc and proc.poll() is None:
                self._megszakitva = True
                proc.terminate()
                self._log("⛔ Letöltés megszakítva.")
        except OSError:
            pass

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

        if not os.path.isfile(YTDLP_EXE):
            self._log("⚠️ Az eszközök még letöltődnek, várj!")
            return
        mod = self.mod_var.get()
        if mod == "video" and not self.formatumok:
            messagebox.showwarning("Figyelem", "Először nyomj Lekérdezést a felbontások letöltéséhez!")
            return
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

        self.fut = True
        self.letolt_fut = True
        self.gomb.config(state="normal", text="⛔ Megszakítás", command=self._megallitas)
        self.lekerdezes_gomb.config(state="disabled")
        playlist = self.playlist_var.get()
        mappa = self.mappa_var.get()
        fmt_valasztas = self.fmt_valasztas.get()
        formatumok = list(self.formatumok)
        threading.Thread(target=self._letoltes, args=(url, mod, playlist, mappa, fmt_valasztas, formatumok), daemon=True).start()

    def _letoltes(self, url, mod, playlist, mappa, fmt_valasztas, formatumok):
        os.makedirs(mappa, exist_ok=True)
        video_mod = mod == "video"

        parancs = [YTDLP_EXE, "--ffmpeg-location", FFMPEG_MAPPA,
                  "--windows-filenames", "--progress"]

        vr_mod = mod == "vr"

        if vr_mod:
            parancs += ["-f", "bestvideo+bestaudio/best",
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

            if valasztott and valasztott.get("best_audio_id"):
                fmt_str = f"{valasztott['format_id']}+{valasztott['best_audio_id']}"
            elif valasztott:
                fmt_str = valasztott["format_id"]
            else:
                if valasztott_h > 0:
                    fmt_str = f"bestvideo[height<={valasztott_h}]+bestaudio/best"
                else:
                    fmt_str = "bestvideo+bestaudio/best"

            parancs += ["-f", fmt_str, "--merge-output-format", "mp4",
                        "--embed-thumbnail", "--embed-metadata"]
        else:
            parancs += ["-x", "--audio-format", "mp3", "--audio-quality", "0",
                        "--embed-thumbnail", "--embed-metadata"]

        # Fájlnév sablon
        if vr_mod:
            nev_sablon = "%(title)s_3D_SBS.%(ext)s"
        else:
            nev_sablon = "%(title)s.%(ext)s"

        if playlist:
            parancs += ["--yes-playlist", "-o", os.path.join(mappa, "%(playlist_title)s", "%(playlist_index)03d - " + nev_sablon)]
        else:
            parancs += ["--no-playlist", "-o", os.path.join(mappa, nev_sablon)]

        parancs += ["--newline", url]

        self._log(f"\nLetöltés: {url}")
        self.process = None
        self._megszakitva = False
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
        finally:
            self.process = None
            self.fut = False
            self.letolt_fut = False
            try:
                if not self._destroyed:
                    self.after(0, lambda: self.lekerdezes_gomb.config(state="normal"))
                    self.after(0, lambda: self.gomb.config(state="normal", text="⬇  Letöltés", command=self._letoltes_inditasa))
            except RuntimeError:
                pass


if __name__ == "__main__":
    app = ZeneLetolto()
    app.mainloop()
