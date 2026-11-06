"""Extract the authentic application icon from the known 1.20 executable."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import pefile

EXPECTED = '581d8940c6a5471184d7d8ce9e7cf01a2a5cf48ddabe427f8ff4b7483582d457'


def extract(source: Path, target: Path):
    if hashlib.sha256(source.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('L’exécutable ne correspond pas à la référence 1.20 vérifiée.')
    if target.exists(): raise FileExistsError(target)
    pe=pefile.PE(str(source))
    entries={e.id:e for e in pe.DIRECTORY_ENTRY_RESOURCE.entries if e.id in (3,14)}
    def read(entry):
        data=entry.directory.entries[0].data.struct
        return pe.get_data(data.OffsetToData,data.Size)
    groups=entries[14].directory.entries
    group=read(next(e for e in groups if str(e.name)=='MAINICON'))
    reserved,kind,count=struct.unpack_from('<HHH',group)
    if reserved!=0 or kind!=1 or not 1<=count<=64 or len(group)!=6+14*count: raise ValueError('Groupe d’icônes invalide.')
    icons={e.id:read(e) for e in entries[3].directory.entries}
    directories=[]; payload=[]; offset=6+16*count
    for i in range(count):
        width,height,colors,reserved,planes,bits,size,ident=struct.unpack_from('<BBBBHHIH',group,6+14*i)
        blob=icons[ident]
        if len(blob)!=size: raise ValueError('Taille de ressource incohérente.')
        directories.append(struct.pack('<BBBBHHII',width,height,colors,reserved,planes,bits,size,offset))
        payload.append(blob); offset+=size
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream: stream.write(struct.pack('<HHH',0,1,count)+b''.join(directories)+b''.join(payload))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path); parser.add_argument('target',type=Path)
    args=parser.parse_args(); extract(args.source,args.target)
