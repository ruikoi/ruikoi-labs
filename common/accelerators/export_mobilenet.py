#!/usr/bin/env python3
import os, argparse, json, hashlib, shutil
from pathlib import Path
os.environ['KERAS_BACKEND']='jax'
os.environ.setdefault('KERAS_HOME',str(Path(os.environ.get('LAB_WORK','.work'))/'cache/keras'))
import keras
import numpy as np
from PIL import Image
p=argparse.ArgumentParser(); p.add_argument('--image',type=Path,required=True); p.add_argument('--alpha',type=float,default=.35); p.add_argument('--size',type=int,default=96); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
a.out.mkdir(parents=True,exist_ok=True)
model=keras.applications.MobileNetV2(alpha=a.alpha,input_shape=(a.size,a.size,3),weights='imagenet')
image=a.image
x=np.asarray(Image.open(image).convert('RGB').resize((a.size,a.size)),dtype=np.float32)[None]/np.float32(127.5)-np.float32(1)
x.astype('<f4').tofile(a.out/'input.bin')
shutil.copy2(image,a.out/'image.jpg')
ops=[]; aliases={}; outputs=[]
def source(t): return aliases[t._keras_history.operation.name]
def dims(shape):
    s=list(shape)[1:]
    return s if len(s)==3 else [1,1,int(np.prod(s))]
def store(idx,key,array):
    np.asarray(array,dtype='<f4').tofile(a.out/f'{idx:03d}-{key}.bin')
for layer in model.layers:
    kind=layer.__class__.__name__
    if kind=='InputLayer': aliases[layer.name]=0; continue
    if kind=='BatchNormalization':
        i=source(layer.input)-1; op=ops[i]
        gamma,beta,mean,var=layer.get_weights()
        scale=gamma/np.sqrt(var+np.float32(layer.epsilon))
        op['weight']=op['weight']*scale.reshape([-1]+[1]*(op['weight'].ndim-1))
        op['bias']=(op['bias']-mean)*scale+beta
        aliases[layer.name]=i+1; outputs[i]=layer.output; continue
    if kind=='ReLU':
        i=source(layer.input)-1; assert layer.max_value==6
        ops[i]['relu']=6; aliases[layer.name]=i+1; outputs[i]=layer.output; continue
    ins=layer.input if isinstance(layer.input,list) else [layer.input]
    op=dict(name=layer.name,src=source(ins[0]),src2=source(ins[1]) if len(ins)>1 else 0,
            ih=0,iw=0,ic=0,oh=0,ow=0,oc=0,kh=1,kw=1,stride=1,ph=0,pw=0,relu=0,weight=np.array([],np.float32),bias=np.array([],np.float32))
    op['ih'],op['iw'],op['ic']=dims(ins[0].shape); op['oh'],op['ow'],op['oc']=dims(layer.output.shape)
    if kind in ['Conv2D','DepthwiseConv2D']:
        op['type']=0 if kind=='Conv2D' else 1
        w=layer.get_weights()[0]
        op['weight']=w.transpose(3,0,1,2) if op['type']==0 else w[:,:,:,0].transpose(2,0,1)
        op['bias']=np.zeros(op['oc'],np.float32)
        op['kh'],op['kw']=layer.kernel_size; assert layer.strides[0]==layer.strides[1]; op['stride']=layer.strides[0]
        if layer.padding=='same':
            op['ph']=max(0,(op['oh']-1)*op['stride']+op['kh']-op['ih'])//2
            op['pw']=max(0,(op['ow']-1)*op['stride']+op['kw']-op['iw'])//2
    elif kind=='Add': op['type']=2
    elif kind=='ZeroPadding2D': op['type']=3; op['ph']=layer.padding[0][0]; op['pw']=layer.padding[1][0]
    elif kind=='GlobalAveragePooling2D': op['type']=4
    elif kind=='Dense':
        op['type']=5; w,b=layer.get_weights(); op['weight']=w.T; op['bias']=b
        assert layer.activation.__name__=='softmax'
    else: raise RuntimeError((kind,layer.name))
    ops.append(op); aliases[layer.name]=len(ops); outputs.append(layer.output)
refs=keras.Model(model.input,outputs)(x,training=False)
refs=[np.asarray(r,dtype=np.float32) for r in refs]
# The Dense layer's Keras output includes softmax; retain an independent logits oracle too.
last=ops[-1]; assert last['type']==5
refs[-1]=refs[last['src']-1].reshape(1,-1) @ last['weight'].T + last['bias']
prob=np.asarray(model(x,training=False),dtype=np.float32)
soft=dict(ops[-1]); soft.update(name='gpu_softmax',type=6,src=len(ops),src2=0,ih=1,iw=1,ic=1000,oh=1,ow=1,oc=1000,weight=np.array([],np.float32),bias=np.array([],np.float32))
ops.append(soft); refs.append(prob)
fields=['type','src','src2','ih','iw','ic','oh','ow','oc','kh','kw','stride','ph','pw','relu']
lines=[]; macs=0
for i,(op,ref) in enumerate(zip(ops,refs),1):
    store(i,'weight',op['weight']); store(i,'bias',op['bias']); store(i,'reference',ref)
    lines.append(' '.join([op['name']]+[str(op[f]) for f in fields]))
    if op['type']==0: macs+=op['oh']*op['ow']*op['oc']*op['kh']*op['kw']*op['ic']
    if op['type']==1: macs+=op['oh']*op['ow']*op['oc']*op['kh']*op['kw']
    if op['type']==5: macs+=op['ic']*op['oc']
(a.out/'plan.txt').write_text(str(len(lines))+'\n'+'\n'.join(lines)+'\n')
report={'architecture':'MobileNetV2','alpha':a.alpha,'input_size':a.size,'classes':1000,'keras':keras.__version__,'ops':len(ops),'macs':macs,'keras_top5':[(int(i),float(prob[0,i])) for i in prob[0].argsort()[-5:][::-1]],'decoded_top5':[(s,n,float(v)) for s,n,v in keras.applications.mobilenet_v2.decode_predictions(prob,top=5)[0]],'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'atol':.003,'rtol':.003}
weights_name=f'mobilenet_v2_weights_tf_dim_ordering_tf_kernels_{a.alpha}_{a.size}.h5'
report['weights_url']='https://storage.googleapis.com/tensorflow/keras-applications/mobilenet_v2/'+weights_name
report['weights_sha256']=hashlib.sha256((Path(os.environ['KERAS_HOME'])/'models'/weights_name).read_bytes()).hexdigest()
report['sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(a.out.iterdir()) if p.is_file() and p.name!='export.json'}
(a.out/'export.json').write_text(json.dumps(report,indent=2)+'\n')
print('MOBILENET_EXPORT_PASS',json.dumps({k:v for k,v in report.items() if k!='sha256'}),flush=True)
