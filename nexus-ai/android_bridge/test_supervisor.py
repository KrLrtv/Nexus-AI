import tempfile,unittest,sqlite3,zipfile,hashlib
from pathlib import Path
from recovery_tools import inspect_release,stage_release
from notification_engine import summarize
from supervisor import restore_corrupt_db
from unittest.mock import patch
class CompletionTests(unittest.TestCase):
 def test_release_check(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); z=root/'release.zip'
   with zipfile.ZipFile(z,'w') as ar:ar.writestr('app.py','x=1')
   digest=hashlib.sha256(z.read_bytes()).hexdigest()
   self.assertEqual(inspect_release(z,digest),1)
   stage_release(z,root/'staged',digest)
   self.assertTrue((root/'staged'/'app.py').exists())
 def test_reject_traversal(self):
  with tempfile.TemporaryDirectory() as d:
   z=Path(d)/'bad.zip'
   with zipfile.ZipFile(z,'w') as ar:ar.writestr('../oops','x')
   with self.assertRaises(ValueError):inspect_release(z,hashlib.sha256(z.read_bytes()).hexdigest())
 def test_notifications_local(self):
  result=summarize([{'title':'Срочно!','packageName':'mail'},{'title':'Распродажа','packageName':'shop'}])
  self.assertEqual(result['items'][0]['priority'],'high')
 def test_restore(self):
  import supervisor
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d); db=folder/'db.sqlite3'; shots=folder/'backups';shots.mkdir()
   good=shots/'agent-20260101.sqlite3'
   with sqlite3.connect(good) as con:con.execute('CREATE TABLE t (x INTEGER)')
   db.write_bytes(b'not a db')
   with patch.object(supervisor,'DB',db),patch.object(supervisor,'SNAP',shots):
    self.assertTrue(supervisor.restore_corrupt_db())
    self.assertTrue(supervisor.integrity(db))
if __name__=='__main__':unittest.main()
