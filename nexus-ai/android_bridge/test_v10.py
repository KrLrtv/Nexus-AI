import os,tempfile,unittest
TMP=tempfile.TemporaryDirectory()
os.environ['NEXUS_DB_PATH']=TMP.name+'/test.db'
os.environ['NEXUS_EVOLUTION_DIR']=TMP.name+'/drafts'
from core10 import node,edge,graph_search,add_goal,add_task,goals,task_done,set_permission,permission,enqueue,get_events,ack_events
from evolution10 import stage,static_review
class V10Tests(unittest.TestCase):
 def test_graph(self):
  a=node('project','My redactor');b=node('device','ESP32');edge(a,b,'uses');self.assertEqual(graph_search('redactor')[0]['links'][0]['to'],b)
 def test_goals(self):
  a=add_goal('Project');b=add_task(a,'Prototype');self.assertTrue(task_done(b));self.assertEqual(goals()[0]['tasks'][0]['status'],'done')
 def test_grants(self):
  self.assertFalse(permission('torch_on'));set_permission('torch_on',True);self.assertTrue(permission('torch_on'))
  with self.assertRaises(ValueError):set_permission('install_code',True)
 def test_event_ack(self):
  enqueue('info',{'a':2});items=get_events();self.assertTrue(items);ack_events([items[-1]['id']]);self.assertFalse(any(x['id']==items[-1]['id'] for x in get_events()))
 def test_source_never_executed(self):
  self.assertTrue(static_review('import subprocess\ndef f():return 1'))
  d=stage('counter','demo','def count(x):\n    return x + 1\n');self.assertEqual(d['status'],'draft_never_executed')
if __name__=='__main__':unittest.main()
