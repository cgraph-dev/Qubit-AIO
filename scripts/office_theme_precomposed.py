import json, hashlib, os, shutil
from pathlib import Path
from PIL import Image

SRC=Path('/home/trick/Desktop/UI Office/moderninteriors-win/2_Characters/Old/Single_Characters_Legacy/16x16')
LIVE=Path('/home/trick/.local/state/qubit-voicebridge/runtime/office/themes/limezu')
OUT=Path('/tmp/qubit-precomposed-pack')
if OUT.exists(): shutil.rmtree(OUT)
shutil.copytree(LIVE, OUT)

# discover names with the core sheets
names=[]
for p in sorted(SRC.glob('*_idle_anim_16x16.png')):
    name=p.name[:-len('_idle_anim_16x16.png')]
    if (SRC/f'{name}_run_16x16.png').exists() and (SRC/f'{name}_sit_16x16.png').exists():
        names.append(name)
print('precomposed characters', len(names))
agents=json.load(open(OUT/'agents.json'))['recipes']
slugs=list(agents.keys())
# stable map persona -> precomposed character
def pick(slug):
    h=int(hashlib.sha256(slug.encode()).hexdigest(),16)
    return names[h%len(names)]
persona_char=[pick(s) for s in slugs]

DIRS=['left','up','right','down']
SHEET_GROUP={'left':2,'up':1,'right':3,'down':0}
CLIPS=[
 ('idle','_idle_anim_16x16.png',6,4),
 ('walk','_run_16x16.png',6,8),
 ('sit','_sit_16x16.png',6,2),
 ('talk','_phone_16x16.png',2,4),
 ('read','_reading_16x16.png',2,4),
]

cells={s:i for i,s in enumerate(slugs)}
rows=len(slugs)
# static atlas 20 cols
cols=20
static=Image.new('RGBA',(cols*16,15*32),(0,0,0,0))
clips_doc={}
for idx,slug in enumerate(slugs):
    name=persona_char[idx]
    im=Image.open(SRC/f'{name}_idle_anim_16x16.png').convert('RGBA')
    frame=im.crop((0,0,16,32))
    static.alpha_composite(frame,((idx%cols)*16,(idx//cols)*32))
static.save(OUT/'characters'/'atlas16.webp','WEBP',lossless=True,quality=100)

for clip,pat,frames,fps in CLIPS:
    atlas=Image.new('RGBA',(4*frames*16,rows*32),(0,0,0,0))
    for idx,slug in enumerate(slugs):
        name=persona_char[idx]
        path=SRC/f'{name}{pat}'
        if not path.exists():
            path=SRC/f'{name}_sit_16x16.png'
        im=Image.open(path).convert('RGBA')
        available=im.width//16
        for di,d in enumerate(DIRS):
            g=SHEET_GROUP[d]
            for f in range(frames):
                src_col=g*frames+f
                if src_col>=available:
                    src_col=available-1
                cell=im.crop((src_col*16,0,src_col*16+16,32))
                atlas.alpha_composite(cell,((di*frames+f)*16,idx*32))
    atlas.save(OUT/'characters'/f'{clip}.webp','WEBP',lossless=True,quality=100)
    clips_doc[clip]={'atlas':f'characters/{clip}.webp','frames':frames,'fps':fps,'cols':4*frames,'rows':rows}

theme=json.load(open(OUT/'theme.json'))
theme['characters']={
    'atlas':'characters/atlas16.webp','cell':{'w':16,'h':32},'cols':cols,'rows':15,
    'count':rows,'anchor':{'x':8,'y':32},'scale':1.0,
    'animations':{'schema':1,'cell':{'w':16,'h':32},'directions':DIRS,'cells':cells,'clips':clips_doc,'scale':1}
}
# update files map for the generated files
for rel in ['characters/atlas16.webp']+[f'characters/{c[0]}.webp' for c in CLIPS]:
    data=(OUT/rel).read_bytes()
    theme.setdefault('files',{})[rel]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
json.dump(theme,open(OUT/'theme.json','w'),indent=2,sort_keys=True); open(OUT/'theme.json','a').write('\n')
print('built', OUT, 'clips', {k:v['frames'] for k,v in clips_doc.items()})
