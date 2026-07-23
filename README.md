# YouTube Letöltő

Windows asztali alkalmazás YouTube (és sok más oldal) videóinak és zenéinek letöltéséhez.
A `yt-dlp`-t és az `ffmpeg`-et első indításkor magától letölti, semmit nem kell külön telepíteni.

## Funkciók

- **🎵 Zene (MP3)** – legjobb elérhető audió, MP3-ba konvertálva, borítóképpel és metaadatokkal
- **🎬 Videó (MP4)** – lekérdezés után kiválaszthatod a felbontást (a legjobb van előre kijelölve)
- **🥽 VR 3D (SBS)** – maximális minőség, `_3D_SBS` névvel (a forrásnak már side-by-side-nak kell lennie)
- **✂️ Szakasz letöltése** – csak egy időintervallum, pl. egy 4 órás videóból a `3:00:00`–`3:20:00` rész.
  Nem a teljes videót tölti le és vágja ki: kiszámolja a szakaszhoz tartozó byte-tartományt,
  és tényleg csak azt szedi le. Egy 4 órás videóból 3 perc kivágása ~5 MB letöltés, pár másodperc.
- **Playlist** támogatás (szakasz módban nem)
- **Naplózás** – minden művelet az exe melletti `zeneletolto.log` fájlba kerül

## Használat

1. Illeszd be a linket, nyomj **🔍 Lekérdezés**-t – kiírja a címet, a hosszt és az elérhető minőségeket
2. Válassz módot (zene / videó / VR) és felbontást
3. Ha csak egy részlet kell, pipáld be a **✂️ Csak egy szakasz letöltése** opciót és add meg az időpontokat.
   Formátum: `óra:perc:mp` (`3:00:00`), `perc:mp` (`20:15`) vagy csak másodperc (`90`).
   A lekérdezés után a végpont automatikusan a videó hosszára áll be.
4. **⬇ Letöltés**

## Hibakeresés

Minden művelet naplózódik az exe melletti `zeneletolto.log` fájlba: a használt yt-dlp/ffmpeg verzió,
a kiadott parancsok, a letöltött byte-tartományok és a teljes hibaüzenetek. A programból a
**📄 Naplófájl megnyitása** gombbal éred el.

## Futtatás forrásból

```powershell
python zeneletolto.py
```

Csak a Python beépített moduljait használja, nincs `pip install`.

## Exe készítése

```powershell
.\build.bat
```

Az eredmény: `dist\YouTube Letolto.exe`. A PyInstallert szükség esetén magától telepíti.
