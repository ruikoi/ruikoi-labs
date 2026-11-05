#!/usr/bin/env python3
"""Execute official add and quantized RVV model with the real Coral C ABI."""
import ctypes as C
import json
import struct
import sys
from pathlib import Path
from elf32 import read

class Request(C.Structure):
    _fields_ = [('itcm',C.POINTER(C.c_ubyte)),('dtcm',C.POINTER(C.c_ubyte)),
                ('ext',C.POINTER(C.c_ubyte)),('entry',C.c_uint32),('limit',C.c_uint32),
                ('steps',C.c_uint64),('error',C.c_char*256)]
lib = C.CDLL(sys.argv[1])
lib.coral_qemu_abi.restype = C.c_uint32
lib.coral_qemu_run.argtypes = [C.POINTER(Request)]
assert lib.coral_qemu_abi() == 1
itcm=(C.c_ubyte*0x2000)()
dtcm=(C.c_ubyte*0x8000)()
ext=(C.c_ubyte*0x400000)()
r = Request(itcm,dtcm,ext,0,100000,0,b'')
def load(path):
    C.memset(itcm,0,len(itcm)); C.memset(dtcm,0,len(dtcm))
    entry,segments,symbols = read(path)
    for addr,data,size in segments:
        if 0 <= addr and addr+size <= 0x2000: base=C.addressof(itcm)+addr
        elif 0x10000 <= addr and addr+size <= 0x18000: base=C.addressof(dtcm)+addr-0x10000
        else: raise ValueError(f'unsupported segment {addr:x}')
        C.memmove(base,data,len(data))
    r.entry = entry
    return symbols
def write_data(addr,data): C.memmove(C.addressof(dtcm)+addr-0x10000,data,len(data))
def read_data(addr,size): return C.string_at(C.addressof(dtcm)+addr-0x10000,size)
def run():
    code=lib.coral_qemu_run(C.byref(r))
    assert code==1,(code,r.error,r.steps)
    assert r.steps>0
    return r.steps
symbols=load('add.elf')
for case in range(4):
    a=[(i-case)*.5 for i in range(8)]
    b=[(case+i)*.25 for i in range(8)]
    write_data(symbols['input1'][0],struct.pack('<8f',*a))
    write_data(symbols['input2'][0],struct.pack('<8f',*b))
    steps=run()
    out=struct.unpack('<8f',read_data(symbols['output'][0],32))
    assert list(out)==[x+y for x,y in zip(a,b)],out
    print(f'PASS official add case={case} steps={steps} output={out}')
symbols=load('model.elf'); addr=symbols['model_io'][0]
model=json.loads(Path('model.json').read_text())
for i,c in enumerate(model['cases']):
    write_data(addr,struct.pack('<16i',*c['input']))
    steps=run()
    out=struct.unpack('<36i',read_data(addr,144))
    assert list(out[16:32])==c['hidden'],(i,out[16:32],c['hidden'])
    assert list(out[32:35])==c['output'],(i,out[32:35],c['output'])
    assert out[35]==c['predicted']
    print(f'PASS RVV model case={i} steps={steps} logits={out[32:35]} class={out[35]}')
Path('elf_symbols.h').write_text('/* Generated from verified ELF symbols. */\n'+
    '\n'.join(f'#define ADD_{k.upper()} 0x{v[0]:x}u' for k,v in read('add.elf')[2].items() if k in ['input1','input2','output'])+
    f'\n#define MODEL_IO 0x{addr:x}u\n')
print('BACKEND_ALL_PASS')
