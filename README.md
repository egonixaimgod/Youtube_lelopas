# YouTube Letöltő

Windows asztali alkalmazás YouTube (és sok más oldal) videóinak és zenéinek letöltéséhez.
A `yt-dlp`-t és az `ffmpeg`-et első indításkor magától letölti, semmit nem kell külön telepíteni.

## Funkciók

- **🎵 Zene (MP3)** – választható kimeneti bitráta (128 / 192 / V0 / 256 / **320 kbps**, alapból a
  legjobb), borítóképpel és metaadatokkal. A forrás hangsáv mindig a legjobb elérhető, és a lista
  ki is írja – a YouTube Opusban ~130 kbps-t ad, e fölött a nagyobb MP3 bitráta már csak a fájlt
  hizlalja.
- **🎬 Videó** – az összes elérhető minőség táblázatban (felbontás, FPS, kodek, méret),
  a legjobb előre kijelölve
- **🥽 VR 3D (SBS)** – ugyanaz a minőségválaszték, csak `_3D_SBS` névvel
  (a forrásnak már side-by-side-nak kell lennie)
- **Konténer** – alapból a **gyári formátum**, és a legördülő ki is írja, épp melyik az
  (`Gyári formátum – MKV`). Kézzel átállítható MP4-re vagy MKV-ra.
  **Egyik sem kódol újra semmit**, csak a „doboz" más
- **✂️ Részlet letöltése csúszkával** – csak egy időintervallum, pl. egy 4 órás videóból a
  `3:00:00`–`3:20:00` rész. Nem a teljes videót tölti le és vágja ki: kiszámolja a szakaszhoz
  tartozó byte-tartományt, és párhuzamos Range kérésekkel tényleg csak azt szedi le.
  Egy 3,5 órás 4K videóból 30 másodperc kivágása ~64 MB letöltés, 7 másodperc.
- **Lejátszási lista** támogatás (részlet módban nem)
- **Önfrissítés** – induláskor megnézi, van-e újabb build a GitHubon, és felajánlja a
  telepítést. Egy kattintás: letölti, lecseréli magát és újraindul.
- **Naplózás** – minden művelet az exe melletti `zeneletolto.log` fájlba kerül

- **Lapok** – a fülsor végén lévő `+` gombbal új lapot nyithatsz (`Ctrl+T`), és egyszerre több
  letöltés futhat. Minden lap üresen indul, saját linkkel, minőséggel és naplóval.
  A lapfülön látszik, mit tölt épp és hol tart; ha elkészült, **zöld pipa** kerül rá –
  így másik lapról is látod, hogy kész van. Lap bezárása: a fülön az `✕` (`Ctrl+W`),
  váltás a fülre kattintva vagy `Ctrl+Tab`-bal.

## Használat

0. Ha több videót akarsz egyszerre, nyiss annyi lapot a `+` gombbal – a lépések
   laponként külön értendők.
1. Illeszd be a linket (**Beillesztés** gomb vagy Ctrl+V). A program magától elemzi:
   kiírja a címet, az előnézeti képet, a hosszt és az összes elérhető minőséget.
   Elemzés előtt a többi vezérlő szürke – nincs mit beállítani rajtuk.
2. Válassz módot (zene / videó / VR) és a listából minőséget.
3. Ha csak egy részlet kell, pipáld be a **Csak egy részlet letöltése** opciót, és húzd a
   csúszka két fogantyúját – vagy írd be az időpontokat a mezőkbe (`3:00:00`, `20:15`, `90`).
4. **↓ Letöltés**. A haladás sávon látszik a százalék, a sebesség és a hátralévő idő.

### Konténer és minőség

A YouTube DASH-t szolgál ki: a videó és a hang **külön fájlként** érkezik (VP9 → webm,
AV1/H.264 → mp4, AAC → m4a, Opus → webm), ezért mindig össze kell fűzni őket. Ez az
összefűzés **újracsomagolás** (`-c copy`), nem újrakódolás – a videósáv bitre ugyanaz marad,
akármelyik konténert választod. A program soha nem kódol újra videót.

Emiatt jön le egy 4K/8K (jellemzően VP9) videó natívan webm-ként: az „Automatikus" beállítás
ilyenkor MKV-t ad. MP4-et azért érdemes választani, mert ahhoz mutat előnézetet az Explorer,
és a régebbi lejátszók/TV-k is jobban szeretik.

### A minőségekről

A YouTube minőségi címkéje nem mindig a pixelmagasság: egy 21:9-es ultrawide videó
3440×1440-es változata a YouTube-nál **2160p (4K)**. A program a YouTube saját címkéjét
használja, és mindig a `https` (DASH) streamet választja a HLS változat helyett – ugyanaz a
tartalom, de a HLS-nek hamisan magas a bitrátája, nincs byte-indexe, és lassabban is jön.

## Frissítés

A program induláskor összeveti a saját build számát a GitHubon közzétett legfrissebb
kiadással. Ha van újabb, felugrik egy ablak **Telepítés** és **Később** gombbal.
A Telepítés letölti az új exét, lecseréli a futót és újraindítja a programot.

A **„Ne jelenjen meg többé"** jelölő megjegyzett beállítás: mindkét gomb (Telepítés és
Később) elmenti, és amikor legközelebb előjön az ablak, már bepipálva nyílik. Ha kiveszed
a pipát, újra kapsz értesítést.

Bepipálva többet nem ugrik fel magától – de bal alul megmarad egy zöld
`● Új verzió: build N` jelzés, amire kattintva bármikor előhozható ugyanez az ablak.

Kézzel bármikor kereshetsz frissítést a lábléc **Frissítés keresése** linkjével.

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

A PyInstallert szükség esetén magától telepíti.

### Ikoncsere

Az ikon útvonala a `YouTubeLetolto.spec`-ben van, **név szerint** – a `build.bat`-ban nincs
ikonra hivatkozás. Ha lecseréled a `icon_youtube_letolto.ico` fájlt ugyanezzel a névvel,
a következő build már az újat használja, semmit nem kell átírni.

Valódi `.ico` kell, nem átnevezett PNG – a Tk ablakikon csak azt fogadja el. PNG-ből
bármelyik online vagy asztali ico-konverterrel készíthetsz ilyet.

A jelenlegi ikon egyetlen 512×512-es képet tartalmaz, és ez szándékos: a Windows a nagy
képből maga skáláz a tálcára, ami ennél a logónál élesebb, mint a fájlba előre beégetett
16/32 képpontos változatok.
