import unittest,tempfile,sqlite3
from pathlib import Path
from notification_engine import classify,summarize
from recovery import backup,restore,integrity
from session import VoiceSession
class CompletionTests(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(classify({'title':'СРОЧНО: ошибка','app':'demo'})['priority'],'high')
        self.assertEqual(classify({'title':'Скидка','app':'demo'})['priority'],'low')
    def test_notification_no_body(self):
        item=summarize([{'title':'Hello','content':'secret','packageName':'app'}])['items'][0]
        self.assertNotIn('content',item)
    def test_db_backup_restore(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'agent.db'
            with sqlite3.connect(db) as c:c.execute('CREATE TABLE x(v INTEGER)');c.execute('INSERT INTO x VALUES(1)')
            snap=backup(db,Path(d)/'backups');self.assertTrue(integrity(snap))
            with sqlite3.connect(db) as c:c.execute('DELETE FROM x')
            with self.assertRaises(PermissionError):restore(db,snap)
            restore(db,snap,stopped=True)
            with sqlite3.connect(db) as c:self.assertEqual(c.execute('SELECT v FROM x').fetchone()[0],1)
    def test_session(self):
        s=VoiceSession(max_turns=2);s.start();self.assertTrue(s.consume());self.assertTrue(s.consume());self.assertFalse(s.consume())
if __name__=='__main__':unittest.main()
