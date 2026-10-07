# PyInstaller recipe for "Notes to Google Docs".
#
#   pyinstaller notes2gdoc.spec
#
# macOS:   dist/Notes to Google Docs.app   (a normal Mac app; zipped for download)
# Windows: dist/NotesToGoogleDocs.exe      (a single file you double-click)
#
# The Google client file (client_secret.json) is NOT bundled: each person
# chooses it the first time they sign in (see SETUP.md).

import sys

from PyInstaller.utils.hooks import collect_data_files

APP_NAME = "Notes to Google Docs"
VERSION = "0.7.1"

datas = [
    ("src/notes2gdoc/resources/icon.png", "notes2gdoc/resources"),
]
# The Google API client describes each API (Docs, Drive) with JSON files that
# must ship with the app.
datas += collect_data_files("googleapiclient", includes=["discovery_cache/documents/docs.v1.json",
                                                         "discovery_cache/documents/drive.v3.json"])

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=[],
    # Big optional pieces of Qt and Python the app doesn't use
    excludes=[
        "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=APP_NAME,
        console=False,
        icon="packaging/icon.png",
    )
    coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon="packaging/icon.png",
        bundle_identifier="io.github.notes2gdoc",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            # Lets you drag a PDF onto the app's icon in the Dock
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF document",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["com.adobe.pdf"],
            }],
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="NotesToGoogleDocs",
        console=False,
        icon="packaging/icon.png",
        upx=False,
    )
