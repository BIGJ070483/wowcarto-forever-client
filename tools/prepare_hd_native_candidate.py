"""Prepare a private experimental 32px DFM candidate; never run or install it.

The layout differs from the original and source pixels are merely enlarged.
This is not a production executable or evidence of broad compatibility.
"""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import shutil
import struct
import sys
import pefile
from tools.local_install import EXPECTED_SHA256, checked_path, digest
from tools.inspect_native_ui import DfmReader, parse_dfm, summarize_dfm

if os.name != 'nt' or len(sys.argv) != 3:
    raise SystemExit('Windows : fournir la source 1.20 et une nouvelle destination privée')
source, output = map(checked_path, sys.argv[1:])
repo = Path(__file__).resolve().parents[1]
backups = checked_path(Path(os.environ['USERPROFILE']) / 'Documents' / 'WoWCarto-sauvegardes')
if (output.exists() or output.is_relative_to(source.parent) or output.is_relative_to(repo)
        or output.is_relative_to(backups) or not output.parent.is_dir() or digest(source) != EXPECTED_SHA256):
    raise SystemExit('Source or destination refused')
pe = pefile.PE(str(source))
entries = [lang for typ in pe.DIRECTORY_ENTRY_RESOURCE.entries if typ.id == 10
           for name in typ.directory.entries if str(name.name) == 'TMAINFORM'
           for lang in name.directory.entries if lang.id == 0]
if len(entries) != 1:
    raise SystemExit('Resource identity mismatch')
entry = entries[0].data.struct
data = pe.get_data(entry.OffsetToData, entry.Size)
parse_dfm(data)
original_commands = summarize_dfm(data)['commands']

def marker(cls, name):
    a, b = cls.encode('ascii'), name.encode('ascii')
    return bytes([len(a)]) + a + bytes([len(b)]) + b

def edit(cls, name, changes):
    global data
    ident = marker(cls, name)
    if data.count(ident) != 1:
        raise ValueError('component not unique: ' + name)
    start = data.index(ident) + len(ident)
    reader = DfmReader(data)
    reader.pos = start
    positions = {}
    while reader.peek():
        key = reader.short()
        before = reader.pos
        value = reader.value(1)
        positions[key] = (before, reader.pos, value)
    inserts = b''
    replacements = []
    for key, wanted in changes.items():
        if isinstance(wanted, int) and 0 <= wanted <= 32767:
            encoded_value = (b'\x02' + bytes([wanted])) if wanted <= 127 else b'\x03' + struct.pack('<h', wanted)
        elif wanted == '':
            encoded_value = b'\x06\x00'
        else:
            raise ValueError('bounded value required')
        if key in positions:
            a, b, old = positions[key]
            if not (type(old) is int or isinstance(old, str)):
                raise ValueError('unexpected original value encoding: ' + name + '.' + key)
            replacements.append((a, b, encoded_value))
        else:
            encoded = key.encode('ascii')
            inserts += bytes([len(encoded)]) + encoded + encoded_value
    for a, b, encoded_value in sorted(replacements, reverse=True):
        data = data[:a] + encoded_value + data[b:]
    data = data[:start] + inserts + data[start:]

for name in ('ImageList1', 'ImageList3'):
    edit('TImageList', name, {'Width': 32, 'Height': 32})
for name in ('ToolBar1', 'ToolBar2', 'ToolBar4', 'ToolBar5'):
    edit('TToolBar', name, {'ButtonWidth': 50, 'ButtonHeight': 50})
edit('TToolBar', 'ToolBar3', {'Width': 50, 'ButtonWidth': 50, 'ButtonHeight': 50})
edit('TPanel', 'PanelToolBar', {'Height': 50})
edit('TPanel', 'Panel2', {'Height': 50})
edit('TPanel', 'Panel1', {'Width': 140})
edit('TPanel', 'Panel4', {'Height': 54, 'Caption': ''})
edit('TPanel', 'Panel6', {'Width': 54})
edit('TToolBar', 'ToolBar4', {'Width': 140, 'Height': 50})
edit('TToolBar', 'ToolBar5', {'Width': 54, 'Height': 50})
edit('TToolBar', 'ToolBar2', {'Width': 200})
parse_dfm(data)
if summarize_dfm(data)['commands'] != original_commands:
    raise SystemExit('Command or event mapping changed')
shutil.copyfile(source, output)
k = ct.WinDLL('kernel32', use_last_error=True)
k.BeginUpdateResourceW.argtypes = [wt.LPCWSTR, wt.BOOL]; k.BeginUpdateResourceW.restype = wt.HANDLE
k.UpdateResourceW.argtypes = [wt.HANDLE, ct.c_void_p, wt.LPCWSTR, wt.WORD, ct.c_void_p, wt.DWORD]; k.UpdateResourceW.restype = wt.BOOL
k.EndUpdateResourceW.argtypes = [wt.HANDLE, wt.BOOL]; k.EndUpdateResourceW.restype = wt.BOOL
handle = k.BeginUpdateResourceW(str(output), False)
if not handle:
    raise ct.WinError(ct.get_last_error())
buffer = ct.create_string_buffer(data)
if not k.UpdateResourceW(handle, ct.c_void_p(10), 'TMAINFORM', 0, buffer, len(data)):
    error = ct.get_last_error(); k.EndUpdateResourceW(handle, True); raise ct.WinError(error)
if not k.EndUpdateResourceW(handle, False):
    raise ct.WinError(ct.get_last_error())
new = pefile.PE(str(output))
found = [lang for typ in new.DIRECTORY_ENTRY_RESOURCE.entries if typ.id == 10
         for name in typ.directory.entries if str(name.name) == 'TMAINFORM'
         for lang in name.directory.entries if lang.id == 0]
if len(found) != 1 or new.get_data(found[0].data.struct.OffsetToData, found[0].data.struct.Size) != data:
    raise SystemExit('Readback failed')
print('candidate_sha256=', digest(output), 'resource_bytes=', len(data),
      'commands_preserved=', len(original_commands), 'tested_or_installed=false')
