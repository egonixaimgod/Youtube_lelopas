# YouTube Letöltő

Windows asztali alkalmazás YouTube (és sok más oldal) videóinak és zenéinek letöltéséhez.
A `yt-dlp`-t és az `ffmpeg`-et első indításkor magától letölti, semmit nem kell külön telepíteni.

## Funkciók

- **🎵 Zene (MP3)** – legjobb elérhető hangsáv, MP3-ba konvertálva, borítóképpel és metaadatokkal
- **🎬 Videó (MP4)** – az összes elérhető minőség táblázatban (felbontás, FPS, kodek, méret),
  a legjobb előre kijelölve
- **🥽 VR 3D (SBS)** – maximális minőség, `_3D_SBS` névvel (a forrásnak már side-by-side-nak kell lennie)
- **✂️ Részlet letöltése csúszkával** – csak egy időintervallum, pl. egy 4 órás videóból a
  `3:00:00`–`3:20:00` rész. Nem a teljes videót tölti le és vágja ki: kiszámolja a szakaszhoz
  tartozó byte-tartományt, és párhuzamos Range kérésekkel tényleg csak azt szedi le.
  Egy 3,5 órás 4K videóból 30 másodperc kivágása ~64 MB letöltés, 7 másodperc.
- **Lejátszási lista** támogatás (részlet módban nem)
- **Naplózás** – minden művelet az exe melletti `zeneletolto.log` fájlba kerül

## Használat

1. Illeszd be a linket (**Beillesztés** gomb vagy Ctrl+V). A program magától elemzi:
   kiírja a címet, az előnézeti képet, a hosszt és az összes elérhető minőséget.
   Elemzés előtt a többi vezérlő szürke – nincs mit beállítani rajtuk.
2. Válassz módot (zene / videó / VR) és a listából minőséget.
3. Ha csak egy részlet kell, pipáld be a **Csak egy részlet letöltése** opciót, és húzd a
   csúszka két fogantyúját – vagy írd be az időpontokat a mezőkbe (`3:00:00`, `20:15`, `90`).
4. **↓ Letöltés**. A haladás sávon látszik a százalék, a sebesség és a hátralévő idő.

### A minőségekről

A YouTube minőségi címkéje nem mindig a pixelmagasság: egy 21:9-es ultrawide videó
3440×1440-es változata a YouTube-nál **2160p (4K)**. A program a YouTube saját címkéjét
használja, és mindig a `https` (DASH) streamet választja a HLS változat helyett – ugyanaz a
tartalom, de a HLS-nek hamisan magas a bitrátája, nincs byte-indexe, és lassabban is jön.

## Hibakeresés

Minden művelet naplózódik az exe melletti `zeneletolto.log` fájlba: a használt yt-dlp/ffmpeg
verzió, a kiadott parancsok, a letöltött byte-tartományok és a teljes hibaüzenetek.
A programból a **Naplófájl** gombbal éred el.

## Futtatás forrásból

```powershell
python zeneletolto.py
```

Csak a Python beépített moduljait használja, nincs `pip install`.

## Exe készítése

```powershell
.\build.bat                                        # csak build -> dist\YouTube Letolto.exe
.\rebuild_verzioszam_novelessel_es_github_pushal.bat   # build szám +1, build, GitHub push + release
```

A PyInstallert szükség esetén magától telepíti. Az ikon újragenerálása (ha módosítod):

```powershell
python ikon_keszites.py
```
