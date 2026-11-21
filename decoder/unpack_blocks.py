"""Pipe filter: read (packed length, output length, packed bytes), emit decoded bytes."""
import struct
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import ooz

def main():
    source=sys.stdin.buffer
    while header:=source.read(8):
        packed,size=struct.unpack('<II',header)
        if not 0<packed<=131072 or not 0<size<=65536:raise ValueError('invalid block length')
        block=source.read(packed)
        if len(block)!=packed:raise EOFError('incomplete block')
        sys.stdout.buffer.write(ooz.decompress(block,size))
        sys.stdout.buffer.flush()

if __name__=='__main__':main()
