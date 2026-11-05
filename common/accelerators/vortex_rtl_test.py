#!/usr/bin/env python3
import argparse, os, re, subprocess, sys, time
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--qbox',type=Path,required=True)
p.add_argument('--build',type=Path,required=True)
p.add_argument('--platform',type=Path,required=True)
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--log',type=Path,required=True)
p.add_argument('--stage',choices=['smoke','mnist','interface','mobilenet','full'],default='mnist')
p.add_argument('--samples',type=int,default=2)
p.add_argument('--timeout',type=int,default=600)
p.add_argument('--waveform',type=Path)
p.add_argument('--backpressure',action='store_true')
a=p.parse_args();q=a.qbox.resolve();build=a.build.resolve()
env=os.environ.copy();env['VORTEX_ARTIFACTS']=str(a.artifacts.resolve());env['DYLD_LIBRARY_PATH' if sys.platform=='darwin' else 'LD_LIBRARY_PATH']=str(build)
env['VORTEX_RTL_WAVEFORM']=str(a.waveform.resolve()) if a.waveform else ''
env['VORTEX_RTL_BACKPRESSURE']='1' if a.backpressure else '0'
a.log=a.log.resolve();a.log.parent.mkdir(parents=True,exist_ok=True)
start=time.monotonic()
with a.log.open('w') as f:
    try:
        result=subprocess.run([str(build/'platforms-vp'),'-l',str(a.platform.resolve()),'-p','log_level=1'],cwd=q,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=a.timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f'FAIL timeout; log={a.log}')
log=a.log.read_text(errors='replace')
for line in log.splitlines():
    if any(s in line for s in ('VORTEX_', 'MNIST_', 'Passed!', 'errors=')): print(line)
required=['VORTEX_QBOX_BACKEND=AFU RTL','VORTEX_RTL_CASE_EXIT=demo:0','VORTEX_RTL_CASE_EXIT=demo2d:0','VORTEX_RTL_CASE_EXIT=sgemm:0','VORTEX_RTL_QUEUE_RESET_PASS','VORTEX_RTL_CASE_EXIT=reset:0','VORTEX_RTL_CASE_EXIT=recovery:0','VORTEX_QBOX_RTL_ALL_PASS','VORTEX_RTL_TEST_EXIT=0']
if a.stage in ('mnist','full'):
    required+=['VORTEX_RTL_CASE_EXIT=mnist:0','VORTEX_MNIST_ALL_PASS']
    rows=re.findall(r'MNIST_RESULT index=(\d+) label=(\d+) gpu=(\d+) cpu=(\d+) python=(\d+)',log)
    checks=re.findall(r'MNIST_LAYER_CHECK=([^ ]+) elements=(\d+) max_abs=([^ ]+) atol=([^ ]+) rtol=([^ ]+) errors=(\d+)',log)
    if len(rows)!=a.samples or [int(r[0]) for r in rows]!=list(range(a.samples)) or any(r[2]!=r[3] or r[2]!=r[4] for r in rows):
        sys.exit('FAIL: missing images or inconsistent classification references')
    if len(checks)!=6 or any(int(c[-1])!=0 for c in checks):
        sys.exit('FAIL: six intermediate numeric comparisons required')
irq_rows=re.findall(r'VORTEX_QBOX_IRQ waits=(\d+) driver_irqs=(\d+) rtl_pulses=(\d+) seq_start=(\d+) seq_end=(\d+) aw_stalls=(\d+) w_stalls=(\d+) ar_stalls=(\d+) idle_gates=(\d+) uid=(\d+)',log)
if not irq_rows or not any(int(r[1])>0 for r in irq_rows) or any(int(r[9])!=1000 for r in irq_rows):
    sys.exit('FAIL: unprivileged device/interrupt wait evidence required')
if a.stage in ('mnist','mobilenet','full') and not any(int(r[0])>0 for r in irq_rows):
    sys.exit('FAIL: no observed blocking completion ioctl')
if a.backpressure and any(int(v)==0 for v in irq_rows[-1][5:8]):
    sys.exit('FAIL: independent AXI channel stall counters required')
if a.stage in ('interface','full'):
    if not any(int(r[4])-int(r[3])>1024 for r in irq_rows): sys.exit('FAIL: no observed native ring wrap')
if a.stage in ('interface','full'): required+=['VORTEX_INTERFACE_ALL_PASS','VORTEX_RTL_CASE_EXIT=interface:0']
if a.stage in ('mobilenet','full'):
    numeric=re.findall(r'MOBILENET_LAYER_CHECK name=([^ ]+) compare=([^ ]+) elements=(\d+) max_abs=([^ ]+) atol=([^ ]+) rtol=([^ ]+) errors=(\d+)',log)
    launches=re.findall(r'MOBILENET_GPU_LAUNCH layer=(\d+)/(\d+) name=([^ ]+) type=(\d+) elements=(\d+)',log)
    if len(numeric)!=207 or len(launches)!=69 or any(int(c[-1]) for c in numeric) or [int(l[0]) for l in launches]!=list(range(1,70)):
        sys.exit('FAIL: complete 69-op MobileNet graph / 207 comparisons required')
    if not re.search(r'MOBILENET_RESULT gpu=(\d+) keras=\1 layers=69 gpu_all_ops=1',log):
        sys.exit('FAIL: model classification reference mismatch')
if a.stage in ('mobilenet','full'): required+=['VORTEX_MOBILENET_ALL_PASS','VORTEX_RTL_CASE_EXIT=mobilenet:0']
required+=['VORTEX_DRIVER_READY','VORTEX_QBOX_IRQ','VORTEX_DRIVER_API_PASS uid=1000','VORTEX_RTL_CASE_EXIT=driver:0']
missing=[s for s in required if s not in log]
stats=re.findall(r'VORTEX_QBOX_RTL cycles=(\d+) host_reads=(\d+) host_writes=(\d+) vram_reads=(\d+) vram_writes=(\d+)',log)
if result.returncode or missing or not stats or any(int(v)==0 for v in stats[-1]):
    sys.exit(f'FAIL exit={result.returncode} missing={missing} stats={stats}; log={a.log}')
print(f'PASS QBox/SystemC/TLM/Vortex AFU RTL wall_seconds={time.monotonic()-start:.2f} log={a.log}')
