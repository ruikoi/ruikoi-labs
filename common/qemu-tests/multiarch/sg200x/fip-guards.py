# SPDX-License-Identifier: GPL-2.0-or-later
import argparse,binascii,json,pathlib,struct,subprocess,tempfile
p=argparse.ArgumentParser();p.add_argument('--qemu',required=True);p.add_argument('--fip',required=True,type=pathlib.Path);p.add_argument('--output',required=True,type=pathlib.Path);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True)
original=a.fip.read_bytes();pos=struct.unpack_from('<I',original,0xe0)[0]
load=struct.unpack_from('<I',original,pos+68)[0];length=struct.unpack_from('<I',original,load+12)[0]
def crc(b):return 0xcafe0000|binascii.crc_hqx(b,0)
cases=[]
for name,expected in [('param1','PARAM1'),('param2','PARAM2'),('monitor','monitor range or CRC'),('monitor-range','monitor range or CRC'),('small-range','small-core range or CRC'),('compression','unsupported loader compression'),('lzma','invalid or oversized LZMA'),('loader-range','loader overlaps monitor')]:
    data=bytearray(original)
    if name=='param1':data[12]^=1
    elif name=='param2':data[pos+8]^=1
    elif name=='monitor':data[struct.unpack_from('<I',data,pos+52)[0]+32]^=1
    elif name=='monitor-range':struct.pack_into('<I',data,pos+60,0x04000000)
    elif name=='small-range':struct.pack_into('<I',data,pos+44,0x80000100)
    elif name=='compression':struct.pack_into('<I',data,load+4,0x12345678)
    elif name=='lzma':data[load+32]=0xff
    elif name=='loader-range':struct.pack_into('<Q',data,load+16,0xfffffff080000000)
    if name in ('monitor-range','small-range'):struct.pack_into('<I',data,pos+8,crc(data[pos+12:pos+4096]))
    if name in ('lzma','loader-range'):struct.pack_into('<I',data,load+8,crc(data[load+12:load+length]))
    with tempfile.TemporaryDirectory(prefix='fipguard-') as tmp:
        image=pathlib.Path(tmp)/'bad.bin';image.write_bytes(data)
        cmd=[a.qemu,'-M',f'milkv-duos,boot-mode=fip,fip={image}','-accel','tcg,thread=single','-display','none','-monitor','none','-serial','null','-serial','null','-S']
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        (a.output/(name+'.log')).write_text(result.stderr)
        assert result.returncode!=0 and expected in result.stderr,(name,result)
        cases.append([name,'PASS']);print(name,'PASS',flush=True)
(a.output/'results.json').write_text(json.dumps(cases,indent=2))
