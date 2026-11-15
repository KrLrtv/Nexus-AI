"""RSS discovery from hardcoded feeds, no user URLs or open proxy. Feed content is untrusted."""
import os,urllib.request,xml.etree.ElementTree as ET,time,re
from core10 import connect,LOCK,log,enqueue
FEEDS={
 'science':'https://www.sciencedaily.com/rss/top/science.xml',
 'technology':'https://feeds.bbci.co.uk/news/technology/rss.xml',
 'world':'https://feeds.bbci.co.uk/news/world/rss.xml'
}
def clean(s):return re.sub(r'\s+',' ',re.sub(r'<[^>]+>','',s or ''))[:210]
def scan(topic='technology',timeout=6):
    if topic not in FEEDS:raise ValueError('Allowed topics: technology, science, world')
    req=urllib.request.Request(FEEDS[topic],headers={'User-Agent':'NEXUSAI/1.0 (personal RSS reader)'})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        data=response.read(250000)
    root=ET.fromstring(data)
    results=[]
    with LOCK,connect() as db:
        for item in root.findall('.//item')[:12]:
            title=clean(item.findtext('title'))
            link=(item.findtext('link') or '')[:250]
            if not title or not link.startswith('https://'):continue
            key=topic+':'+link
            exists=db.execute('SELECT 1 FROM feed_seen WHERE key=?',(key,)).fetchone()
            if not exists:
                db.execute('INSERT INTO feed_seen VALUES(?,?)',(key,int(time.time())))
                results.append({'title':title,'url':link})
    if results:enqueue('research',{'topic':topic,'new':len(results),'titles':[x['title'] for x in results[:3]]})
    log('research','ok',f'{topic}: {len(results)} new')
    return results
