# -*- mode: python ; coding: utf-8 -*-
"""今日事 PyInstaller 打包配置：单文件、无控制台窗口、含前端资源"""

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='今日事',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,          # Windows 无 strip 工具，关闭避免构建失败
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 无命令行黑窗，像普通桌面 App
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
