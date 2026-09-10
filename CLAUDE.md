# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Windows desktop GUI (Tkinter) that wraps `yt-dlp` for downloading audio/video from YouTube and other sites. The application lives in [zeneletolto.py](zeneletolto.py) — stdlib only, no packages, no tests. Supporting files: [YouTubeLetolto.spec](YouTubeLetolto.spec) (PyInstaller), [bump_build.py](bump_build.py) (release build-number bump), [version_info.txt](version_info.txt) (Windows file-version resource), `icon_youtube_letolto.ico` (a single 512 px entry — deliberately *not* a multi-size icon: Windows' own downscaling of the large bitmap looks sharper on this artwork than pre-baked 16/32 px entries did).

**Language convention:** all identifiers, comments, log messages, and UI strings are in Hungarian (e.g. `letoltes_mappa`, `formatumok_lekerese`, `_megallitas`). Keep new code in Hungarian to match.

## Running / building

```powershell
python zeneletolto.py    # run (stdlib only, no pip install needed)
.\build.bat              # build dist\YouTube Letolto.exe from the .spec
.\rebuild_verzioszam_novelessel_es_github_pushal.bat   # bump BUILD_SZAM, build, git push, gh release
```

`bump_build.py` raises `BUILD_SZAM` in `zeneletolto.py` and mirrors it into `version_info.txt`. It takes `max(local, published-on-GitHub) + 1` — a release built from a stale checkout would otherwise move the published build number *backwards*.

Both build scripts move `dist\zeneletolto.log` aside before wiping `dist\` and put it back afterwards, and refuse to build while `YouTube Letolto.exe` is running (PyInstaller otherwise dies with a confusing `PermissionError` on the locked exe). **Never let a build delete that log** — it is often the only record of a user-reported failure.

No test suite, linter, or CI exists. `python -m pyflakes zeneletolto.py` catches the cheap mistakes. Verify real changes by running the GUI. For headless tests, load the module with `importlib`, then subclass **`Lap`** (the download engine lives there, not on the window) **without calling its `__init__`** — that would build widgets — and supply the handful of attributes the worker methods touch:

```python
class Teszt(z.Lap):
    def __init__(self):
        self._megszakitva = False
        self._lezarva = False
        self.process = None
        self.azonosito = 0
        self.naplo_cimke = "[teszt]"
        self._haladas_utolso = 0.0
    def _log(self, s, csak_fajlba=False): print(s, flush=True)   # note the kwarg
    def _fajlnaplo(self, s): pass
    def _haladas_fojtott(self, szazalek, szoveg): print(szazalek, szoveg)
    def _haladas_beallit(self, szazalek=None, szoveg=None, stilus=None): pass
```

Then call `_szakasz_letoltes(terv)` or `_letoltes_ytdlp(terv)` directly with a `terv` dict (`url`, `mod`, `mappa`, `playlist`, `szakasz`, `formatum`, `friss`, `konteneres`). For UI tests, build a real `Alkalmazas()` and drive `app.uj_lap()` / `lap._elemzes_inditasa()` from `after()` callbacks. Run the interpreter with `PYTHONUTF8=1` — the Hungarian log lines and emoji raise `UnicodeEncodeError` on the default Windows console codepage (cp1250).

To screenshot the running GUI without stealing focus from the user, use `PrintWindow(hwnd, hdc, 2)` (PW_RENDERFULLCONTENT), not `CopyFromScreen` — the latter captures whatever window happens to be on top.

**Verifying downloaded output** (the GUI reporting success is not evidence the file is good):

```powershell
ffprobe -v error -show_entries format=duration -show_entries stream=codec_name,codec_type,width,height,r_frame_rate:stream_disposition=attached_pic -of default=noprint_wrappers=1 FILE
ffmpeg -v error -xerror -ss 600 -t 15 -i FILE -f null -    # decode a slice; non-zero exit = corruption
```

A correct section download has a duration matching the requested range to ~0.01 s, **a real video stream** (this is the one that silently goes missing — see the webm note below), an `attached_pic` mjpeg stream, and decodes cleanly at the start, middle and end.

## Architecture

**Self-bootstrapping binaries.** The app has no bundled dependencies: on startup a daemon thread runs `eszközök_letöltése()`, which downloads `yt-dlp.exe` and extracts `ffmpeg.exe`/`ffprobe.exe` from a BtbN release zip into `%LOCALAPPDATA%\ZeneLetolto\`. Both are written to a `.tmp` path and moved only on success, so an interrupted download can't leave a truncated exe that the next run mistakes for a finished one. `YTDLP_EXE`/`FFMPEG_MAPPA` module constants point at this directory; anything invoking the tools must go through them, and `_eszkozok_keszen()` must gate downloads because the bootstrap may still be running — it checks **both** binaries, since merging, mp3 conversion and section cutting all need ffmpeg.

`ytdlp_frissites()` then runs `yt-dlp -U` if the exe's mtime is older than `YTDLP_FRISSITES_NAPOK` (7). This is not optional polish: YouTube changes often enough that a months-old yt-dlp silently stops resolving formats. When already current it touches the mtime so the check doesn't repeat every launch.

### Format selection — three traps that all bit us

`formatum_csoportositas(info)` reduces `info["formats"]` to one row per quality tier. Three things it must keep doing:

1. **A quality tier is not a pixel height.** A 21:9 ultrawide video's best rendition is 3440×1440, and YouTube calls that **2160p (4K)** — its `format_note` says so. Grouping by `height` labelled it "1440p 2K" and users reported the 4K as missing. `minoseg_szint()` reads `format_note` first; only when that's absent does it fall back to geometry (portrait → width, ultrawide → `width * 9/16`), snapped to the nearest standard rung by `_szintre_illesztes()` so a 1935 estimate becomes 2160 rather than showing "1935p".

2. **Prefer `https` (DASH) over `m3u8` (HLS).** YouTube exposes the same stream twice; the HLS variant's `tbr` is inflated (itag 628 claims 30101 kbps and ~43 GiB for what itag 315 serves as 13076 kbps and 18.7 GiB — the HLS URL's own `sgovp` parameter names itag 315 as the source). Sorting by bitrate therefore always picked HLS, which is slower, has **no byte index** (so section downloads fell back to the slow path), and displayed a fake size. `protokoll_rangsor()` ranks protocol above bitrate everywhere: in the grouping, in `-S ...,proto,tbr`, and via `[protocol^=http]` in every `-f` chain.

3. **Storyboards are not video formats.** YouTube's `sb0`–`sb3` entries are mhtml image grids with a `height` (27, 45, 90, 180) and `vcodec: none`. A `has_video = vcodec != "none" or height` test let all four through, so the picker listed 12 "resolutions" for a video with 8. `valodi_video_formatum()` rejects `mhtml`, `vcodec in (none, images)`, and `format_note == storyboard`.

Grouping runs in two passes: first one winner per real rendition `(width, height, fps)` — this is what collapses the https/HLS duplicate pair, and prefers a video-only stream over a muxed one (muxed carries worse audio) — then one winner per quality tier. `legjobb_hang()` prefers audio-only over combined, **non-DRC over DRC** (the `-drc` tracks are dynamic-range-compressed and were being picked by bitrate ties), and m4a/AAC for mp4 output.

**Two-phase flow: query then download.**
1. `formatumok_lekerese(url, extra=None)` runs `yt-dlp -j` (90 s timeout) and returns `(info, hiba)`, where `hiba` has already been through `ytdlp_hiba_forditas()` — raw yt-dlp stderr is not shown to users. `extra` injects flags; the section downloader reuses it to pass `-S`/`-f` and get back the resolved `requested_formats` with direct stream URLs.
2. `_letoltes(terv)` is a thin dispatcher: routes to `_szakasz_letoltes()` when a time range is set (falling back to `_letoltes_ytdlp()` if that returns `False` for any reason other than cancellation), and owns the single `finally` that clears `fut`/`letolt_fut` and restores the buttons. `_letoltes_ytdlp()` deliberately does *not* reset UI state — don't reintroduce a second `finally` there.

If video mode starts with no fresh format list (`terv["friss"]` false), the worker calls `_formatum_biztositas()` and re-queries on the spot rather than sending the user back to press a button — the old code refused to start and demanded a re-query.

**Three modes** (`self.mod`): `zene` → `-x --audio-format mp3`; `video` and `vr` → identical downloads, the only difference being the `_3D_SBS` filename suffix. VR mode does *no* stereoscopic conversion — it only labels the file; the source is assumed to already be side-by-side. All three modes fill the same `Treeview` (`_lista_feltoltes()` switches its columns and contents): video/VR get the resolution tiers, `zene` gets `mp3_valasztekok()` — the MP3 output bitrates (128 / 192 / V0 / 256 / 320, best last so the top row is selected by default). The *source* audio track is never a user choice: it is always the best available, and the list shows it (`forras`) so the tradeoff is visible — above ~130 kbps Opus the MP3 bitrate adds bytes, not quality.

**Containers never mean re-encoding.** `--merge-output-format` and the section path's `-c copy` only change the box around a bit-identical stream; the program never re-encodes video (no `--recode-video` anywhere). This is worth stating explicitly because YouTube serves DASH — video and audio always arrive as *separate* files (VP9 in webm, AV1/H.264 in mp4, AAC in m4a, Opus in webm) and always have to be merged, so "downloading an mp4" is never what actually happens on the wire. `_gyari_konteneres()` reports the source's own container (webm video → `mkv`, otherwise `mp4`) and `_konteneres_feloldas()` applies the user's choice on top: the default `auto` takes the factory container, `mp4`/`mkv` override it. The dropdown's first entry is relabelled live to name the resolved container (`Gyári formátum – MKV`) by `_konteneres_felirat_frissites()`, so "which one is the untouched original" is never a guess; it is driven from `_minoseg_osszefoglalo()`, which a `<<TreeviewSelect>>` binding also fires, because picking a lower tier can change the source container (2160p VP9/webm vs 1080p H.264/mp4). MP4 is what gives Explorer thumbnails; MKV is VP9/AV1's native container and safer with picky players. In MKV the cover art is an **attachment** (`-attach` + `mimetype`/`filename` metadata), not an `attached_pic` stream, so the section path branches on the container.

**Quality policy.** Every mode passes an explicit `-S` sort (`res,fps,hdr:12,proto,tbr` for video, `abr,asr,acodec:opus,proto` for audio). `_video_formatum_kifejezes()` builds a `/`-separated `-f` chain starting with the exact `format_id+hang_id` and degrading through same-height fallbacks, so an expired format id lands on the same resolution rather than a lower one. Downloads also pass `--concurrent-fragments 4` and generous retry counts.

### Section downloads use a custom downloader — read this before touching it

`--download-sections` alone is unusable on YouTube, and the reason is non-obvious:

- YouTube throttles sequential GETs on googlevideo URLs to ~250 kB/s but serves `Range` requests at ~8 MB/s (measured 17–28 MB/s with 4 parallel requests).
- yt-dlp forces the **ffmpeg** downloader for `--download-sections`, and ffmpeg does not issue a Range request when seeking these URLs. It logs `Soft-seeking to offset N by draining M remaining byte(s)` and reads the file from byte 0 at the throttled rate. Reaching a 3-hour offset in an 8 GB file takes hours.
- Verified as *not* fixable via `-seekable 1`, `-multiple_requests`, `-noaccurate_seek`, `-http_seekable`, `--downloader native`, `--extractor-args youtube:formats=dashy`, browser cookies, or other player clients.

So `_szakasz_letoltes()` does the ranged fetch itself, for **both container families**:

- **mp4** (`mp4_sidx_olvasas`): YouTube's DASH mp4 streams are fragmented and carry a `sidx` box right after `moov` mapping subsegment durations to byte sizes.
- **webm** (`webm_cues_olvasas`): YouTube's VP9 renditions above 1080p are frequently webm-only (the 4K ultrawide case has no mp4 at 2160p at all), so mp4-only support silently degraded 4K requests to 1080p. Matroska carries the same information in `Cues`, which YouTube places in the init range before the first Cluster. `_ebml_vint`/`_ebml_elemek` are a minimal EBML reader; `CueTime` is in `TimestampScale` units (ns), hence `ido * timescale / 1e9`.

Both parsers return the same normalized index — `{"tipus", "adat_kezd", "meret", "pontok": [(time, byte_offset)]}` — which `index_byte_tartomany()` turns into a byte range, rounded outward to whole subsegments (they start on keyframes). The bytes are pulled in 4 MB `Range` chunks, **`SZAKASZ_PARHUZAM` (4) at a time**, into a **sparse** file (`fsutil sparse setflag`, so a 4-hour video costs KB of disk, not GB) sized to the full stream length — keeping the original byte offsets valid so ffmpeg can parse and seek it as an ordinary local file. ffmpeg then cuts with `-c copy` (lossless) and muxes video+audio.

**The webm sparse-file trap.** An mp4's `moov` fully describes the track, so ffmpeg needs no packets to know the codec. Matroska's `Tracks` does not carry the pixel format, so ffmpeg reads packets from the start of the file to find it — hits the sparse hole, logs `0x00 at pos N invalid as first byte of an EBML number`, and then **creates a duplicate, parameterless second stream** (`Could not find codec parameters for stream 1 ... unspecified pixel format`). The mux then exits 0 having silently dropped the video, leaving an "audio + cover art" mp4. `webm_elolap_tartomany()` therefore also fetches the first cluster (~6 MB, rounded up to a cue boundary) so the file parses from byte 0; seeking still jumps straight into the requested range via Cues. If you ever see a section download produce a file with no video stream, this is the mechanism.

Consequences to keep in mind:
- If neither index is present (non-fragmented mp4, exotic site), `_szakasz_letoltes()` returns `False` and `_letoltes()` falls back to plain `--download-sections`, which is fine on sites that don't throttle.
- Playlists are not supported in section mode; `_letoltes_inditasa()` unchecks the playlist box after confirming.
- `_megallitas()` must set `_megszakitva` unconditionally — during the ranged fetch there is no subprocess to terminate, and every chunk worker polls that flag.
- Signed googlevideo URLs go stale during multi-GB fetches. `http_tartomany()` raises `UrlLejartHiba` on 403/410 (and retries other network errors with backoff); the chunk workers then re-resolve a fresh URL **under a lock** (so four threads don't each launch their own yt-dlp) and resume, up to 3 times. Observed in the wild at a 42 GB offset.
- `sparse_fajl_letrehozas()` returns `False` when the target filesystem won't hold a sparse file (exFAT/FAT32); the caller must bail rather than materialise a 100 GB zero-filled temp file.

Measured: 30 s taken from 2:06:00 of a 3.5-hour 4K (3440×1440 VP9) video = 64 MB fetched, 7 s wall clock, byte offset 12.4 GB. In production use it has served byte offsets past 172 GB without trouble.

**Thumbnails matter and are easy to lose.** `.mp4` has the `{9DBD2C50-62AD-11D0-B806-00C04FD706EC}` thumbnail provider registered, which extracts the *embedded cover art* rather than decoding a frame — so an mp4 without cover art gets no Explorer preview at all (`IShellItemImageFactory::GetImage` returns `0x8004B200`), and an 8K AV1 file would not be decodable for a frame grab anyway. The yt-dlp path gets this from `--embed-thumbnail`; the section path must do it itself via `_boritokep_letoltes()` (fetch `info["thumbnail"]`, transcode the WebP to JPEG with `-frames:v 1 -update 1` since mp4 can't hold WebP) plus `-disposition:v:N attached_pic`. `N` indexes **video streams, not inputs** — with video+audio inputs the cover is `v:1`, so the code counts video streams in `streamek` rather than reusing the input index. mp3 output takes the same cover via `-id3v2_version 3`. A missing cover is logged but never fails the download.

The section path bypasses `_letoltes_ytdlp()` entirely, so anything that path gets from yt-dlp flags — thumbnail, metadata, mp3 encoding — has to be reimplemented in the ffmpeg mux. That is exactly how cover art went missing for a user's first six downloads. When adding a yt-dlp flag to `_letoltes_ytdlp()`, check whether `_szakasz_letoltes()` needs the equivalent.

**Time strings** are parsed by `ido_ertelmezes()` (`H:MM:SS`, `MM:SS`, or bare seconds; `.` and `,` accepted as separators) and rendered by `ido_formazas()`. `_szakasz_ellenorzes()` returns a tri-state — `None` (feature off), `False` (invalid, already reported to the user), or `(kezd_mp, veg_mp)` — so callers must test `is False` rather than falsiness, since a range starting at 0 is a valid truthy-ambiguous tuple.

Section output filenames embed the range (`Cím_[3-00-00_3-20-00].mp4`, via `fajlnev_tisztitas()`) so different excerpts of one video neither overwrite each other nor trip yt-dlp's "already downloaded" detection.

## Tabs: `Alkalmazas` owns the window, `Lap` owns a download

The window is a thin shell. **`Alkalmazas(tk.Tk)`** holds only what is shared: the ttk styles, the icon, the tool bootstrap thread, the header status line, the tab strip, and the footer build number. **`Lap(tk.Frame)`** is one complete download session — its own URL field, format list, section slider, folder, progress bar, log widget, worker thread, `process` and `_megszakitva` flag. Several `Lap`s run at once, independently; `FeluletSegedek` carries the `_cimke`/`_kartya` helpers both classes use.

Things this split makes load-bearing:

- **Every per-download value must live on the `Lap`, never on the window.** A stray `self.<something>` on `Alkalmazas` would silently be shared between concurrent downloads.
- **`_fo_szalon()` on a Lap checks `self._lezarva or self.alk._destroyed`.** A closed tab's worker thread keeps running for a moment; without the first flag it would touch destroyed widgets.
- **File-log lines carry `[lap N]` next to `[pid]`.** One process now runs several downloads, so the `[pid]` marker alone no longer separates them. `Lap._fajlnaplo()` adds the label; module-level helpers take an optional `lap=` argument (`fajlba_naplo`, `naplo_parancs`, `formatumok_lekerese`) so the yt-dlp argv lines are attributable too.
- **Temp filenames include the tab id** (`.l2.` in `_szakasz_letoltes`, `elonezet_2.png` for the preview). Two tabs downloading the *same* video and range would otherwise write into each other's sparse temp files.
- **`_letoltes_vege()` sets `lap_allapot`**, which drives the tab chip: `✓` (green) when finished, `%` while downloading, `◌` while analysing, `✕` on error. That is how a finished download is visible from another tab.
- **A tab's *displayed* name comes from its position, its *identity* from `azonosito`.** `ful_felirat()` uses `alk.lapok.index(self)` ("Új lap", "Új lap 2", …) so closing tabs renumbers the rest — otherwise a single open tab ends up labelled "Új lap 17" after some churn. `azonosito` stays monotonic because the log label and the temp filenames key off it, and reusing a number could collide with a tab that is still writing.
- The `+` button is re-created inside `_lapcsik_ujraepites()` at grid column `len(lapok)` — i.e. directly after the rightmost tab, browser-style — with the filler column after it.
- `_kilep()` asks before killing tabs that are still downloading, then calls `lezaras()` on each.

`LAP_MAX` is 8. Each tab's ranged fetch already opens `SZAKASZ_PARHUZAM` (4) connections, so eight tabs mean 32 parallel requests — more tabs would fight each other for bandwidth rather than go faster.

## Self-update

On startup (1.5 s after the window is up) `frissites_kereses()` asks the GitHub releases API for the latest release. The **contract with the release script** is the tag format: `legujabb_kiadas()` accepts only `re.fullmatch(r"build-(\d+)")` and reads the `.exe` asset's `browser_download_url` from it. If `rebuild_verzioszam_novelessel_es_github_pushal.bat` ever changes its tag naming, the updater silently stops finding anything — it deliberately fails *closed* (no update offered) rather than guessing a number out of an unrecognised tag.

The check always runs; only the popup is conditional. `frissites_ertesites: false` in the settings suppresses `FrissitesAblak`, but the footer still gets a green `● Új verzió: build N` label that reopens the dialog on click — so dismissing is never a dead end. The dialog's "Ne jelenjen meg többé" checkbox is honoured **only on the "Később" path** (`_bezar(mentes=True)`); pressing "Telepítés" ignores it, because the program is about to be replaced anyway.

Replacing a running exe is the part with the traps:

- **Windows cannot overwrite a running exe**, so `frissites_telepites()` writes a small `.bat` that polls `tasklist` until our PID is gone, then `move`s the downloaded file over `sys.executable`, `start`s it and deletes itself.
- **Write that batch file with `newline=""`.** The script contains explicit `\r\n`, and text mode would translate those to `\r\r\n`. cmd then fails to match the `:varakozas` label, `goto` dies, and the whole script exits silently having replaced nothing — with `@echo off` and no console there is no error anywhere. This cost a debugging round; the symptom is "the update ran, nothing happened, the .bat is still there".
- **Launch it with `CREATE_NO_WINDOW` only, not `| DETACHED_PROCESS`.** Detached means no console at all, which makes the `tasklist`/`find`/`ping` pipeline unreliable. A plain child process already survives our exit.
- **Verify the download before trusting it**: `frissites_letoltes()` rejects anything that does not start with `MZ` or is under 1 MB, so a captive-portal HTML page or a truncated transfer can never become the user's exe.
- Running from source (`sys.frozen` false) there is nothing to swap: the button opens the releases page instead, and the checkbox is not saved on that path.

`Alkalmazas.beallitasok_mentese(**valtozasok)` **merges** into the shared `self.beallitas` before writing. The old per-Lap save wrote a fixed three-key dict, which would have wiped `frissites_ertesites` the next time anyone changed a folder.

## User interface

Custom-themed Tkinter: `ttk` (clam) for the things tk lacks — `Treeview`, `Progressbar`, `Scrollbar` — and plain `tk` widgets elsewhere, because only those accept arbitrary colours on Windows. `Gomb` is a flat hover-effect button whose `configure()` override mutes the background when `state=disabled` (an accent-coloured disabled button reads as clickable). `Mezo` wraps an `Entry` in a 1 px frame that turns accent-coloured on focus. `TartomanySav` is a hand-drawn two-handle range slider on a `Canvas` (Tk has no such widget); it is kept in two-way sync with the two time `Mezo`s through the `_szakasz_frissul` re-entry guard.

**Layout rules that exist because they broke.**
- The window is a 4-row grid (header, tab strip, tab container `weight=1`, footer); each `Lap` is a 3-row grid inside it (body `weight=4, minsize=240`, action bar, log `weight=1, minsize=124`). Those row minsizes plus `self.minsize(840, 600)` are what keep the log and the progress bar on screen — in an earlier version the log panel vanished entirely when the window was not maximised.
- The quality list's row `minsize` must stay small enough (150) that the grid can shrink *it* when space runs short. When it could not, the rows **below** it (the download folder) were pushed out of the window instead — which is what the tab strip's extra 44 px first exposed.
- A `ttk.Treeview` only redistributes column widths on `<Configure>`. Hiding a tab and showing it again fires no such event, so `stretch` alone leaves the columns bunched at the left; `_fa_szelesseg_igazitas()` computes the name column's width explicitly and is called on tab switch, after every list refill, and from a `<Configure>` binding (guarded by a 2 px threshold so it cannot re-trigger itself).
- `tk.Frame`'s `padx`/`pady` take a **single** distance, unlike pack/grid's, which accept a tuple. `pady=(10, 14)` on a Frame raises `TclError: bad screen distance "10 14"`.
- Prefer `↓` (U+2193) over `⬇` (U+2B07) in button labels: Segoe UI has no glyph for the latter and renders a tofu box.
- A `ttk.Combobox`'s field is styleable, but its drop-down is a plain Tk `Listbox` reachable only through `option_add("*TCombobox*Listbox.…")` — without those four lines the list opens white-on-white in a dark UI.
- `_mod_valtas()` must call `_lista_feltoltes()` **before** `_minoseg_osszefoglalo()`: the summary reads the tree's selection, and on a mode switch the old selection index points into the *previous* mode's list (this showed "MP4" for a webm 4K source).
- The program has **no version number**, only `BUILD_SZAM`. It lives in a footer row (bottom-left); the window title is just `PROGRAM_NEV`, and the GitHub release is tagged `build-N`. The one place a numeric version survives is `version_info.txt`'s `filevers`/`prodvers`, because Windows requires a four-part tuple there — the build goes in the third slot and the user-visible strings read `build N`.

**Gating.** `_felulet_allapot(elemezve)` greys out everything except the URL field until an analysis succeeds — before that there is nothing meaningful to configure. `_url_valtozott()` debounces 900 ms and analyses automatically, so the user never has to remember a separate query step.

**Progress.** `haladas_ertelmezes()` parses yt-dlp's `[download] 42.3% of ~ 1.2GiB at 5.4MiB/s ETA 03:21` lines (yt-dlp is run with `--newline --progress-delta 0.3`); `_ytdlp_sor()` routes those to the bar and the raw line to the log file only, maps postprocessor tags (`[Merger]`, `[ExtractAudio]`, …) to Hungarian status text, and tracks `Destination:` lines so the completion message names the *final* file (`.mp3`), not the intermediate (`.webm`). In video mode the two streams split the bar 0–50 % / 50–100 %. The ffmpeg mux reports real progress via `-progress pipe:1 -nostats` and `out_time_us`. `_haladas_fojtott()` caps updates at ~8/s so the event loop isn't flooded.

## Cross-cutting invariants

**Threading contract.** Tkinter is not thread-safe, so all worker threads (`_init_eszkozok`, `_elemzes_worker`, `_letoltes`) touch widgets only via `self._fo_szalon(...)`, which wraps `after(0, ...)` in an `if not self._destroyed` check *and* a `try/except RuntimeError`, because the interpreter can be torn down between the check and the call. `_kilep()` sets `_destroyed` before killing the subprocess and destroying the window. Worker threads receive their inputs in the `terv` dict rather than reading `self.*` widgets, so a mid-download UI change can't corrupt the running job.

**Concurrency guards.** `self.fut` blocks any second operation; `self.letolt_fut` marks a download specifically, which turns the download button into a cancel button. `_megszakitva` distinguishes user cancellation from a genuine failure when interpreting the exit code — and is also the *only* cancellation signal the ranged fetch has.

**Cancellation kills the process tree.** `_folyamat_leallitas()` uses `taskkill /F /T /PID` before falling back to `terminate()`: yt-dlp spawns ffmpeg as a child, and a plain `terminate()` left that child downloading in the background.

**Subprocess conventions.** Every `yt-dlp` invocation uses `creationflags=subprocess.CREATE_NO_WINDOW` (no console flash) and an env with `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`. Streaming output is read as **bytes** and decoded by `sor_dekodolas()` (utf-8, then the ANSI codepage) — yt-dlp sometimes emits console-codepage bytes, which turned accented titles into `Marvel?s` in the log.

**Logging.** `_log()` writes every line to `zeneletolto.log` next to the exe (`sys.executable` when frozen, else the script dir; falls back to `APP_MAPPA` if that path is read-only) *and* to the GUI widget — pass `csak_fajlba=True` for file-only lines such as raw ffmpeg output and yt-dlp internals. The file is the debugging surface: it also records yt-dlp/ffmpeg versions, every subprocess argv (`naplo_parancs()` truncates signed googlevideo URLs), index/byte-range details, and full tracebacks via `sys.excepthook` + `threading.excepthook` (a windowed PyInstaller build has no console — the `bad screen distance` crash above was diagnosed purely from this file). New files get a UTF-8 BOM so Notepad renders the accents; it rotates to `.1` past 5 MB. Lines carry `[pid]` because users run several instances at once and all of them append to the same file.

**Settings** (`beallitasok.json` in `APP_MAPPA`) persist the download folder and mode; failures to read or write are non-fatal by design.
