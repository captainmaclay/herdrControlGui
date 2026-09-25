# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Herdr Control Center standalone single-file GUI."""

import os
import sys

block_cipher = None
root = os.path.dirname(os.path.abspath(SPEC))

datas = [
    (os.path.join(root, 'settings.json'), '.'),
    (os.path.join(root, 'proxies.json'), '.'),
    (os.path.join(root, 'app_icon.ico'), '.'),
]
if os.path.exists(os.path.join(root, 'scripts')):
    datas.append((os.path.join(root, 'scripts'), 'scripts'))

a = Analysis(
    [os.path.join(root, 'config_app.py')],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'sync_manager',
        'proxy_manager',
        'gemini_manager',
        'settings_manager',
        'strategy_manager',
        'backup_manager',
        'claude_manager',
        'claude_oauth_manager',
        'watchdog_manager',
        'i18n',
        'pystray',
        'PIL',
        'dotenv',
        'requests',
        'socks',
        'sockshandler',
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.primitives',
    ],
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
    name='HerdrControlCenter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(root, 'app_icon.ico') if os.path.exists(os.path.join(root, 'app_icon.ico')) else None,
)
