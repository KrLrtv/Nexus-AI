import tempfile, os, importlib, sys, unittest, unittest.mock
class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();os.environ['NEXUS_DB_PATH']=self.tmp.name+'/unit.sqlite'
        sys.modules.pop('authority',None)
        import authority
        self.auth=importlib.reload(authority)
    def tearDown(self):self.tmp.cleanup()
    def test_non_device_is_ignored(self):
        p=self.auth.request('torch_on');self.assertEqual(self.auth.consume('разрешаю '+p['code'],'browser')['state'],'ignored')
    def test_wrong_and_expired_replay(self):
        p=self.auth.request('torch_on');self.assertEqual(self.auth.consume('разрешаю 0000')['state'],'wrong_phrase')
        with unittest.mock.patch('agent.execute',return_value='OK') as run:
            self.assertEqual(self.auth.consume('разрешаю '+p['code'])['state'],'executed')
            self.assertNotEqual(self.auth.consume('разрешаю '+p['code'])['state'],'executed');run.assert_called_once()
    def test_needs_button_for_sensitive(self):
        p=self.auth.request('memory_delete','15')
        self.assertEqual(self.auth.consume('разрешаю '+p['code'])['state'],'await_button')
        with unittest.mock.patch('agent.execute',return_value='OK'):
            self.assertEqual(self.auth.button_confirm()['state'],'executed')
    def test_stop_cancels(self):
        p=self.auth.request('torch_off');self.auth.cancel_all();self.assertIsNone(self.auth.current())
if __name__=='__main__':unittest.main()
