# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Windows desktop GUI (Tkinter) that wraps `yt-dlp` for downloading audio/video from YouTube and other sites. The entire application lives in [zeneletolto.py](zeneletolto.py) — there are no packages, dependencies, or tests.

**Language convention:** all identifiers, comments, log messages, and UI strings are in Hungarian (e.g. `letoltes_mappa`, `formatumok_lekerese`, `_megallitas`). Keep new code in Hungarian to match.

## Running / building

```powershell
python zeneletolto.py    # run (stdlib only, no pip install needed)
.\build.bat              # build dist\YouTube Letolto.exe (installs PyInstaller if missing,
                         # cleans build/ and the .spec afterwards)
```

`build.bat` has two guards that exist because both failures actually happened: it refuses to build while `YouTube Letolto.exe` is running (PyInstaller otherwise dies with a confusing `PermissionError` on the locked exe) and offers to close it, and it moves `dist\zeneletolto.log` aside before wiping `dist\` and puts it back afterwards. **Never let a build delete that log** — it is often the only record of a user-reported failure, and once destroyed the bug report can't be reconstructed.

No test suite, linter, or CI exists. Verify changes by running the GUI. For headless smoke tests, load the module with `importlib`, stub `eszközök_letöltése` to a no-op, and either monkeypatch `subprocess.Popen` to capture the argv `_letoltes_ytdlp()` builds or call `_szakasz_letoltes()` directly against a real URL:

```python
z.eszközök_letöltése = lambda cb: None
app._log = lambda s, csak_fajlba=False: print(s, flush=True)   # note the kwarg
```

Run the interpreter with `PYTHONUTF8=1` — the Hungarian log lines and emoji raise `UnicodeEncodeError` on the default Windows console codepage (cp1250).

**Verifying downloaded output** (the GUI reporting success is not evidence the file is good):

```powershell
ffprobe -v error -show_entries format=duration -show_entries stream=codec_name,codec_type,width,height,r_frame_rate:stream_disposition=attached_pic -of default=noprint_wrappers=1 FILE
ffmpeg -v error -xerror -ss 600 -t 15 -i FILE -f null -    # decode a slice; non-zero exit = corruption
```

A correct section download has a duration matching the requested range to ~0.01 s, an `attached_pic` mjpeg stream, and decodes cleanly at the start, middle and end.

## Architecture

**Self-bootstrapping binaries.** The app has no bundled dependencies: on startup a daemon thread runs `eszközök_letöltése()`, which downloads `yt-dlp.exe` and extracts `ffmpeg.exe`/`ffprobe.exe` from a BtbN release zip into `%LOCALAPPDATA%\ZeneLetolto\`. Both are written to a `.tmp` path and moved only on success, so an interrupted download or extraction can't leave a truncated exe that the next run mistakes for a finished one. `YTDLP_EXE`/`FFMPEG_MAPPA` module constants point at this directory; anything invoking the tools must go through them, and `_eszkozok_keszen()` must gate downloads because the bootstrap may still be running — it checks **both** binaries, since merging, mp3 conversion and section cutting all need ffmpeg.

`ytdlp_frissites()` then runs `yt-dlp -U` if the exe's mtime is older than `YTDLP_FRISSITES_NAPOK` (7). This is not optional polish: YouTube changes often enough that a months-old yt-dlp silently stops resolving formats. When already current it touches the mtime so the check doesn't repeat every launch.

**Two-phase flow: query then download.**
1. `formatumok_lekerese(url, extra=None)` runs `yt-dlp -j` (60s timeout) and returns `(info, hiba)`. `extra` injects flags — the section downloader reuses it to pass `-S`/`-f` and get back the resolved `requested_formats` with direct stream URLs.
2. `formatum_csoportositas(info)` reduces `info["formats"]` to one entry per `height` (highest `tbr` wins), picks a single `best_audio` (preferring audio-only streams over combined ones — relevant for Twitch/other sites that only expose muxed formats), and builds the `{leiras, height, format_id, best_audio_id}` dicts the radio buttons render. `best_audio_id` is `None` when the video format already carries audio.
3. `_letoltes()` is a thin dispatcher: it resets `process`/`_megszakitva`, routes to `_szakasz_letoltes()` when a time range is set (falling back to `_letoltes_ytdlp()` if that returns `False` for any reason other than cancellation), and owns the single `finally` that clears `fut`/`letolt_fut` and restores the buttons. `_letoltes_ytdlp()` builds the actual `yt-dlp` argv and deliberately does *not* reset UI state — don't reintroduce a second `finally` there.

**Three modes** (`self.mod_var`): `zene` → `-x --audio-format mp3 --audio-quality 0`; `video` → shows the resolution picker; `vr` → `bestvideo*+bestaudio/best` with a `_3D_SBS` filename suffix. Note that VR mode does *no* stereoscopic conversion — it only downloads at max quality and labels the file; the source is assumed to already be side-by-side. `zene` and `vr` clear `self.formatumok` and hide `fmt_keret`.

**Quality policy.** Every mode passes an explicit `-S` sort (`res,fps,hdr:12,tbr` for video, `abr,asr,acodec:opus` for audio) so the highest-quality stream wins; yt-dlp appends its own default sort after these. Video mode's `-f` is the exact `format_id+best_audio_id` from the query, followed by a `/`-separated fallback chain that pins the *same* height (`bestvideo[height=N]+bestaudio/...`) so an expired format id degrades to the same resolution rather than to a lower one.

**Section downloads use a custom downloader — read this before touching it.** `--download-sections` alone is unusable on YouTube, and the reason is non-obvious:

- YouTube throttles sequential GETs on googlevideo URLs to ~250 kB/s but serves `Range` requests at ~8 MB/s.
- yt-dlp forces the **ffmpeg** downloader for `--download-sections`, and ffmpeg does not issue a Range request when seeking these URLs. It logs `Soft-seeking to offset N by draining M remaining byte(s)` and reads the file from byte 0 at the throttled rate. Reaching a 3-hour offset in an 8 GB file takes hours.
- Verified as *not* fixable via `-seekable 1`, `-multiple_requests`, `-noaccurate_seek`, `-http_seekable`, `--downloader native`, `--extractor-args youtube:formats=dashy`, browser cookies, or other player clients (`web`/`tv`/`android` need PO tokens and yield no URL in this environment).

So `_szakasz_letoltes()` does the ranged fetch itself. YouTube's DASH mp4 streams are fragmented and carry a `sidx` box right after `moov`, which maps subsegment durations to byte sizes. `mp4_sidx_olvasas()` parses it from the first 1 MB, `sidx_byte_tartomany()` converts the time range to a byte range (rounded outward to whole subsegments, which start on keyframes), and the bytes are pulled in 4 MB `Range` chunks into a **sparse** file (`fsutil sparse setflag`, so a 4-hour video costs KB of disk, not GB) sized to the full stream length — keeping the original byte offsets valid so ffmpeg can parse and seek it as an ordinary local mp4. ffmpeg then cuts with `-c copy` (lossless) and muxes video+audio.

**Thumbnails matter and are easy to lose.** `.mp4` has the `{9DBD2C50-62AD-11D0-B806-00C04FD706EC}` thumbnail provider registered, which extracts the *embedded cover art* rather than decoding a frame — so an mp4 without cover art gets no Explorer preview at all (`IShellItemImageFactory::GetImage` returns `0x8004B200`), and an 8K AV1 file would not be decodable for a frame grab anyway. The yt-dlp path gets this from `--embed-thumbnail`; the section path must do it itself via `_boritokep_letoltes()` (fetch `info["thumbnail"]`, transcode the WebP to JPEG with `-frames:v 1 -update 1` since mp4 can't hold WebP) plus `-disposition:v:N attached_pic`. `N` indexes **video streams, not inputs** — with video+audio inputs the cover is `v:1`, so the code counts video streams in `streamek` rather than reusing the input index. mp3 output takes the same cover via `-id3v2_version 3`. A missing cover is logged but never fails the download.

Consequences to keep in mind:
- Section mode is restricted to `[ext=mp4]`/`[ext=m4a]` because `sidx` is mp4-only; a better-quality webm/VP9 rendition is skipped. Supporting it would mean parsing Matroska Cues.
- If `sidx` is absent (non-YouTube site, non-fragmented mp4), `_szakasz_letoltes()` returns `False` and `_letoltes()` falls back to plain `--download-sections`, which is fine on sites that don't throttle.
- Playlists are not supported in section mode; `_letoltes_inditasa()` unchecks the playlist box after confirming.
- `_megallitas()` must set `_megszakitva` unconditionally — during the ranged fetch there is no subprocess to terminate, and the chunk loop polls that flag.
- Signed googlevideo URLs go stale during multi-GB fetches. `http_tartomany()` raises `UrlLejartHiba` on 403/410 (and retries other network errors with backoff); the chunk loop then re-resolves a fresh URL for the same `format_id` via `_stream_url_frissites()` and resumes at the current offset, up to 3 times. Observed in the wild at a 42 GB offset.
- `sparse_fajl_letrehozas()` returns `False` when the target filesystem won't hold a sparse file (exFAT/FAT32); the caller must bail rather than materialise a 100 GB zero-filled temp file.

Measured: 3 minutes taken from 3:00:00 of a 4-hour video = 5 MB fetched, 5.3 s wall clock. In production use it has served byte offsets past 172 GB without trouble.

The section path bypasses `_letoltes_ytdlp()` entirely, so anything that path gets from yt-dlp flags — thumbnail, metadata, mp3 encoding — has to be reimplemented in the ffmpeg mux. That is exactly how cover art went missing for a user's first six downloads. When adding a yt-dlp flag to `_letoltes_ytdlp()`, check whether `_szakasz_letoltes()` needs the equivalent.

**Query-state invalidation.** `format_id`s are per-video, so `self.lekerdezett_url` records the URL the current `self.formatumok`/`self.video_hossz` came from. If the URL field changed since the query, video mode refuses to start and asks for a re-query.

Time strings are parsed by `ido_ertelmezes()` (`H:MM:SS`, `MM:SS`, or bare seconds; `.` is accepted as a separator) and rendered by `ido_formazas()`. `_szakasz_ellenorzes()` returns a tri-state — `None` (feature off), `False` (invalid, already reported to the user), or `(kezd_mp, veg_mp)` — so callers must test `is False` rather than falsiness, since a range starting at 0 is a valid truthy-ambiguous tuple.

Section output filenames embed the range (`Cím_[3-00-00_3-20-00].mp4`, via `fajlnev_tisztitas()`) so different excerpts of one video neither overwrite each other nor trip yt-dlp's "already downloaded" detection.

**Threading contract.** Tkinter is not thread-safe, so all worker threads (`_init_eszközök`, `_formatumok_worker`, `_letoltes`) touch widgets only via `self.after(0, ...)`. Every such call is guarded by `if not self._destroyed` and wrapped in `try/except RuntimeError`, because the interpreter can be torn down between the check and the call. `_kilep()` sets `_destroyed` before terminating the subprocess and destroying the window. Worker threads receive their inputs as arguments (url, mode, folder, format selection) rather than reading `self.*` widgets, so a mid-download UI change can't corrupt the running job.

**Concurrency guards.** `self.fut` blocks any second operation; `self.letolt_fut` marks a download specifically, which turns the download button into a cancel button (`_megallitas`). `_megszakitva` distinguishes user cancellation from a genuine failure when interpreting the exit code — and is also the *only* cancellation signal the ranged fetch has, so `_megallitas()` sets it before (and independently of) terminating any subprocess.

**Subprocess conventions.** Every `yt-dlp` invocation uses `creationflags=subprocess.CREATE_NO_WINDOW` (no console flash), `encoding="utf-8", errors="replace"`, and an env with `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` — required for non-ASCII video titles on Windows. Downloads stream with `--newline` and are read line-by-line into the log, scanning for `"has already been downloaded"` and `"ERROR:"` since yt-dlp's return code alone doesn't distinguish those cases.

**Logging.** `_log()` writes every line to `zeneletolto.log` next to the exe (`sys.executable` when frozen, else the script dir; falls back to `APP_MAPPA` if that path is read-only) *and* to the GUI widget — pass `csak_fajlba=True` for file-only lines such as raw ffmpeg output. The file is the debugging surface: it also records yt-dlp/ffmpeg versions, every subprocess argv (`naplo_parancs()` truncates signed googlevideo URLs), sidx/byte-range details, and full tracebacks via `sys.excepthook` + `threading.excepthook` (a windowed PyInstaller build has no console). New files get a UTF-8 BOM so Notepad renders the accents; it rotates to `.1` past 5 MB. Lines carry `[pid]` because users run several instances at once and all of them append to the same file — without it, two concurrent downloads look like one instance doing something impossible.

**Log widget** is `state="disabled"` at rest and trimmed to the last 500 lines on each append.
