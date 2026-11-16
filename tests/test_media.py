import base64
import json
import unittest
import helpers
from r52.domain.media import raster_data
from r52.domain.normalize import normalize_eye, card_identity
from r52.desktop.link import CoreLink
from r52.errors import AppError

# Valid 1x1 PNG; opaque repeated base64 makes a >6KB payload regression fixture.
PNG='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'x'*7000).decode()
class MediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home=helpers.temp_home();cls.link=CoreLink(cls.home);cls.link.connect()
    @classmethod
    def tearDownClass(cls):
        cls.link.stop()
        import shutil
        shutil.rmtree(cls.home,ignore_errors=True)
    def api(self,method,path,body=None):
        return self.link.client.request(method,path,body)
    def test_media_survives_normalization(self):
        doc=helpers.eye_document();doc['observation']['media']={'image_data':PNG,'source_url':'https://example.com/photo.jpg'}
        n,c=normalize_eye(doc['observation']);self.assertEqual(n['media']['image_data'],PNG);self.assertEqual(c['media']['image_data'],PNG)
    def test_svg_rejected(self):
        self.assertIsNone(raster_data('data:image/svg+xml;base64,PHN2Zz4='))
    def test_fake_mime_rejected(self):
        self.assertIsNone(raster_data('data:image/png;base64,PHN2Zz4='))
    def test_oversized_rejected(self):
        self.assertIsNone(raster_data('data:image/png;base64,'+'A'*550001))
    def test_external_url_rejected(self):
        self.assertIsNone(raster_data('https://example.com/photo.png'))
    def test_farpost_identity(self):
        self.assertEqual(card_identity('FARPOST','12345','https://www.farpost.ru/vladivostok/test-12345.html'),'https://www.farpost.ru/vladivostok/test-12345.html')
    def test_media_roundtrip(self):
        doc=helpers.eye_document(listing_id='999111888');doc['observation']['media']={'image_data':PNG}
        result=self.api('POST','/api/inbox/import',{'filename':'jarvis-eye-media.json','data_base64':base64.b64encode(json.dumps(doc).encode()).decode()})
        accepted=self.api('POST',f"/api/inbox/{result['item']['id']}/accept",{});pid=accepted['product_id']
        card=self.api('GET',f'/api/products/{pid}')['product'];self.assertTrue(card['image_url']);self.assertEqual(self.api('GET',card['image_url'])['image_data'],PNG)
        self.api('POST',f'/api/favorites/{pid}',{});fav=self.api('GET','/api/favorites')['items'];self.assertTrue(next(p for p in fav if p['id']==pid)['image_url'])
    def test_manual_image_and_category(self):
        p=self.api('POST','/api/products/manual',{'title':'Тестовый товар'})['product'];pid=p['id']
        self.api('POST',f'/api/products/{pid}/image',{'image_data':PNG});self.api('POST',f'/api/products/{pid}/category',{'category_path':'Техника > Видеокарты'})
        p=self.api('GET',f'/api/products/{pid}')['product'];self.assertEqual(p['category_path'],'Техника > Видеокарты');self.assertTrue(p['image_url'])
    def test_invalid_image_api(self):
        p=self.api('POST','/api/products/manual',{'title':'Без фото'})['product']
        with self.assertRaises(AppError): self.api('POST',f"/api/products/{p['id']}/image",{'image_data':'javascript:alert(1)'})
    def test_private_farpost_rejected(self):
        with self.assertRaises(AppError):card_identity('FARPOST','123','https://www.farpost.ru/account/test-123.html')
