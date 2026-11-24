# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import datetime
import functools
import types
from unittest import mock

import webob
import webob.exc

from oslo_web import opts
from oslo_web.tests import base
from oslo_web.validation import decorators
from oslo_web.validation import exceptions


class TestSchemas(base.BaseTestCase):
    def test_adjacent_ranges_and_selection(self):
        schemas = decorators.Schemas()
        schemas.add_schema({'title': 'new'}, 3, None)
        schemas.add_schema({'title': 'old'}, 1, 2)
        self.assertIsNone(schemas(0))
        for version in (1, 2):
            self.assertEqual({'title': 'old'}, schemas(version))
        for version in (3, 100):
            self.assertEqual({'title': 'new'}, schemas(version))

    def test_invalid_coverage_is_rejected_atomically(self):
        for first, second in (
            ((None, None), (1, None)),
            ((0, 0), (0, 1)),
            ((1, 2), (2, 3)),
            ((1, 2), (4, 5)),
            ((1, None), (1, 2)),
        ):
            with self.subTest(first=first, second=second):
                schemas = decorators.Schemas()
                schemas.add_schema({'title': 'first'}, *first)
                self.assertRaises(
                    RuntimeError, schemas.add_schema, {}, *second
                )
                self.assertEqual({'title': 'first'}, schemas(first[0] or 0))

    def test_invalid_range(self):
        for minimum, maximum in ((-1, 1), (2, 1), (None, -1)):
            with self.subTest(minimum=minimum, maximum=maximum):
                schemas = decorators.Schemas()
                self.assertRaises(
                    ValueError, schemas.add_schema, {}, minimum, maximum
                )


class TestVersionedValidation(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        opts.register_wsgi_opts(self.conf)
        self.config(response_validation='error', group='oslo_web')

    def test_request_bounds(self):
        for decorate in (
            decorators.request_body_schema,
            decorators.request_query_schema,
        ):
            for version in (0, 1, 2, 3):
                with self.subTest(decorate=decorate, version=version):
                    service_schema = functools.partial(
                        decorate, version_getter=lambda **kw: version
                    )

                    @service_schema(
                        {'type': 'object', 'required': ['missing']}, 1, 2
                    )
                    def method(**kwargs):
                        return 'result'

                    if version in (1, 2):
                        self.assertRaises(
                            exceptions.ValidationError,
                            method,
                            body={},
                            req=webob.Request.blank('/'),
                        )
                    else:
                        self.assertEqual(
                            'result',
                            method(body={}, req=webob.Request.blank('/')),
                        )

    def test_response_bounds(self):
        service_response_body_schema = functools.partial(
            decorators.response_body_schema,
            version_getter=lambda version: version,
        )

        @service_response_body_schema({'type': 'object'}, 1, 2)
        def method(version):
            return 'not an object'

        self.assertEqual('not an object', method(0))
        self.assertRaises(webob.exc.HTTPInternalServerError, method, 1)
        self.assertRaises(webob.exc.HTTPInternalServerError, method, 2)
        self.assertEqual('not an object', method(3))

    def test_version_getter_required(self):
        for decorate in (
            decorators.request_body_schema,
            decorators.request_query_schema,
            decorators.response_body_schema,
        ):
            self.assertRaises(ValueError, decorate, {}, min_version=1)

    def test_stacked_schemas(self):
        service_request_body_schema = functools.partial(
            decorators.request_body_schema,
            version_getter=lambda **kw: kw['version'],
        )

        @service_request_body_schema({'required': ['new']}, 2, None)
        @service_request_body_schema({'required': ['old']}, None, 1)
        def method(**kwargs):
            return kwargs['body']

        self.assertEqual({'old': True}, method(version=1, body={'old': True}))
        self.assertEqual({'new': True}, method(version=2, body={'new': True}))


class TestRequestExtraction(base.BaseTestCase):
    def test_query_excludes_route_arguments_and_preserves_repetitions(self):
        schema = {
            'type': 'object',
            'properties': {'tag': {'const': ['a', 'b']}},
            'required': ['tag'],
            'additionalProperties': False,
        }

        @decorators.request_query_schema(schema)
        def method(req, resource_id):
            return resource_id

        request = webob.Request.blank('/?tag=a&tag=b')
        self.assertEqual('id', method(request, 'id'))
        self.assertEqual('id', method(req=request, resource_id='id'))

    def test_query_getter(self):
        service_request_query_schema = functools.partial(
            decorators.request_query_schema,
            query_getter=lambda: {'limit': '1'},
        )

        @service_request_query_schema({'const': {'limit': '1'}})
        def method():
            return True

        self.assertTrue(method())

    def test_query_requires_request_or_getter(self):
        @decorators.request_query_schema({})
        def method():
            pass

        self.assertRaises(ValueError, method)

    def test_bad_query_encoding(self):
        request = mock.Mock()
        request.GET.dict_of_lists.side_effect = UnicodeDecodeError(
            'utf-8', b'\xff', 0, 1, 'invalid'
        )

        @decorators.request_query_schema({})
        def method(req):
            pass

        self.assertRaises(exceptions.ValidationError, method, request)

    def test_body_binds_positional_keyword_and_default(self):
        @decorators.request_body_schema({'const': {'name': 'ok'}})
        def method(body={'name': 'ok'}):
            return body

        self.assertEqual({'name': 'ok'}, method())
        self.assertEqual({'name': 'ok'}, method({'name': 'ok'}))
        self.assertEqual({'name': 'ok'}, method(body={'name': 'ok'}))
        self.assertRaises(exceptions.ValidationError, method, {})

    def test_body_getter(self):
        service_request_body_schema = functools.partial(
            decorators.request_body_schema, body_getter=lambda payload: payload
        )

        @service_request_body_schema({'const': [1]})
        def method(payload):
            return payload

        self.assertEqual([1], method([1]))
        self.assertRaises(exceptions.ValidationError, method, [2])


class TestResponsePolicy(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        opts.register_wsgi_opts(self.conf)

    def test_warning_returns_original_response(self):
        response = {'secret': 'must not reach the client error'}

        @decorators.response_body_schema({'type': 'array'})
        def method():
            return response

        with mock.patch.object(decorators.LOG, 'exception') as log:
            self.assertIs(response, method())
        log.assert_called_once_with('Schema failed to validate')

    def test_error_is_internal_server_error(self):
        self.config(response_validation='error', group='oslo_web')

        @decorators.response_body_schema({'type': 'array'})
        def method():
            return {'secret': 'must not reach the client error'}

        exc = self.assertRaises(webob.exc.HTTPInternalServerError, method)
        self.assertNotIn('secret', exc.detail)

    def test_ignore_does_not_validate_or_serialize(self):
        self.config(response_validation='ignore', group='oslo_web')
        response = object()

        @decorators.response_body_schema({'type': 'array'})
        def method():
            return response

        self.assertIs(response, method())

    def test_valid_response_is_unchanged(self):
        response: list[object] = []

        @decorators.response_body_schema({'type': 'array'})
        def method():
            return response

        self.assertIs(response, method())


class TestResponseExtraction(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        opts.register_wsgi_opts(self.conf)
        self.config(response_validation='error', group='oslo_web')

    def test_response_representations(self):
        for response in (
            {'name': 'ok'},
            types.SimpleNamespace(obj={'name': 'ok'}),
            ({'name': 'ok'}, 201),
            ({'name': 'ok'}, 201, {'X-Test': 'value'}),
            webob.Response(json_body={'name': 'ok'}),
        ):
            with self.subTest(response=response):

                @decorators.response_body_schema({'const': {'name': 'ok'}})
                def method():
                    return response

                self.assertIs(response, method())

    def test_empty_json_response_and_null(self):
        for response in (
            None,
            webob.Response(body=b'', content_type='application/json'),
        ):

            @decorators.response_body_schema({'type': 'null'})
            def method():
                return response

            self.assertIs(response, method())

    def test_json_string_is_not_treated_as_encoded_json(self):
        @decorators.response_body_schema({'const': ''})
        def method():
            return ''

        self.assertEqual('', method())

    def test_datetime_serialization(self):
        @decorators.response_body_schema(
            {'const': {'date': '2026-01-01T00:00:00.000000'}}
        )
        def method():
            return {'date': datetime.datetime(2026, 1, 1)}

        method()

    def test_custom_response_getter(self):
        response = types.SimpleNamespace(payload=[1])

        def get_payload(value: types.SimpleNamespace) -> object:
            return value.payload

        service_response_body_schema = functools.partial(
            decorators.response_body_schema, response_getter=get_payload
        )

        @service_response_body_schema({'const': [1]})
        def method():
            return response

        self.assertIs(response, method())

    def test_does_not_consume_stream(self):
        iterator = iter([b'{"name":"ok"}'])
        response = webob.Response(
            app_iter=iterator, content_type='application/json'
        )

        @decorators.response_body_schema({'type': 'null'})
        def method():
            return response

        self.assertIs(response, method())
        self.assertEqual(b'{"name":"ok"}', next(iterator))

    def test_binary_response_is_not_json_validated(self):
        response = webob.Response(
            body=b'\xff', content_type='application/octet-stream'
        )

        @decorators.response_body_schema({'type': 'null'})
        def method():
            return response

        self.assertIs(response, method())

    def test_malformed_json_obeys_response_policy(self):
        response = webob.Response(
            body=b'{', content_type='application/problem+json'
        )

        @decorators.response_body_schema({})
        def method():
            return response

        self.assertRaises(webob.exc.HTTPInternalServerError, method)
        self.config(response_validation='warn', group='oslo_web')
        with mock.patch.object(decorators.LOG, 'exception') as log:
            self.assertIs(response, method())
        log.assert_called_once()
