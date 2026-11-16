import base64,json,unittest
from unittest.mock import patch
import helpers
from r52.domain.media import normalize_media,public_media_url
from r52.desktop.link import CoreLink
from r52.errors import AppError
# A real PNG, extended with trailing bytes to exercise strings beyond text-field bounds.
PNG='data:image/png;base64,'+base64.b64encode(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0V8AAAAASUVORK5CYII=')+b'x'*6500).decode()
class GalleryTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.home=helpers.temp_home();cls.link=CoreLink(cls.home);cls.link.connect()
 @classmethod
 def tearDownClass(cls):
  cls.link.stop();__import__('shutil').rmtree(cls.home,ignore_errors=True)
 def api(self,m,p,b=None):return self.link.client.request(m,p,b)
 def staged(self,id='222333',items=None):
  doc=helpers.eye_document(listing_id=id,observed_at='2026-11-08T09:00:00Z');doc['observation']['media']={'items':items or [{'type':'image','image_data':PNG},{'type':'image','source_url':'https://ir.ozone.ru/s3/test.jpg'},{'type':'video','source_url':'https://cdn1.ozone.ru/product.mp4'}]}
  return self.api('POST','/api/inbox/import',{'filename':'jarvis-eye-gallery.json','data_base64':base64.b64encode(json.dumps(doc).encode()).decode()})['item']
 def test_gallery_normalization(self):
  m=normalize_media({'items':[{'type':'image','image_data':PNG},{'type':'video','source_url':'https://cdn1.ozone.ru/a.mp4'}]});self.assertEqual(len(m['items']),2);self.assertEqual(m['image_data'],PNG)
 def test_private_and_unknown_urls_rejected(self):
  for u in ['http://ir.ozone.ru/a.jpg','https://localhost/a.jpg','https://127.0.0.1/a.jpg','https://ozone.ru.evil.org/a.jpg','https://REDACTED@ir.ozone.ru/a.jpg','https://ir.ozone.ru:444/a.jpg','javascript:alert(1)']:
   self.assertIsNone(public_media_url(u))
 def test_duplicates_removed(self):
  m=normalize_media({'items':[{'type':'image','image_data':PNG}]*3});self.assertEqual(len(m['items']),1)
 def test_streaming_manifest_rejected(self):
  self.assertFalse(normalize_media({'items':[{'type':'video','source_url':'https://cdn1.ozone.ru/a.m3u8'}]}))
 def test_gallery_item_limit(self):
  m=normalize_media({'items':[{'type':'image','source_url':'https'+':/'+'/ir.ozone.ru/'+str(i)+'.jpg'} for i in range(70)]});self.assertEqual(len(m['items']),40)
 def test_inbox_gallery_before_accept(self):
  item=self.staged('222334');base=f"/api/inbox/{item['id']}/media";meta=self.api('GET',base);self.assertEqual([i['type'] for i in meta['items']],['image','image','video']);self.assertNotIn('image_data',meta['items'][0]);self.assertEqual(self.api('GET',base+'/0')['image_data'],PNG);self.assertEqual(item['preview']['photo_count'],2)
 def test_accept_and_reload_gallery(self):
  item=self.staged('222335');accepted=self.api('POST',f"/api/inbox/{item['id']}/accept",{});pid=accepted['product_id'];p=self.api('GET',f'/api/products/{pid}')['product'];self.assertEqual(p['photo_count'],2);self.assertEqual(p['video_count'],1);self.assertEqual(len(self.api('GET',p['media_url'])['items']),3)
  with self.assertRaises(AppError):self.api('GET',p['media_url']+'/90')
 def test_manual_gallery_append(self):
  p=self.api('POST','/api/products/manual',{'title':'Тест галереи'})['product'];base=f"/api/products/{p['id']}/media";self.api('POST',base,{'items':[{'type':'image','image_data':PNG}]});self.api('POST',base,{'items':[{'type':'image','source_url':'https://ir.ozone.ru/second.jpg'}]});self.assertEqual(len(self.api('GET',base)['items']),2)
 def test_invalid_manual_gallery(self):
  p=self.api('POST','/api/products/manual',{'title':'Тест безопасности'})['product']
  with self.assertRaises(AppError):self.api('POST',f"/api/products/{p['id']}/media",{'items':[{'type':'image','source_url':'file:///etc/passwd'}]})
 def test_missing_media_does_not_erase_gallery(self):
  item=self.staged('222336');pid=self.api('POST',f"/api/inbox/{item['id']}/accept",{})['product_id'];self.api('POST',f'/api/products/{pid}/observations/manual',{'amount':'2500'});self.assertEqual(self.api('GET',f'/api/products/{pid}')['product']['photo_count'],2)
 def test_migration_from_released_schema(self):
  from r52.db.database import Database
  from r52.db.migrate import migrate
  from r52.db.schema import MIGRATIONS
  db=Database(helpers.temp_home(self)/'test.sqlite');db.initialize()
  with patch('r52.db.migrate.MIGRATIONS',MIGRATIONS[:2]):migrate(db)
  with db.write() as conn:conn.execute("INSERT INTO product(source,listing_id,title,origin,search_text,created_at,updated_at,image_data) VALUES('MANUAL','compat','Legacy','MANUAL','legacy','2026-11-01','2026-11-01',?)",(PNG,))
  migrate(db)
  with db.read() as conn:
   row=conn.execute("SELECT image_data,gallery_json FROM product WHERE listing_id='compat'").fetchone();self.assertEqual(row['image_data'],PNG);self.assertIsNone(row['gallery_json'])
