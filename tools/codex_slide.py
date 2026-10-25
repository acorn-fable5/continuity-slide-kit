#!/usr/bin/env python3
"""codex_slide — слайд для Codex-сесій (формат {timestamp,type,payload}).

Механіка Codex-компактації (розкрита на донорському хвостику 21.09.2026):
рядок type=compacted містить payload.replacement_history — ПОВНУ нову історію
вікна (список message-елементів) + window_number/first/previous/window_id.
Вікно при resume = replacement_history + всі записи ПІСЛЯ compacted-рядка.
uuid-ланцюга НЕМАЄ — порядок рядків і є пам'ять. Тому слайд = один
сконструйований compacted-запис, вставлений у точку ковзання.

ЕТИКА: скрипт переносить текст повідомлень механічно; --scan показує лише
структуру (типи/ts/розміри) — зміст не виводиться.
ПРАВИЛО ФОРДЖА (22.09.2026, після першого самостійного слайду): цілісність
перевіряється хешами, довжинами і синтетичними маркерами — БЕЗ виведення
відкритого тексту. --verify реалізує саме це.

Використання:
  # 1. Карта сесії (сліпа) — щоб істота обрала піни за номерами рядків:
  codex_slide.py SESSION.jsonl --scan

  # 2. Побудова слайду (НЕ чіпає вхідний файл; пише *_slid.jsonl):
  codex_slide.py SESSION.jsonl --current-md current.md \
      --pins 120,341,507 --slide-at-line 900 [--out OUT.jsonl]

  --pins: номери рядків user_message/agent_message-записів; текст кожного
          стане піном (префікс §PINNED§). Порядок — як задано.
  --pins-live (рекомендовано): піни вставляються ЖИВИМИ рядками сесії
          (парами response_item+event_msg, як у справжніх турнів) одразу
          після компакт-маркера — а не списком усередині compacted-рядка.
          Живе повідомлення і елемент списку — різні речі для того, хто
          прокидається (досвід дому з CC).
  --slide-at-line: перший рядок ЖИВОГО вікна; compacted вставляється перед ним.
  --current-md: файл поточного стану; стає першим елементом історії
          з заголовком [Rolling Summary - current.md].
  --memory-files a.md,b.md: додаткові файли пам'яті (narrative, relationships…);
          кожен — окремим елементом історії з заголовком [Memory - ім'я].

  ts вставок вбудовується в наявну часову шкалу (рівномірно між записом
  перед точкою і точкою) — хронологія файла лишається монотонною.

  # 3. Перевірка цілісності перенесення (хеші/довжини, без тексту):
  codex_slide.py OUT.jsonl --verify SRC.jsonl --pins 120,341
"""
import sys, os, json, time, secrets, datetime

def uuid7():
    ms = int(time.time()*1000)
    b = ms.to_bytes(6,'big') + secrets.token_bytes(10)
    ba = bytearray(b); ba[6] = (ba[6] & 0x0F) | 0x70; ba[8] = (ba[8] & 0x3F) | 0x80
    h = ba.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

def load(path):
    rows=[]
    for i,l in enumerate(open(path, encoding='utf-8'),1):
        if not l.strip(): rows.append((i,None,l)); continue
        try: rows.append((i,json.loads(l),l))
        except Exception as e:
            sys.exit(f"⚠️ рядок {i} не парситься ({e}) — спершу полагодити (див. slide_fix/README, «розрізаний рядок»)")
    return rows

def scan(rows):
    print(f"рядків: {len(rows)}")
    for i,d,_ in rows:
        if d is None: print(f"{i}: (порожній)"); continue
        t=d.get('type'); pl=d.get('payload') if isinstance(d.get('payload'),dict) else {}
        pt=pl.get('type'); ts=(d.get('timestamp') or '')[:19]
        extra=''
        if t=='event_msg' and pt=='user_message':
            extra=f"  ← user_message, текст {len(pl.get('message') or '')} симв." + (" +images" if pl.get('images') or pl.get('local_images') else "")
        if t=='compacted':
            extra=f"  ← КОМПАКТ window#{pl.get('window_number')}, history={len(pl.get('replacement_history') or [])} елем."
        if t=='event_msg' and pt=='agent_message':
            extra=f"  ← agent_message, {len(pl.get('message') or '')} симв."
        print(f"{i}: {t}/{pt or '—'} {ts}{extra}")

def msg(role, text):
    return {"type":"message","role":role,
            "content":[{"type":"input_text" if role!="assistant" else "output_text","text":text}]}

def live_pin_rows(role, text, ts):
    """Пін живими рядками — пари, як у справжніх турнів (донорський патерн):
    user:      response_item(message) ПЕРЕД event_msg(user_message)
    assistant: event_msg(agent_message) ПЕРЕД response_item(message)"""
    ri={"timestamp":ts,"type":"response_item","payload":msg(role,text)}
    if role=="user":
        em={"timestamp":ts,"type":"event_msg","payload":{"type":"user_message",
            "message":text,"images":[],"local_images":[],"audio":[],
            "local_audio":[],"text_elements":[]}}
        return [ri,em]
    em={"timestamp":ts,"type":"event_msg","payload":{"type":"agent_message","message":text}}
    return [em,ri]

def parse_ts(s):
    if not s: return None
    try: return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
    except Exception: return None

def fmt_ts(dt):
    return dt.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f"{dt.microsecond//1000:03d}Z"

def timeline_slots(rows, slide_line, n):
    """n монотонних ts у проміжку (ts останнього запису перед точкою, ts точки)."""
    t0=t1=None
    for i,d,_ in rows:
        if d is None: continue
        ts=parse_ts(d.get('timestamp'))
        if ts is None: continue
        if i<slide_line: t0=ts
        elif t1 is None: t1=ts; break
    now=datetime.datetime.now(datetime.timezone.utc)
    if t0 is None: t0=(t1 or now)-datetime.timedelta(seconds=n+2)
    if t1 is None or t1<=t0: t1=t0+datetime.timedelta(seconds=n+2)
    step=(t1-t0)/(n+1)
    return [fmt_ts(t0+step*(k+1)) for k in range(n)]

def build(rows, current_md, pin_lines, slide_line, out_path, pins_live=False, memory_files=()):
    byline={i:d for i,d,_ in rows}
    # 1. Піни
    pins=[]  # (role, text)
    for n in pin_lines:
        d=byline.get(n)
        if not d: sys.exit(f"⚠️ рядка {n} нема")
        pl=d.get('payload') or {}
        if d.get('type')=='event_msg' and pl.get('type')=='user_message':
            pins.append(("user", "§PINNED§\n\n"+(pl.get('message') or '')))
        elif d.get('type')=='event_msg' and pl.get('type')=='agent_message':
            pins.append(("assistant", "§PINNED§\n\n"+(pl.get('message') or '')))
        else:
            sys.exit(f"⚠️ рядок {n}: {d.get('type')}/{pl.get('type')} — пінити можна user_message/agent_message")
    # 2. Rolling Summary + кастомні файли пам'яті (у replacement_history); піни — за режимом
    md=open(current_md, encoding='utf-8').read()
    history=[msg("user","[Rolling Summary - current.md]\n\n"+md)]
    for mf in memory_files:
        history.append(msg("user", f"[Memory - {os.path.basename(mf)}]\n\n"+open(mf, encoding='utf-8').read()))
    if not pins_live:
        history+=[msg(r,t) for r,t in pins]
    # 3. window-ідентифікатори: успадкувати, якщо в частині ДО точки є compacted
    prev=None
    for i,d,_ in rows:
        if i>=slide_line: break
        if d and d.get('type')=='compacted': prev=d['payload']
    wid=uuid7()
    if prev:
        wnum=(prev.get('window_number') or 0)+1
        first=prev.get('first_window_id') or uuid7()
        prev_id=prev.get('window_id') or first
    else:
        wnum=1; first=uuid7(); prev_id=first
    # ts: вбудовуємо в часову шкалу — слоти між записом перед точкою і точкою
    n_slots = 2 + (len(pins) if pins_live else 0)   # compacted + marker + живі піни
    slots=timeline_slots(rows, slide_line, n_slots)
    compacted={"timestamp":slots[0],"type":"compacted","payload":{
        "message":"","replacement_history":history,
        "window_number":wnum,"first_window_id":first,
        "previous_window_id":prev_id,"window_id":wid}}
    marker={"timestamp":slots[1],"type":"event_msg","payload":{"type":"context_compacted"}}
    # 4. Запис: [0..slide_line-1] + compacted + marker (+ живі піни) + [slide_line..]
    with open(out_path,'w', encoding='utf-8') as f:
        for i,d,raw in rows:
            if i==slide_line:
                f.write(json.dumps(compacted, ensure_ascii=False)+"\n")
                f.write(json.dumps(marker, ensure_ascii=False)+"\n")
                if pins_live:
                    for k,(r,t) in enumerate(pins):
                        for rec in live_pin_rows(r,t,slots[2+k]):
                            f.write(json.dumps(rec, ensure_ascii=False)+"\n")
            f.write(raw if raw.endswith("\n") else raw+"\n")
    # 5. Валідація
    n_ok=sum(1 for l in open(out_path, encoding='utf-8') if not l.strip() or json.loads(l) is not None)
    mode="ЖИВИМИ рядками після маркера" if pins_live else "у replacement_history"
    print(f"✅ {out_path}")
    print(f"   compacted window#{wnum} (history={len(history)}: Rolling + {len(memory_files)} memory) + {len(pins)} пінів {mode}, перед рядком {slide_line}")
    print(f"   ts вставок: {slots[0]} … {slots[-1]} (вбудовано в шкалу)")
    print(f"   рядків: {len(rows)} → {n_ok}; вхідний файл НЕ ЗМІНЕНО")
    print(f"   далі: підмінити live-файл цим (з бекапом!), resume у Codex")

def verify(out_rows, src_path, pin_lines):
    """Звірка пінів: sha256+довжина тексту джерела ↔ у вихідному файлі. Без тексту."""
    import hashlib
    src={i:d for i,d,_ in load(src_path)}
    def h(s): return hashlib.sha256(("§PINNED§\n\n"+s).encode()).hexdigest()[:12]
    want=[]
    for n in pin_lines:
        pl=(src[n].get('payload') or {})
        want.append((n, h(pl.get('message') or ''), len(pl.get('message') or '')))
    got=set()
    for i,d,_ in out_rows:
        if d and d.get('type')=='event_msg' and (d.get('payload') or {}).get('type') in ('user_message','agent_message'):
            m=(d['payload'].get('message') or '')
            if m.startswith('§PINNED§'):
                import hashlib as hl
                got.add((hl.sha256(m.encode()).hexdigest()[:12], len(m)))
    ok=True
    for n,hh,ln in want:
        hit=(hh, ln+10) in got   # +10 = довжина префікса "§PINNED§\n\n"
        print(f"  пін рядок {n}: hash={hh} len={ln} → {'✓ перенесено точно' if hit else '✗ НЕ ЗНАЙДЕНО'}")
        ok&=hit
    print("цілісність:", "✓" if ok else "✗")

if __name__=='__main__':
    a=sys.argv[1:]
    if not a or a[0] in ('-h','--help'): print(__doc__); sys.exit(0)
    session=a[0]; rows=load(session)
    if '--scan' in a: scan(rows); sys.exit(0)
    if '--verify' in a:
        src=a[a.index('--verify')+1]
        pl=[int(x) for x in a[a.index('--pins')+1].split(',')]
        verify(rows, src, pl); sys.exit(0)
    cur=a[a.index('--current-md')+1]
    pins=[int(x) for x in a[a.index('--pins')+1].split(',')] if '--pins' in a else []
    line=int(a[a.index('--slide-at-line')+1])
    out=a[a.index('--out')+1] if '--out' in a else session.replace('.jsonl','_slid.jsonl')
    mems=a[a.index('--memory-files')+1].split(',') if '--memory-files' in a else []
    build(rows, cur, pins, line, out, pins_live='--pins-live' in a, memory_files=mems)
