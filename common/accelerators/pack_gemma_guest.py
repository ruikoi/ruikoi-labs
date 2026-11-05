#!/usr/bin/env python3
"""Build a Linux initramfs with guest executable, tokenizer and actual weights."""
import argparse, gzip, shutil, stat
from pathlib import Path
def header(name,size,mode,ino):
    name=name.encode()+b'\0'
    fields=[ino,mode,0,0,1,0,size,0,0,0,0,len(name),0]
    data=b'070701'+b''.join(f'{x:08x}'.encode() for x in fields)+name
    return data+b'\0'*((-len(data))%4)
p=argparse.ArgumentParser()
p.add_argument('--base',type=Path,required=True);p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=['interactive','prompt','tokenizer'],default='interactive')
p.add_argument('--max-new-tokens',type=int,default=192);p.add_argument('--prompt',default='介绍下你是什么模型')
a=p.parse_args()
def sh_quote(s):return "'"+s.replace("'","'\\''")+"'"
command=f'/guest-chat /gemma3_runner.elf /gemma-platform.bin /gemma-embeddings.bf16 /gemma-ddr.bin /tokenizer.model {a.max_new_tokens} {a.mode}'
if a.mode=='prompt':command+=' '+sh_quote(a.prompt)
init=f'''#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
echo GEMMA_CHAT_LINUX_START
uname -a
{command}
rc=$?
echo GEMMA_CHAT_TEST_EXIT=$rc
sync
sleep 1
poweroff -f
while true; do sleep 1; done
'''.encode()
files=['guest-chat','tokenizer.model']
if a.mode!='tokenizer':files+=['gemma3_runner.elf','gemma-platform.bin','gemma-embeddings.bf16','gemma-ddr.bin']
a.output.parent.mkdir(parents=True,exist_ok=True)
with gzip.open(a.output,'wb',compresslevel=1) as out:
    with gzip.open(a.base,'rb') as src:
        shutil.copyfileobj(src,out,1<<20);out.write(b'\0'*((-src.tell())%4))
    out.write(header('gemma-chat-init',len(init),stat.S_IFREG|0o755,10000));out.write(init);out.write(b'\0'*((-len(init))%4))
    for ino,name in enumerate(files,10001):
        src=a.artifacts/name;mode=stat.S_IFREG|(0o755 if name=='guest-chat' else 0o644)
        out.write(header(name,src.stat().st_size,mode,ino))
        with src.open('rb') as f:shutil.copyfileobj(f,out,1<<20)
        out.write(b'\0'*((-src.stat().st_size)%4))
    out.write(header('TRAILER!!!',0,0,20000))
print('GEMMA_CHAT_INITRAMFS_READY',a.output,a.output.stat().st_size)
