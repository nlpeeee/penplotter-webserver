import os
import unittest

import main


class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not main.config.has_section('telegram'):
            main.config.read('config.ini.sample')

    def setUp(self):
        self.client = main.app.test_client()

    def test_home_uses_pcp_branding_and_accessible_logo(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<title>PCP</title>', html)
        self.assertEqual(html.count('alt="PCP logo"'), 2)
        self.assertIn('img/pcp-logo.png?v=', html)
        self.assertNotIn('img/user.png', html)
        self.assertNotIn('class="uk-text-center uk-margin-remove-vertical text-light">PCP</h4>', html)
        self.assertNotIn('Web Plotter', html)
        self.assertNotIn('Plotter Webserver', html)

    def test_footer_retains_upstream_and_license_attribution(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('PCP — based on', html)
        self.assertIn('https://github.com/henrytriplette/penplotter-webserver', html)
        self.assertIn('created by Henry Triplette (2021)', html)
        self.assertIn('href="/license"', html)

    def test_bundled_license_is_served(self):
        response = self.client.get('/license')
        self.assertEqual(response.status_code, 200)
        license_text = response.get_data(as_text=True)
        self.assertIn('MIT License', license_text)
        self.assertIn('Copyright (c) 2021 Henry Triplette', license_text)
        self.assertIn('Permission is hereby granted', license_text)
        response.close()

    def test_logo_is_present(self):
        logo_path = os.path.join(main.app.static_folder, 'img', 'pcp-logo.png')
        obsolete_logo_path = os.path.join(main.app.static_folder, 'img', 'user.png')
        self.assertTrue(os.path.isfile(logo_path))
        self.assertGreater(os.path.getsize(logo_path), 1000)
        self.assertFalse(os.path.exists(obsolete_logo_path))


if __name__ == '__main__':
    unittest.main()
