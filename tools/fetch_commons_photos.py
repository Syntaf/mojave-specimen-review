import json, urllib.request, os, time, sys
UA="MojaveYardReview/1.0 (personal garden planning tool; gmercer015@gmail.com)"
src=json.load(open('final_sources.json'))
jobs=[]
for pid,arr in src.items():
    for i,im in enumerate(arr):
        jobs.append((im['dl'], f"orig/{pid}_{i}.img"))
todo=[j for j in jobs if not (os.path.exists(j[1]) and os.path.getsize(j[1])>2000)]
print(f"todo {len(todo)} of {len(jobs)}", flush=True)
fails=[]
for n,(url,fn) in enumerate(todo,1):
    delay=1.6
    for attempt in range(6):
        try:
            r=urllib.request.Request(url,headers={'User-Agent':UA})
            d=urllib.request.urlopen(r,timeout=120).read()
            if len(d)<2000: raise ValueError("tiny %d"%len(d))
            open(fn,'wb').write(d); print(f"[{n}/{len(todo)}] ok {fn} {len(d)//1024}KB",flush=True); break
        except Exception as e:
            code=getattr(e,'code',None)
            if attempt==5: fails.append((fn,str(e)[:90])); print(f"[{n}] FAIL {fn} {e}",flush=True); break
            time.sleep(delay*(3 if code==429 else 1)); delay*=1.8
    time.sleep(1.5)
json.dump(fails,open('dl_fails.json','w'),indent=1)
print("DONE. fails:",len(fails),flush=True)
