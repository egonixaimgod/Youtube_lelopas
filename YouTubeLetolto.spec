# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['zeneletolto.py'],
    pathex=[],
    binaries=[],
    # Az ikon a futó ablaknak és a tálcának is kell, ezért be kell csomagolni:
    # a program a sys._MEIPASS mappából tölti be (lásd _ikon_beallitas).
    datas=[('icon_youtube_letolto.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='YouTube Letolto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX tömörítés kikapcsolva: a csomagolt (packed) exe-ket a malware-szerzők
    # is előszeretettel használják aláírás-felismerés megkerülésére, ezért a
    # Defender/heurisztikus AV-motorok UPX-es PyInstaller exe-ket sokkal
    # gyakrabban jelölnek meg/törölnek, mint tömörítetlent.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Ablakos program: konzolablak nélkül indul. Emiatt nincs, ami kiírja a
    # kivételeket - azokat a zeneletolto.log fogja.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon=['icon_youtube_letolto.ico'],
)
