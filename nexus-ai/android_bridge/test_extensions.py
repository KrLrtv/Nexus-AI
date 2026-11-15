import os, tempfile, unittest, importlib
from pathlib import Path
os.environ['NEXUS_DB_PATH']=str(Path(tempfile.mkdtemp())/'test.sqlite3')
import extensions
class ExtensionsTest(unittest.TestCase):
    def test_valid(self):
        item=extensions.validate({'name':'daily_info','description':'Daily briefing','steps':[{'tool':'battery'},{'tool':'notes_list'}]})
        self.assertEqual(len(item['steps']),2)
    def test_reject_shell(self):
        with self.assertRaises(ValueError):extensions.validate({'name':'run_shell','description':'x','steps':[{'tool':'shell','argument':'rm -rf /'}]})
    def test_reject_side_effect(self):
        with self.assertRaises(ValueError):extensions.validate({'name':'torch_action','description':'x','steps':[{'tool':'torch_on'}]})
    def test_reject_many(self):
        with self.assertRaises(ValueError):extensions.validate({'name':'many_steps','description':'x','steps':[{'tool':'battery'}]*10})
    def test_approval(self):
        with extensions.db_connect() as db:
            recipe={'name':'daily_info','description':'test','steps':[{'tool':'notes_list'}]}
            cur=db.execute('INSERT INTO extension_proposals (name,intent,description,recipe,status,reason,created) VALUES (?,?,?,?,?,?,?)',('daily_info','test','test',__import__('json').dumps(recipe),'pending','',0))
            identifier=cur.lastrowid
        self.assertEqual(extensions.run(identifier)[1],404)
        self.assertEqual(extensions.decide(identifier,True)[1],200)
        self.assertEqual(extensions.run(identifier)[1],200)
if __name__=='__main__':unittest.main()
