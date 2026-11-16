import shutil, unittest
import helpers
from r52.desktop.link import CoreLink
from r52.errors import AppError

class MapContextTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.home=helpers.temp_home();cls.link=CoreLink(cls.home);cls.link.connect();cls.ids=[]
  for title in ['Выбранный Alpha','Выбранный Beta','Посторонний Gamma']:
   cls.ids.append(cls.link.client.request('POST','/api/products/manual',{'title':title})['product']['id'])
 @classmethod
 def tearDownClass(cls):cls.link.stop();shutil.rmtree(cls.home,ignore_errors=True)
 def api(self,m,p,b=None):return self.link.client.request(m,p,b)
 def session(self):return self.api('POST','/api/assistant/sessions',{})['id']
 def send(self,sid,text='Сравни выбранные записи',scope=None,key=None):
  body={'text':text}
  if scope is not None:body['context_product_ids']=scope
  if key:body['idempotency_key']=key
  return self.api('POST',f'/api/assistant/sessions/{sid}/messages',body)
 def test_exact_scope_and_comparison(self):
  reply=self.send(self.session(),scope=self.ids[:2])['reply'];self.assertEqual(reply['data']['intent'],'map_context');self.assertEqual([p['id'] for p in reply['data']['cards']],self.ids[:2]);self.assertTrue(reply['data']['table']);self.assertNotIn('Gamma',reply['text'])
 def test_single_product(self):
  reply=self.send(self.session(),scope=self.ids[:1])['reply'];self.assertEqual(len(reply['data']['cards']),1);self.assertIsNone(reply['data']['table'])
 def test_scope_persists_after_reload(self):
  sid=self.session();self.send(sid,scope=self.ids[:1]);self.api('GET',f'/api/assistant/sessions/{sid}');reply=self.send(sid,'Какие сведения есть?')['reply'];self.assertEqual([p['id'] for p in reply['data']['cards']],self.ids[:1])
 def test_scope_can_be_cleared(self):
  sid=self.session();self.send(sid,scope=self.ids[:1]);reply=self.send(sid,'найди Посторонний Gamma',[])['reply'];self.assertEqual(reply['data']['intent'],'search');self.assertIn(self.ids[2],[p['id'] for p in reply['data']['cards']])
 def test_scope_is_read_only(self):
  reply=self.send(self.session(),'добавь Выбранный Alpha в избранное',self.ids[:1])['reply'];self.assertEqual(reply['data']['actions'],[]);self.assertFalse(self.api('GET',f'/api/products/{self.ids[0]}')['product']['favorite'])
 def test_missing_ids_do_not_expand_search(self):
  reply=self.send(self.session(),'найди Alpha',[999999999])['reply'];self.assertEqual(reply['data']['cards'],[]);self.assertIn('не найдены',reply['text'])
 def test_invalid_scope_rejected(self):
  for scope in ['1,2',[True],[0],[-1],['1'],list(range(1,14))]:
   with self.subTest(scope=scope),self.assertRaises(AppError):self.send(self.session(),scope=scope)
 def test_idempotency_includes_scope(self):
  sid=self.session();self.send(sid,scope=self.ids[:1],key='map_scope_replay');self.assertTrue(self.send(sid,scope=self.ids[:1],key='map_scope_replay')['replayed'])
  with self.assertRaises(AppError) as cm:self.send(sid,scope=self.ids[1:2],key='map_scope_replay')
  self.assertEqual(cm.exception.code,'IDEMPOTENCY_CONFLICT')
