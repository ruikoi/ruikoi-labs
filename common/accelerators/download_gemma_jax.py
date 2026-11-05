#!/usr/bin/env python3
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--objects',type=Path,required=True);a=p.parse_args()
root=a.out;root.mkdir(parents=True,exist_ok=True)
objects=json.loads(a.objects.read_text())['items']
prefix='checkpoints/gemma3-270m-it/'
def download(item):
    name=item['name'];path=root/name.removeprefix(prefix)
    path.parent.mkdir(parents=True,exist_ok=True)
    url='https://storage.googleapis.com/gemma-data/'+quote(name,safe='/')+'?generation='+item['generation']
    if not path.exists() or path.stat().st_size!=int(item['size']):
        with urlopen(url,timeout=120) as response,path.with_suffix(path.suffix+'.partial').open('wb') as out:
            for block in iter(lambda:response.read(1<<20),b''):out.write(block)
        path.with_suffix(path.suffix+'.partial').replace(path)
    sha=hashlib.sha256();md5=hashlib.md5()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):sha.update(block);md5.update(block)
    assert path.stat().st_size==int(item['size'])
    assert base64.b64encode(md5.digest()).decode()==item['md5Hash'],name
    result={'name':name,'generation':item['generation'],'bytes':int(item['size']),'sha256':sha.hexdigest()}
    print('CHECKPOINT_OBJECT_VERIFIED',json.dumps(result),flush=True)
    return result
with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(download,objects))
(root/'download-manifest.json').write_text(json.dumps(results,indent=2)+'\n')
print('OFFICIAL_GEMMA_CHECKPOINT_DOWNLOAD_PASS',len(results))
