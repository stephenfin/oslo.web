# Copyright 2011 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os
import tempfile
from unittest import mock

from oslo_config import cfg
import webob

from oslo_web.tests import base
from oslo_web.wsgi import exceptions
from oslo_web.wsgi import loader
from oslo_web.wsgi import urlmap

CONF = cfg.CONF


def app_factory(global_conf, **local_conf):
    return webob.Response(text=local_conf['message'])


class TestLoaderNothingExists(base.BaseTestCase):
    """Loader tests where os.path.exists always returns False."""

    def setUp(self):
        super().setUp()

        mock_patcher = mock.patch.object(os.path, 'exists', lambda _: False)
        mock_patcher.start()
        self.addCleanup(mock_patcher.stop)

    def test_relpath_config_not_found(self):
        self.config(api_paste_config='api-paste.ini', group='oslo_web')
        self.assertRaises(exceptions.ConfigNotFound, loader.Loader, self.conf)

    def test_asbpath_config_not_found(self):
        self.config(
            api_paste_config='/etc/openstack-srv/api-paste.ini',
            group='oslo_web',
        )
        self.assertRaises(exceptions.ConfigNotFound, loader.Loader, self.conf)


class TestLoaderNormalFilesystem(base.BaseTestCase):
    """Loader tests with normal filesystem (unmodified os.path module)."""

    _paste_config = """
[app:test_app]
use = call:oslo_web.tests.unit.wsgi.test_loader:app_factory
message = Hello from oslo.web

[composite:main]
use = call:oslo_web.wsgi.urlmap:urlmap_factory
/v1 = test_app
not_found_app = missing

[app:missing]
use = call:oslo_web.tests.unit.wsgi.test_loader:app_factory
message = No matching application
    """

    def setUp(self):
        super().setUp()
        self.paste_config = tempfile.NamedTemporaryFile(mode='w+t')
        self.paste_config.write(self._paste_config.lstrip())
        self.paste_config.seek(0)
        self.paste_config.flush()

        self.config(api_paste_config=self.paste_config.name, group='oslo_web')
        self.loader = loader.Loader(CONF)

    def tearDown(self):
        self.paste_config.close()
        super().tearDown()

    def test_config_found(self):
        self.assertEqual(self.paste_config.name, self.loader.config_path)

    def test_app_not_found(self):
        self.assertRaises(
            exceptions.PasteAppNotFound,
            self.loader.load_app,
            'nonexistent app',
        )

    def test_urlmap(self):
        app = self.loader.load_app('main')
        self.assertIsInstance(app, urlmap.URLMap)
        response = webob.Request.blank('/v1/servers').get_response(app)
        self.assertEqual(200, response.status_int)
        self.assertEqual('Hello from oslo.web', response.text)
        response = webob.Request.blank('/v2').get_response(app)
        self.assertEqual('No matching application', response.text)

    def test_app_found(self):
        app = self.loader.load_app('test_app')
        response = webob.Request.blank('/').get_response(app)
        self.assertEqual(200, response.status_int)
        self.assertEqual('Hello from oslo.web', response.text)
