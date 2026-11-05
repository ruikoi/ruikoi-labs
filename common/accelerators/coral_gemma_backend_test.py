#!/usr/bin/env python3
"""Real instruction regressions for vector broadcast and completion/error paths."""
import argparse,ctypes as c,mmap,struct,json
class Request(c.Structure):
    _fields_=[('tcm',c.c_void_p),('ddr',c.c_void_p),('entry',c.c_uint32),('limit',c.c_uint32),('steps',c.c_uint64),('reads',c.c_uint64),('writes',c.c_uint64),('error',c.c_char*256)]
p=argparse.ArgumentParser();p.add_argument('--backend',required=True);a=p.parse_args()
lib=c.CDLL(a.backend);lib.coral_gemma_run.argtypes=[c.POINTER(Request)]
def request(code,limit=1000):
    t=mmap.mmap(-1,0x200000);d=mmap.mmap(-1,0x20000000)
    t[:len(code)*4]=struct.pack('<%dI'%len(code),*code)
    # Raw float 1.5 at DTCM base, loaded into f15.
    struct.pack_into('<f',t,0x100000,1.5)
    r=Request(c.addressof(c.c_char.from_buffer(t)),c.addressof(c.c_char.from_buffer(d)),0,limit)
    return t,d,r
for lmul in (0,1,2,3):
    # lui x1,0x100; flw f15,0(x1); vsetvli x2,zero,e32,m{1,2,4,8};
    # vfmv.v.f v8,f15; addi x3,x1,16; vs{1,2,4,8}r.v v8,(x3); mpause.
    code=[0x001000b7,0x0000a787,0x0d007157|(lmul<<20),0x5e07d457,
          0x01008193,0x02818427|(((1<<lmul)-1)<<29),0x08000073]
    t,d,r=request(code);result=lib.coral_gemma_run(c.byref(r))
    got=struct.unpack_from('<%df'%(4<<lmul),t,0x100010)
    assert result==1 and all(x==1.5 for x in got),(lmul,result,got,r.error)
    print('CORAL_BROADCAST_PASS',json.dumps({'lmul':1<<lmul,'elements':len(got),'steps':r.steps}))
    del r;t.close();d.close()
for name,code,limit,expected in [('illegal',[0xffffffff],1000,3),('timeout',[0x0000006f],8,2),('invalid_entry',[0x08000073],1000,4)]:
    t,d,r=request(code,limit)
    if name=='invalid_entry':r.entry=0x100000
    result=lib.coral_gemma_run(c.byref(r));assert result==expected,(name,result,r.error)
    print('CORAL_ERROR_PATH_PASS',json.dumps({'case':name,'result':result,'steps':r.steps,'error':r.error.decode()}))
    del r;t.close();d.close()
print('CORAL_GEMMA_BACKEND_TEST_PASS')
