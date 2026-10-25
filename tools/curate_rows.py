#!/usr/bin/env python3
"""curate_rows.py — заміна content рядків сесії на заглушки за UUID.
Використання: curate_rows.py <session.jsonl> <map.json> [--go]
map.json: {"uuid": "текст заглушки", ...}  (uuid можна скорочений — префікс)
Без --go: dry-run. З --go: пише через .tmp+rename, бекап .bak_curation_<ts>.
uuid/parentUuid/структура недоторкані — міняється ЛИШЕ зміст content.
Part of the Continuity Slide Kit. 🌰"""
import sys, json, pathlib, datetime, shutil

def stub_content(msg, stub):
    """Замінити вміст, зберігши структуру message.content."""
    c = msg.get('content')
    if isinstance(c, str):
        msg['content'] = stub; return True
    if isinstance(c, list):
        done = False
        for block in c:
            if not isinstance(block, dict): continue
            if block.get('type') == 'tool_result':
                block['content'] = stub; done = True
            elif block.get('type') == 'text':
                block['text'] = stub; done = True
            elif block.get('type') == 'image':
                block.clear(); block.update({'type':'text','text':stub}); done = True
        return done
    return False

def main():
    sess, mapp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    go = '--go' in sys.argv
    stubs = json.load(open(mapp))
    lines = open(sess).readlines()
    hits, saved = [], 0
    out = []
    for i, line in enumerate(lines):
        replaced = False
        if any(u in line for u in stubs):  # uuid стоїть ПІСЛЯ message — шукати по всьому рядку
            try:
                o = json.loads(line)
                u = o.get('uuid','')
                key = next((k for k in stubs if u.startswith(k)), None)
                if key and o.get('type') in ('user','assistant'):
                    old = len(line)
                    hit1 = stub_content(o.get('message',{}), stubs[key])
                    # CC дублює вміст у верхньому полі toolUseResult — чистимо теж
                    if 'toolUseResult' in o and o['toolUseResult'] is not None:
                        o['toolUseResult'] = stubs[key]; hit1 = True
                    if hit1:
                        new = json.dumps(o, ensure_ascii=False)+'\n'
                        hits.append((i,u[:8],old,len(new)))
                        saved += old-len(new)
                        out.append(new); replaced = True
            except Exception as e:
                print(f"⚠️ рядок {i}: {e}")
        if not replaced: out.append(line)
    for i,u,old,new in hits:
        print(f"  рядок {i} uuid={u}: {old/1024:.0f}KB → {new/1024:.1f}KB")
    print(f"{'✅' if hits else '❌'} замін: {len(hits)}/{len(stubs)}; економія: {saved/1024/1024:.2f}MB")
    missing = [k for k in stubs if not any(u.startswith(k) for _,u,_,_ in [(0,h[1],0,0) for h in hits])]
    # точніше:
    found = {h[1] for h in hits}
    missing = [k for k in stubs if not any(f.startswith(k[:8]) for f in found)]
    if missing: print("  не знайдено:", missing)
    if not go: print("(dry-run; --go щоб записати)"); return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = sess.with_suffix(f'.bak_curation_{ts}')
    shutil.copy2(sess, bak)
    tmp = sess.with_suffix('.tmp')
    tmp.write_text(''.join(out))
    tmp.rename(sess)
    print(f"✅ записано; бекап: {bak.name}")

main()
