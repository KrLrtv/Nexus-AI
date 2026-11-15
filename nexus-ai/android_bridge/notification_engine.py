"""Local privacy-first notification classification; never sends body to cloud."""
import re
URGENT=('срочно','важно','ошибка','тревога','urgent','critical','failed','alarm','security')
SILENT=('реклама','скидка','акция','sale','promotion','advertisement')
def classify(item):
    title=str(item.get('title',''))[:120]; package=str(item.get('packageName',item.get('app','')))[:100]
    searchable=(title+' '+package).casefold()
    if any(w in searchable for w in URGENT):priority='high'
    elif any(w in searchable for w in SILENT):priority='low'
    else:priority='normal'
    return {'app':package,'title':title,'priority':priority}
def summarize(items, max_count=12):
    ranked=[classify(x) for x in items if isinstance(x,dict)]
    ranked.sort(key=lambda x:{'high':0,'normal':1,'low':2}[x['priority']])
    counts={p:sum(x['priority']==p for x in ranked) for p in ('high','normal','low')}
    return {'count':len(ranked),'counts':counts,'items':ranked[:max_count]}
