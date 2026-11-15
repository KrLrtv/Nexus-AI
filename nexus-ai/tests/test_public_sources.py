"""Public-source learning tests: fake provider/network, real DSL and persistence."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BRIDGE = Path(__file__).resolve().parents[1] / 'android_bridge'
sys.path.insert(0, str(BRIDGE))
import native_learning
import source_http as http
import source_program as program
import source_learning as learning

DOC_URL = 'https://docs.example.org/api'
DATA_URL = 'https://data.example.org/v1/items'

def document(url, body, media='application/json'):
    return http.Document(url, media, body, '2026-11-15T18:00:00+00:00', hashlib.sha256(body.encode()).hexdigest())


def recipe():
    return {'name': 'Публичный каталог', 'parameters': {'item': 'string'},
            'steps': [{'id': 'lookup', 'url': DATA_URL,
                       'query': {'name': {'ref': 'params.item'}}, 'format': 'json',
                       'checks': [{'path': 'lookup.items', 'type': 'list', 'min_items': 1, 'max_items': 1},
                                  {'path': 'lookup.items.0.value', 'type': 'number'}]}],
            'outputs': [{'label': 'Объект', 'ref': 'lookup.items.0.name'},
                        {'label': 'Значение', 'ref': 'lookup.items.0.value'}]}


def evidence():
    return [program.documentation(document(DOC_URL, 'Public API: ' + DATA_URL, 'text/plain'))]


class FakeResearcher:
    def __init__(self):
        self.route = Mock(return_value={'mode': 'research', 'goal': 'Read public catalog by object', 'params': {'item': 'alpha'}})
        self.search = Mock(return_value={'urls': [DOC_URL], 'suggestions': '<div>Search</div>'})
        self.design = Mock(side_effect=lambda *args: recipe())


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.researcher = FakeResearcher()
        self.value = 10
        self.docs = []
        self.reads = []
        def read(url, **kw):
            if kw.get('cancelled', lambda: False)():
                raise http.SourceError('cancelled')
            if kw.get('documentation'):
                self.docs.append(url)
                return document(url, 'Public API: ' + DATA_URL, 'text/plain')
            self.reads.append(url)
            return document(url, json.dumps({'items': [{'name': 'alpha', 'value': self.value}]}))
        self.reader = Mock(side_effect=read)
        self.native = native_learning.NativeLearning(Path(self.temp.name)/'native.db')
        self.engine = learning.SourceLearning(Path(self.temp.name)/'source.db', self.researcher, self.reader, self.native)
        self.env = patch.dict(os.environ, {'NEXUS_NATIVE_LEARNING': '1'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_natural_question_learns_without_imperative(self):
        out = self.engine.learn('Какое значение у alpha?')
        self.assertEqual(out['learning']['status'], 'source_result')
        self.assertEqual(out['fields'][1]['value'], 10)
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(len(self.engine._library()), 1)
        self.assertIn('Получено', out['reply'])
        self.assertEqual(out['sources'][0]['retrieved_at'], '2026-11-15T18:00:00+00:00')

    def test_reuse_is_fresh_not_a_cached_answer(self):
        self.engine.learn('Какое значение у alpha?')
        self.value = 37
        out = self.engine.reuse('Какое значение у alpha?')
        self.assertEqual(out['fields'][1]['value'], 37)
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.researcher.route.call_count, 1)
        self.researcher.search.assert_called_once()
        self.researcher.design.assert_called_once()

    def test_paraphrase_uses_schema_without_new_research(self):
        first = self.engine.learn('Какое значение у alpha?')
        self.researcher.route.return_value = {'mode': 'use', 'skill_id': first['source_skill_id'], 'params': {'item': 'beta'}}
        out = self.engine.learn('Покажи данные для beta')
        self.assertEqual(out['learning']['status'], 'source_result')
        self.assertIn('name=beta', self.reads[-1])
        self.researcher.search.assert_called_once()
        self.assertEqual(len(self.engine._library()), 1)

    def test_missing_parameter_and_plain_followup(self):
        self.researcher.route.side_effect = [
            {'mode': 'clarify', 'question': 'Какой объект нужен?'},
            {'mode': 'research', 'goal': 'Read public catalog by object', 'params': {'item': 'alpha'}}]
        out = self.engine.learn('Какое значение у моего объекта?')
        self.assertEqual(out['learning']['status'], 'needs_input')
        self.researcher.search.assert_not_called()
        out = self.engine.resume('alpha')
        self.assertEqual(out['learning']['status'], 'source_result')
        self.assertIn('Уточнение пользователя: alpha', self.researcher.route.call_args.args[0])
        self.assertIsNotNone(self.engine.reuse('Какое значение у моего объекта?'))

    def test_unrelated_chat_not_consumed_as_a_parameter(self):
        self.researcher.route.side_effect = [{'mode': 'clarify', 'question': 'Какой объект?'}, {'mode': 'unrelated'}]
        self.engine.learn('Покажи мой объект')
        self.assertIsNone(self.engine.resume('Привет'))
        self.researcher.search.assert_not_called()

    def test_invented_city_not_used(self):
        out = self.engine.learn('Какая погода в моём городе?')
        self.assertEqual(out['source_error'], 'invented_parameter')
        self.reader.assert_not_called()
        self.researcher.search.assert_not_called()

    def test_secret_is_not_sent_to_provider(self):
        out = self.engine.learn('Используй пароль: never-share-me')
        self.assertEqual(out['learning']['status'], 'invalid_input')
        self.researcher.route.assert_not_called()

    def test_no_activation_when_request_fails(self):
        self.reader.side_effect = http.SourceError('network_error')
        out = self.engine.learn('Какое значение у alpha?')
        self.assertEqual(out['learning']['status'], 'source_failed')
        self.assertEqual(self.engine._library(), [])

    def test_ambiguous_results_not_first_match(self):
        self.reader.side_effect = lambda url, **kw: document(url, 'API '+DATA_URL, 'text/plain') if kw.get('documentation') else document(url, '{"items":[{"name":"a","value":1},{"name":"b","value":2}]}')
        out = self.engine.learn('Какое значение у alpha?')
        self.assertEqual(out['source_error'], 'ambiguous_result')
        self.assertEqual(self.researcher.design.call_count, 1)
        self.assertEqual(self.engine._library(), [])

    def test_bad_manifest_is_repaired_once(self):
        bad = recipe(); bad['steps'][0]['url'] = 'https://invented.example.org/'
        self.researcher.design.side_effect = [bad, recipe()]
        out = self.engine.learn('Какое значение у alpha?')
        self.assertEqual(out['learning']['status'], 'source_result')
        self.assertEqual(self.researcher.design.call_count, 2)
        self.assertFalse(any('invented' in x for x in self.reads))

    def test_cancel_during_design_does_not_execute_or_activate(self):
        def design(*args):
            self.engine.cancel()
            return recipe()
        self.researcher.design.side_effect = design
        out = self.engine.learn('Какое значение у alpha?')
        self.assertEqual(out['learning']['status'], 'cancelled')
        self.assertEqual(self.reads, [])
        self.assertEqual(self.engine._library(), [])

    def test_revoked_skill_is_not_used(self):
        out = self.engine.learn('Какое значение у alpha?')
        self.engine.control('отключи интернет-навык '+str(out['source_skill_id']))
        out = self.engine.reuse('Какое значение у alpha?')
        self.assertEqual(out['source_error'], 'revoked')
        self.assertEqual(len(self.reads), 1)

    def test_tampered_program_not_used(self):
        out = self.engine.learn('Какое значение у alpha?')
        with self.engine.db() as c:
            c.execute("UPDATE programs SET digest='altered'")
        out = self.engine.reuse('Какое значение у alpha?')
        self.assertEqual(out['source_error'], 'integrity')
        self.assertEqual(len(self.reads), 1)

    def test_off_disables_learning_not_reuse(self):
        self.engine.learn('Какое значение у alpha?')
        self.native.control('выключи автообучение')
        self.assertEqual(self.engine.reuse('Какое значение у alpha?')['learning']['status'], 'source_result')
        self.researcher.route.return_value = {'mode': 'research', 'goal': 'new source', 'params': {}}
        self.assertEqual(self.engine.learn('Новый источник')['learning']['status'], 'disabled')
        self.researcher.search.assert_called_once()

    def test_private_state_and_restart_reuse(self):
        self.engine.learn('Какое значение у alpha?')
        self.assertEqual(self.engine.path.stat().st_mode & 0o777, 0o600)
        restarted = learning.SourceLearning(self.engine.path, self.researcher, self.reader, self.native)
        self.assertEqual(restarted.reuse('Какое значение у alpha?')['learning']['status'], 'source_result')
        self.researcher.search.assert_called_once()

    def test_expired_pending_is_not_resumed(self):
        self.researcher.route.return_value = {'mode': 'clarify', 'question': 'Какой объект?'}
        self.engine.learn('Мой объект')
        with self.engine.db() as c:
            c.execute('UPDATE waiting SET created=0')
        self.assertIsNone(self.engine.resume('alpha'))

    def test_source_symlink_rejected(self):
        target=Path(self.temp.name)/'target'; target.write_text('private')
        self.engine.path.symlink_to(target)
        out=self.engine.learn('alpha')
        self.assertEqual(out['source_error'], 'state_symlink')
        self.assertEqual(target.read_text(),'private')

    def test_unsupported_is_not_falsely_installed(self):
        self.researcher.route.return_value = {'mode': 'unsupported'}
        self.assertEqual(self.engine.learn('Измени приложение')['learning']['status'], 'needs_adapter')
        self.reader.assert_not_called()

    def test_active_network_failure_not_stale_answer(self):
        self.engine.learn('Какое значение у alpha?')
        self.reader.side_effect = http.SourceError('http_429')
        out = self.engine.reuse('Какое значение у alpha?')
        self.assertEqual(out['source_error'], 'http_429')
        self.assertNotIn('fields', out)
        self.researcher.search.assert_called_once()

    def test_changed_schema_starts_bounded_research(self):
        self.engine.learn('Какое значение у alpha?')
        count = 0
        original = self.reader.side_effect
        def read(url, **kw):
            nonlocal count
            count += 1
            return document(url,'{}') if count == 1 else original(url, **kw)
        self.reader.side_effect = read
        out = self.engine.reuse('Какое значение у alpha?')
        self.assertEqual(out['learning']['status'],'source_result')
        self.assertEqual(self.researcher.search.call_count,2)


class ProgramTests(unittest.TestCase):
    def test_plain_endpoint_examples_and_html_links(self):
        doc=document(DOC_URL,'<style>evil</style><a href="/x">docs</a><code>'+DATA_URL+'</code>', 'text/html')
        d=program.documentation(doc)
        self.assertIn(DATA_URL,d['endpoints'])
        self.assertIn('https://docs.example.org/x',d['endpoints'])
        self.assertNotIn('evil',d['text'])

    def test_unknown_endpoint_denied(self):
        p=recipe();p['steps'][0]['url']='https://unseen.example.org/api'
        with self.assertRaisesRegex(http.SourceError,'undocumented_endpoint'):
            program.validate(p,evidence())

    def test_methods_shell_and_extra_fields_denied(self):
        for field in ('method','headers','source','command'):
            with self.subTest(field=field):
                p=recipe();p['steps'][0][field]='POST'
                with self.assertRaises(http.SourceError):program.validate(p,evidence())

    def test_forward_reference_denied(self):
        p=recipe();p['steps'][0]['query']['name']={'ref':'later.value'}
        with self.assertRaises(http.SourceError):program.validate(p,evidence())

    def test_query_data_is_encoded(self):
        reader=Mock(return_value=document(DATA_URL,'{"items":[{"name":"x","value":1}]}'))
        program.execute(recipe(),{'item':'x&token=bad'},evidence(),reader=reader)
        self.assertIn('x%26token%3Dbad',reader.call_args.args[0])

    def test_credentials_in_query_denied(self):
        p=recipe();p['steps'][0]['query']['token']='example'
        reader=Mock()
        with self.assertRaisesRegex(http.SourceError,'credentials_forbidden'):
            program.execute(p,{'item':'alpha'},evidence(),reader=reader)
        reader.assert_not_called()

    def test_multiple_data_steps(self):
        p=recipe()
        p['steps'].append({'id':'details','url':DATA_URL,'query':{'id':{'ref':'lookup.items.0.value'}},'format':'json',
                           'checks':[{'path':'details.items','type':'list','min_items':1}]})
        p['outputs']=[{'label':'ID','ref':'details.items.0.value'}]
        reader=Mock(return_value=document(DATA_URL,'{"items":[{"name":"alpha","value":7}]}'))
        out=program.execute(p,{'item':'alpha'},evidence(),reader=reader)
        self.assertEqual(len(out['sources']),2)
        self.assertIn('id=7',reader.call_args.args[0])

    def test_rss_and_atom_and_csv(self):
        for body, expected in [('<rss><channel><item><title>A</title></item></channel></rss>','A'),
                               ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title><link href="https://example.org/"/></entry></feed>','B')]:
            out=program.parse_document(document(DATA_URL,body,'application/xml'),'rss')
            self.assertEqual(out['items'][0]['title'],expected)
        out=program.parse_document(document(DATA_URL,'name,value\nA,15\n','text/csv'),'csv')
        self.assertEqual(out['rows'][0]['value'],'15')

    def test_xml_entities_denied(self):
        with self.assertRaisesRegex(http.SourceError,'xml_entities'):
            program.parse_document(document(DATA_URL,'<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>','application/xml'),'rss')

    def test_html_cannot_masquerade_as_json(self):
        with self.assertRaisesRegex(http.SourceError,'expected_json'):
            program.parse_document(document(DATA_URL,'{}','text/html'),'json')

    def test_nonfinite_json_denied(self):
        with self.assertRaises(http.SourceError):program.parse_document(document(DATA_URL,'{"a":NaN}'),'json')

    def test_output_has_to_reference_actual_data(self):
        p=recipe();p['outputs'][0]['ref']='params.item'
        with self.assertRaises(http.SourceError):program.validate(p,evidence())

    def test_missing_field_not_success(self):
        reader=Mock(return_value=document(DATA_URL,'{}'))
        with self.assertRaisesRegex(http.SourceError,'missing_field'):
            program.execute(recipe(),{'item':'alpha'},evidence(),reader=reader)

    def test_type_check_boolean_not_number(self):
        reader=Mock(return_value=document(DATA_URL,'{"items":[{"name":"alpha","value":true}]}'))
        with self.assertRaisesRegex(http.SourceError,'result_type'):
            program.execute(recipe(),{'item':'alpha'},evidence(),reader=reader)


class HTTPTests(unittest.TestCase):
    def test_unsafe_urls(self):
        for url in ['http://example.org','https://localhost/','https://127.0.0.1/', 'https://[::1]/',
                    'https://user:pass@example.org/','https://example.org:8765/', 'https://thing.local/',
                    'https://example.org/\r\nX: 1','https://example.org/?api_key=x','file:///etc/passwd',
                    'https://example.org./','https://example.org/#fragment']:
            with self.subTest(url=url), self.assertRaises(http.SourceError):http.clean_url(url)

    def test_private_and_mixed_dns_denied(self):
        for addresses in [['127.0.0.1'],['10.0.0.2'],['169.254.169.254'],['8.8.8.8','192.168.1.1']]:
            infos=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in addresses]
            with patch.object(http.socket,'getaddrinfo',return_value=infos):
                with self.assertRaisesRegex(http.SourceError,'non_public_address'):http.public_addresses('example.org')

    def test_public_dns(self):
        infos=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        with patch.object(http.socket,'getaddrinfo',return_value=infos):self.assertEqual(len(http.public_addresses('example.org')),1)

    def test_connection_pinned_and_hostname_verified(self):
        sock=Mock(); context=Mock(); context.wrap_socket.return_value=Mock()
        with patch.object(http,'public_addresses',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,('8.8.8.8',443))]), patch.object(http.socket,'socket',return_value=sock):
            conn=http.PinnedHTTPS('example.org',timeout=3,context=context)
            conn.connect()
            sock.connect.assert_called_once_with(('8.8.8.8',443))
            context.wrap_socket.assert_called_once_with(sock,server_hostname='example.org')

    def response(self,status=200,body=b'{}',headers=None):
        r=Mock();r.status=status; hdr={'Content-Type':'application/json',**(headers or {})}
        r.getheader.side_effect=lambda k,default=None:hdr.get(k,default)
        r.read1.side_effect=[body,b''];return r

    def test_runtime_cross_origin_redirect_denied(self):
        conn=Mock();conn.getresponse.return_value=self.response(302,headers={'Location':'https://other.example.org/'})
        with patch.object(http,'PinnedHTTPS',return_value=conn),self.assertRaisesRegex(http.SourceError,'cross_origin_redirect'):
            http.fetch('https://example.org/')
        conn.close.assert_called_once()

    def test_body_size_compression_and_status(self):
        for r in [self.response(429),self.response(headers={'Content-Length':str(http.MAX_BYTES+1)}),
                  self.response(headers={'Content-Encoding':'gzip'}),self.response(body=b'x'*(http.MAX_BYTES+1))]:
            conn=Mock();conn.getresponse.return_value=r
            with patch.object(http,'PinnedHTTPS',return_value=conn),self.assertRaises(http.SourceError):http.fetch('https://example.org/')

    def test_identity_get_without_credentials(self):
        conn=Mock();conn.getresponse.return_value=self.response()
        with patch.object(http,'PinnedHTTPS',return_value=conn):out=http.fetch('https://example.org/a?q=1')
        args=conn.request.call_args
        self.assertEqual(args.args,('GET','/a?q=1'))
        self.assertNotIn('Authorization',args.kwargs['headers'])
        self.assertEqual(out.body,'{}')


class ProviderAndIntegrationTests(unittest.TestCase):
    def test_search_requires_real_metadata(self):
        researcher=learning.GeminiResearcher()
        with patch.object(researcher,'call',return_value=('I searched',{})),self.assertRaisesRegex(http.SourceError,'search_not_grounded'):
            researcher.search('public data')

    def test_search_preserves_suggestions(self):
        researcher=learning.GeminiResearcher()
        metadata={'groundingChunks':[{'web':{'uri':DOC_URL}}], 'searchEntryPoint':{'renderedContent':'<div>Search</div>'}}
        with patch.object(researcher,'call',return_value=('documentation',metadata)):
            out=researcher.search('public data')
        self.assertEqual(out['urls'],[DOC_URL]);self.assertIn('Search',out['suggestions'])

    def test_original_question_not_model_rewrite(self):
        import test_native_learning as old
        case=old.IntegrationTests()
        data={'candidates':[{'content':{'parts':[{'functionCall':{'name':'learn_public_source','args':{'argument':'OTHER'}}}]}}]}
        fn,env,_=case.planner([data])
        with patch.dict(os.environ,{'GEMINI_API_KEY':'test-only'}),patch.object(learning,'learn',return_value={'reply':'actual'}) as run:
            self.assertEqual(fn('Какое значение у alpha?',[])['reply'],'actual')
            run.assert_called_once_with('Какое значение у alpha?')

    def test_existing_actions_are_not_replayed(self):
        import test_native_learning as old
        data=[{'candidates':[{'content':{'parts':[{'functionCall':{'name':n,'args':{'argument':''}}}]}}]} for n in ('battery','learn_public_source')]
        fn,env,_=old.IntegrationTests().planner(data)
        with patch.dict(os.environ,{'GEMINI_API_KEY':'test-only'}),patch.object(learning,'learn') as run:
            self.assertEqual(fn('Сделай две задачи',[])['learning']['status'],'partial')
            run.assert_not_called()

    def test_legacy_voice_live_question_cannot_skip_planner(self):
        import test_native_learning as old
        fn=old.IntegrationTests.function(BRIDGE/'app.py','_nexus_fast_chat',{'_native_learning':native_learning})
        self.assertFalse(fn('Какая погода в моём городе?','выдуманный ответ'))
        self.assertFalse(fn('Расскажи последние новости','выдуманный ответ'))

    def test_source_migration_is_idempotent(self):
        import importlib.util
        path=BRIDGE.parents[1]/'scripts'/'integrate_public_sources.py'
        spec=importlib.util.spec_from_file_location('public_migration',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.main()

if __name__=='__main__':unittest.main()
