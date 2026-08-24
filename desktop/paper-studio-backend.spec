"""Build the current platform's self-contained Paper Studio backend."""

from pathlib import Path


project_root = Path(SPECPATH).parent

analysis = Analysis(
    [str(project_root / "desktop" / "backend_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "agent" / "static"), "agent/static"),
        (str(project_root / "agent" / "skills" / "SKILL.md"), "agent/skills"),
    ],
    hiddenimports=["agent.mcp_server"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="paper-studio-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="paper-studio-backend",
)
