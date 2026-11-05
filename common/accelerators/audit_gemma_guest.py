#!/usr/bin/env python3
"""Check guest/NPU execution evidence; a plausible answer alone is insufficient."""
import argparse,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('log',type=Path);p.add_argument('--snapshot',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
raw=a.log.read_bytes();text=raw.decode();lines=text.splitlines()
inputs=[json.loads(s.split('GEMMA_CHAT_INPUT ',1)[1]) for s in lines if 'GEMMA_CHAT_INPUT {' in s]
tokens=[json.loads(s.split('GEMMA_CHAT_TOKEN ',1)[1]) for s in lines if 'GEMMA_CHAT_TOKEN {' in s]
results=[json.loads(s.split('GEMMA_CHAT_RESULT ',1)[1]) for s in lines if 'GEMMA_CHAT_RESULT {' in s]
errors=[]
def check(condition,message):
    if not condition:errors.append(message)
check('GEMMA_CHAT_FAIL' not in text,'guest failure')
check('backend=coral_mpact_m3_decoder' in text,'actual decoder provenance missing')
check('riscv64 GNU/Linux' in text,'guest boot identity missing')
check('abi=2 tcm_bytes=2097152 ddr_bytes=536870912' in text,'device identity missing')
check('TOKENIZER_CHECK ids=[33390,237245,237408,54847,26609]' in text,'Chinese tokenizer check missing')
offset=0;prev_jobs=prev_steps=0
for i,r in enumerate(results):
    check(r['execution']=='qemu_guest_coral_mpact_m3','CPU/fallback execution tag')
    check(i<len(inputs) and inputs[i]['input_ids']==r['input_ids'],'input mismatch')
    ids=r['output_ids'];batch=tokens[offset:offset+len(ids)];offset+=len(ids)
    check([t['id'] for t in batch]==ids,'token stream differs from final response')
    check([t['step'] for t in batch]==list(range(len(ids))),'token stream incomplete')
    expected_jobs=len(r['input_ids'])-1+65*len(ids)
    check(r['npu_jobs']==expected_jobs,'18-layer jobs / full 64 head chunks missing')
    check(r['stop_reason']=='eos' and ids and ids[-1] in (1,106),'not a complete EOS answer')
    check(r['npu_steps']>0 and r['ddr_read_bytes']>0 and r['ddr_write_bytes']>0,'no NPU execution/memory evidence')
    check('<end_of_turn>' not in r['text'],'special marker leaked to response')
    check(r['total_npu_jobs']==prev_jobs+r['npu_jobs'],'job count discontinuity')
    for t in batch:
        check(t['npu_jobs']>prev_jobs and t['npu_steps']>prev_steps,'NPU counters did not advance')
        prev_jobs=t['npu_jobs'];prev_steps=t['npu_steps']
    if i:
        old=results[i-1]
        expected_prefix=old['input_ids']+old['output_ids']+[107]
        check(r['input_ids'][:len(expected_prefix)]==expected_prefix,'multi-turn history not preserved')
done='GEMMA_CHAT_TEST_EXIT=0' in text and 'GEMMA_CHAT_DONE' in text
if done:
    check(len(inputs)==len(results),'guest exited with an unfinished question')
    check(offset==len(tokens),'guest exited with an unfinished token stream')
status='FAIL' if errors else 'PASS' if results and done else 'INCOMPLETE'
report={'status':status,'log':str(a.log),'sha256':hashlib.sha256(raw).hexdigest(),'results':results,'errors':errors,'completed_turns':len(results),'guest_exit_observed':done}
if a.output:a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(1 if errors else 0 if status=='PASS' or a.snapshot else 2)
