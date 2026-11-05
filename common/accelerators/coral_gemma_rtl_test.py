#!/usr/bin/env python3
"""Execute the actual QBox guest and require numerical/IRQ/AXI evidence."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--platform',type=Path,required=True)
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--qbox',type=Path,required=True)
p.add_argument('--log',required=True,type=Path)
p.add_argument('--timeout',type=int,default=14400)
p.add_argument('--generation-tokens',type=int,default=0)
p.add_argument('--expected-token-ids',nargs='*',type=int)
a=p.parse_args()
qbox=a.qbox.resolve()
build=a.build.resolve()
env=os.environ.copy();env['CORAL_ARTIFACTS']=str(a.artifacts.resolve())
var='DYLD_LIBRARY_PATH' if sys.platform=='darwin' else 'LD_LIBRARY_PATH'
env[var]=str(build)+(os.pathsep+env[var] if var in env else '')
env['CORAL_RTL_WAVEFORM']=''
a.log=a.log.resolve();a.log.parent.mkdir(parents=True,exist_ok=True)
command=[str(build/'platforms-vp'),'-l',str(a.platform.resolve()),'-p','log_level=1']
with a.log.open('w') as f:
    try:r=subprocess.run(command,cwd=qbox,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=a.timeout)
    except subprocess.TimeoutExpired:sys.exit(f'FAIL: QBox timeout; log={a.log}')
text=a.log.read_text(errors='replace')
for line in text.splitlines():
    if 'GEMMA_QBOX_' in line or 'CORAL_RTL_STATS' in line:print(line)
required=['GEMMA_QBOX_LINUX_START','GEMMA_QBOX_TEST_EXIT=0','GEMMA_QBOX_JOB_PASS']
required+=['GEMMA_QBOX_GREEDY_PASS'] if a.generation_tokens else ['GEMMA_QBOX_LAYER0_PASS backdoor=off']
missing=[s for s in required if s not in text]
done=re.search(r'GEMMA_QBOX_DONE irq=(\d+) external_axi=(\d+)',text)
if r.returncode or missing or 'GEMMA_QBOX_FAIL' in text or not done:
    sys.exit(f'FAIL: exit={r.returncode}, missing={missing}; log={a.log}')
if int(done[1])<1 or int(done[2])<1:sys.exit('FAIL: IRQ or external AXI unused')
ids=[int(x) for x in re.findall(r'GEMMA_QBOX_GENERATED step=\d+ token=(\d+)',text)]
if len(ids)!=a.generation_tokens:sys.exit(f'FAIL: generated IDs {ids}')
if a.expected_token_ids is not None and ids!=a.expected_token_ids:
    sys.exit(f'FAIL: token IDs {ids} != reference {a.expected_token_ids}')
print(f'GEMMA_QBOX_LINUX_ACCEPTANCE_PASS tokens={ids} log={a.log}')
