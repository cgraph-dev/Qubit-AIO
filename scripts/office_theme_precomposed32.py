import json, hashlib, shutil
from pathlib import Path
from PIL import Image

SRC=Path('/home/trick/Desktop/UI Office/moderninteriors-win/2_Characters/Old/Single_Characters_Legacy/32x32')
LIVE=Path('/home/trick/.local/state/qubit-voicebridge/runtime/office/themes/limezu')
OUT=Path('/tmp/qubit-precomposed32-pack')
if OUT.exists(): shutil.rmtree(OUT)
shutil.copytree(LIVE, OUT)

names=[]
for p in sorted(SRC.glob('*_idle_anim_32x32.png')):
    n=p.name[:-len('_idle_anim_32x32.png')]
    if (SRC/f'{n}_run_32x32.png').exists() and (SRC/f'{n}_sit_32x32.png').exists():
        names.append(n)
print('complete 32px characters',len(names),names)
agents=json.load(open(OUT/'agents.json'))['recipes']; slugs=list(agents)
def pick(slug):
    return names[int(hashlib.sha256(slug.encode()).hexdigest(),16)%len(names)]
persona=[pick(s) for s in slugs]

DIRS=['left','up','right','down']; GROUP={'left':2,'up':1,'right':3,'down':0}
CLIPS=[('idle','_idle_anim_32x32.png',6,4),('walk','_run_32x32.png',6,8),('sit','_sit_32x32.png',6,2),('talk','_phone_32x32.png',2,4),('read','_reading_32x32.png',4,4)]
CELL_W,CELL_H=32,64; SCALE=0.75
rows=len(names); cols=len(names)
static=Image.new('RGBA',(cols*CELL_W,CELL_H),(0,0,0,0))
for i,name in enumerate(names):
    im=Image.open(SRC/f'{name}_idle_anim_32x32.png').convert('RGBA')
    static.alpha_composite(im.crop((0,0,CELL_W,CELL_H)),(i*CELL_W,0))
static.save(OUT/'characters'/'atlas32.webp','WEBP',lossless=True,quality=100)

clips={}
for clip,pat,frames,fps in CLIPS:
    atlas=Image.new('RGBA',(4*frames*CELL_W,rows*CELL_H),(0,0,0,0))
    for i,name in enumerate(names):
        path=SRC/f'{name}{pat}'
        if not path.exists(): path=SRC/f'{name}_sit_32x32.png'
        im=Image.open(path).convert('RGBA'); avail=im.width//CELL_W
        for di,d in enumerate(DIRS):
            g=GROUP[d]
            for f in range(frames):
                c=g*frames+f
                if c>=avail: c=avail-1
                atlas.alpha_composite(im.crop((c*CELL_W,0,c*CELL_W+CELL_W,CELL_H)),((di*frames+f)*CELL_W,i*CELL_H))
    atlas.save(OUT/'characters'/f'{clip}.webp','WEBP',lossless=True,quality=100)
    clips[clip]={'atlas':f'characters/{clip}.webp','frames':frames,'fps':fps,'cols':4*frames,'rows':rows}

theme=json.load(open(OUT/'theme.json'))
theme['characters']={
  'atlas':'characters/atlas32.webp','cell':{'w':CELL_W,'h':CELL_H},'cols':cols,'rows':1,'count':rows,
  'anchor':{'x':CELL_W//2,'y':CELL_H},'scale':SCALE,
  'animations':{'schema':1,'cell':{'w':CELL_W,'h':CELL_H},'directions':DIRS,
                'cells':{s:(hashlib.sha256(s.encode()).digest()[0]%len(names)) for s in slugs},'clips':clips,
                'anchor':{'x':CELL_W//2,'y':CELL_H},'scale':SCALE}
}
for rel in ['characters/atlas32.webp']+[f'characters/{c[0]}.webp' for c in CLIPS]:
    data=(OUT/rel).read_bytes(); theme['files'][rel]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
json.dump(theme,open(OUT/'theme.json','w'),indent=2,sort_keys=True); open(OUT/'theme.json','a').write('\n')
print('built',OUT,'scale',SCALE,'clips',{k:v['frames'] for k,v in clips.items()})
