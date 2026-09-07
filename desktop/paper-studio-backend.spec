"""Build the current platform's complete self-contained Paper Studio backend.

The Web/App process discovers skills, templates and MCP capabilities through
registries.  Those imports are not always visible to PyInstaller's static
analysis, so collect every application and MCP submodule explicitly.  This is
intentional: a desktop release must include the same capability set as the Web
source, rather than a partial subset that happens to be reachable at startup.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
hiddenimports = sorted(set(
    collect_submodules("agent")
    + collect_submodules("mcp")
    + ["agent.mcp_server"]
))

analysis = Analysis(
    [str(project_root / "desktop" / "backend_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "agent" / "static"), "agent/static"),
        (str(project_root / "agent" / "skills" / "SKILL.md"), "agent/skills"),
    ],
    hiddenimports=hiddenimports,
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
