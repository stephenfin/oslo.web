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

from typing import Any
from unittest import mock

import webob

from oslo_web.tests import base
from oslo_web.wsgi import urlmap


def echo_app(environ, start_response):
    response = webob.Response(
        text=environ['SCRIPT_NAME'] + '|' + environ['PATH_INFO']
    )
    return response(environ, start_response)


class TestURLMap(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.mapper = urlmap.URLMap()

    def test_longest_prefix(self):
        self.mapper['/'] = webob.Response(text='root')
        self.mapper['/v1'] = webob.Response(text='v1')
        self.mapper['/v1/detail'] = webob.Response(text='detail')
        for path, expected in [
            ('/', 'root'),
            ('/v1', 'v1'),
            ('/v1/servers', 'v1'),
            ('/v1/detail/servers', 'detail'),
            ('/v10', 'root'),
        ]:
            with self.subTest(path=path):
                response = webob.Request.blank(path).get_response(self.mapper)
                self.assertEqual(expected, response.text)

    def test_path_rewriting(self):
        self.mapper['/v1'] = echo_app
        for path, expected in [
            ('/v1', '/api/v1|'),
            ('/v1/', '/api/v1|/'),
            ('/v1/servers', '/api/v1|/servers'),
            ('//v1//servers', '/api/v1|/servers'),
        ]:
            with self.subTest(path=path):
                request = webob.Request.blank('http://example.com' + path)
                request.environ['SCRIPT_NAME'] = '/api'
                response = request.get_response(self.mapper)
                self.assertEqual(expected, response.text)

    def test_nested_maps(self):
        inner = urlmap.URLMap()
        inner['/v1'] = echo_app
        self.mapper['/api'] = inner
        response = webob.Request.blank('/api/v1/servers').get_response(
            self.mapper
        )
        self.assertEqual('/api/v1|/servers', response.text)

    def test_domain_and_port(self):
        self.mapper['/'] = webob.Response(text='fallback')
        self.mapper['http://example.com/'] = webob.Response(text='domain')
        self.mapper['http://port.example.com:8080/v1'] = webob.Response(
            text='port'
        )
        for address, expected in [
            ('http://example.com/', 'domain'),
            ('http://EXAMPLE.COM/', 'domain'),
            ('http://port.example.com:8080/v1', 'port'),
            ('http://port.example.com:8081/v1', 'fallback'),
            ('http://other.example/v1', 'fallback'),
        ]:
            with self.subTest(address=address):
                response = webob.Request.blank(address).get_response(
                    self.mapper
                )
                self.assertEqual(expected, response.text)

    def test_default_ports(self):
        self.mapper['http://example.com:80/'] = webob.Response(text='http')
        self.mapper['https://example.com:443/'] = webob.Response(text='https')
        for scheme in ('http', 'https'):
            with self.subTest(scheme=scheme):
                request = webob.Request.blank(scheme + '://example.com/')
                request.environ['HTTP_HOST'] = 'example.com'
                self.assertEqual(
                    scheme, request.get_response(self.mapper).text
                )

    def test_server_name_fallback(self):
        self.mapper['http://example.com/'] = webob.Response(text='domain')
        request = webob.Request.blank('http://example.com/')
        del request.environ['HTTP_HOST']
        self.assertEqual('domain', request.get_response(self.mapper).text)

    def test_not_found(self):
        self.mapper['/v1'] = echo_app
        response = webob.Request.blank('/v2').get_response(self.mapper)
        self.assertEqual(404, response.status_int)

    def test_empty_map_not_found(self):
        response = webob.Request.blank('/').get_response(self.mapper)
        self.assertEqual(404, response.status_int)

    def test_not_found_without_http_host(self):
        request = webob.Request.blank('/')
        del request.environ['HTTP_HOST']
        self.assertEqual(404, request.get_response(self.mapper).status_int)

    def test_not_found_escapes_html(self):
        self.mapper['/<script>alert(1)</script>'] = echo_app
        request = webob.Request.blank(
            '/missing', headers={'Accept': 'text/html'}
        )
        response = request.get_response(self.mapper)
        self.assertEqual(404, response.status_int)
        self.assertNotIn('<script>', response.text)

    def test_custom_not_found(self):
        def not_found(environ, start_response):
            self.assertIs(self.mapper, environ['paste.urlmap_object'])
            return webob.Response(status=410)(environ, start_response)

        self.mapper = urlmap.URLMap(not_found_app=not_found)
        response = webob.Request.blank('/').get_response(self.mapper)
        self.assertEqual(410, response.status_int)

    def test_normalize_url(self):
        cases: list[
            tuple[
                str | tuple[str, str] | list[str],
                tuple[str | None, str],
            ]
        ] = [
            ('/', (None, '')),
            ('/v1//servers/', (None, '/v1/servers')),
            ('https://example.com:443/v1/', ('example.com:443', '/v1')),
            ('http://example.com', ('example.com', '')),
            (('example.com', '/v1/'), ('example.com', '/v1')),
            (['example.com', '/v1/'], ('example.com', '/v1')),
        ]
        for address, expected in cases:
            with self.subTest(address=address):
                self.assertEqual(expected, self.mapper.normalize_url(address))
        self.assertEqual(
            (None, '/v1/'), self.mapper.normalize_url('/v1/', trim=False)
        )

    def test_invalid_url(self):
        self.assertRaises(AssertionError, self.mapper.normalize_url, 'v1')

    def test_mapping_operations(self):
        self.mapper['/v1/'] = echo_app
        self.assertEqual(1, len(self.mapper))
        self.assertEqual([(None, '/v1')], list(self.mapper))
        self.assertEqual([(None, '/v1')], list(self.mapper.keys()))
        self.assertIs(echo_app, self.mapper['/v1'])
        self.assertIn('/v1', self.mapper)
        replacement = webob.Response(text='replacement')
        self.mapper['/v1'] = replacement
        self.assertEqual(1, len(self.mapper))
        self.assertIs(replacement, self.mapper.get('/v1/'))
        del self.mapper['/v1']
        self.assertEqual(0, len(self.mapper))

    def test_application_order(self):
        for path in (
            '/',
            '/v2',
            '/v2.1',
            '/bbb',
            '/aaa',
            'http://example.org/',
            'http://example.com/',
            'http://example.com/v2',
            'http://example.com/v2.1',
        ):
            self.mapper[path] = mock.sentinel.app

        self.assertEqual(
            [
                ('example.com', '/v2.1'),
                ('example.com', '/v2'),
                ('example.com', ''),
                ('example.org', ''),
                (None, '/v2.1'),
                (None, '/aaa'),
                (None, '/bbb'),
                (None, '/v2'),
                (None, ''),
            ],
            list(self.mapper),
        )

    def test_keys_view(self):
        self.mapper['/'] = mock.sentinel.root_app
        keys = self.mapper.keys()
        self.mapper['/v2/'] = mock.sentinel.version_app

        self.assertEqual([(None, '/v2'), (None, '')], list(keys))
        self.assertIn((None, '/v2'), keys)
        self.assertIs(mock.sentinel.version_app, self.mapper['/v2'])

        del self.mapper['/v2']

        self.assertEqual([(None, '')], list(keys))

    def test_missing_keys(self):
        for key in ('', '/', '/missing', (None, '/missing')):
            with self.subTest(key=key):
                self.assertRaises(KeyError, self.mapper.__getitem__, key)
                self.assertRaises(KeyError, self.mapper.__delitem__, key)

    def test_assign_none_removes_app(self):
        self.mapper['/v1'] = echo_app
        self.mapper['/v1'] = None
        self.mapper['/missing'] = None
        self.assertEqual(0, len(self.mapper))

    def test_subclass(self):
        class CustomURLMap(urlmap.URLMap):
            def __call__(self, environ, start_response):
                self.asserted = self.normalize_url('/v1/')
                return super().__call__(environ, start_response)

        mapper = CustomURLMap()
        mapper['/v1'] = echo_app
        response = webob.Request.blank('/v1/servers').get_response(mapper)
        self.assertEqual('/v1|/servers', response.text)
        self.assertEqual((None, '/v1'), mapper.asserted)
        self.assertEqual([((None, '/v1'), echo_app)], mapper.applications)


class TestPathExpression(base.BaseTestCase):
    def test_valid(self):
        for expression, expected in [
            ('', ''),
            ('/v1', '/v1'),
            ('domain example.com', 'http://example.com'),
            ('domain example.com port 8080 v1', 'http://example.com:8080/v1'),
            (
                '/v1 port 8080 domain example.com',
                'http://example.com:8080//v1',
            ),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(
                    expected, urlmap.parse_path_expression(expression)
                )

    def test_invalid(self):
        for expression in (
            'domain',
            'port',
            'port 80 /',
            'domain example.com domain other.example',
            'port 80 port 81',
            '/v1 /v2',
        ):
            with self.subTest(expression=expression):
                self.assertRaises(
                    ValueError, urlmap.parse_path_expression, expression
                )


class TestURLMapFactory(base.BaseTestCase):
    def test_factory(self):
        app = webob.Response(text='api')
        loader = mock.Mock()
        loader.get_app.return_value = app
        global_conf: dict[str, Any] = {}
        mapper = urlmap.urlmap_factory(
            loader, global_conf, **{'domain example.com /v1': 'api'}
        )
        self.assertIs(app, mapper['http://example.com/v1'])
        loader.get_app.assert_called_once_with('api', global_conf=global_conf)

    def test_not_found_app(self):
        for local in (False, True):
            with self.subTest(local=local):
                loader = mock.Mock()
                global_conf = {'not_found_app': 'global'}
                local_conf = {'not_found_app': 'local'} if local else {}
                mapper = urlmap.urlmap_factory(
                    loader, global_conf, **local_conf
                )
                loader.get_app.assert_called_once_with(
                    'local' if local else 'global', global_conf=global_conf
                )
                self.assertIs(
                    loader.get_app.return_value, mapper.not_found_application
                )
                self.assertEqual(0, len(mapper))
