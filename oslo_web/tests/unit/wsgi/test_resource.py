# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from unittest import mock

import routes
import webob
import webob.exc

from oslo_web.tests import base
from oslo_web.wsgi import resource
from oslo_web.wsgi import router


class ResourceTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.controller = mock.Mock()
        self.controller.show.return_value = {'ok': True}
        self.resource = resource.Resource(self.controller)

    def request(self, method='GET', body=b'', headers=None, action='show'):
        request = webob.Request.blank(
            '/', method=method, body=body, headers=headers or {}
        )
        request.environ['wsgiorg.routing_args'] = (
            (),
            {'controller': self.resource, 'action': action, 'id': '123'},
        )
        return request

    def test_routes_integration(self):
        mapper = routes.Mapper()
        mapper.connect(
            'item', '/items/{id}', controller=self.resource, action='show'
        )
        response = webob.Request.blank('/items/123').get_response(
            router.Router(mapper)
        )
        self.assertEqual(200, response.status_int)
        self.assertEqual({'ok': True}, response.json)
        self.assertEqual('application/json', response.content_type)
        self.controller.show.assert_called_once_with(req=mock.ANY, id='123')
        self.assertIsInstance(
            self.controller.show.call_args.kwargs['req'], webob.Request
        )

    def test_route_args_are_copied(self):
        args = {
            'controller': self.resource,
            'format': 'json',
            'action': 'show',
        }
        environ = {'wsgiorg.routing_args': ((), args)}
        result = self.resource.get_action_args(environ)
        self.assertEqual({'action': 'show'}, result)
        self.assertEqual(3, len(args))

    def test_missing_route(self):
        response = webob.Request.blank('/').get_response(self.resource)
        self.assertEqual(404, response.status_int)

    def test_malformed_route_metadata(self):
        for routing in (None, (), ((), None), 'invalid'):
            with self.subTest(routing=routing):
                self.assertEqual(
                    {},
                    self.resource.get_action_args(
                        {'wsgiorg.routing_args': routing}
                    ),
                )

    def test_missing_or_private_method(self):
        app = resource.Resource(object())
        for action in ('missing', '_private', None):
            with self.subTest(action=action):
                response = self.request(action=action).get_response(app)
                self.assertEqual(404, response.status_int)

    def test_json_body_methods(self):
        for method in ('POST', 'PUT', 'PATCH'):
            with self.subTest(method=method):
                request = self.request(
                    method,
                    b'{"name": "caf\xc3\xa9"}',
                    {'Content-Type': 'application/json; charset=UTF-8'},
                )
                response = request.get_response(self.resource)
                self.assertEqual(200, response.status_int)
                self.assertEqual(
                    {'name': 'caf\u00e9'},
                    self.controller.show.call_args.kwargs['body'],
                )

    def test_empty_body(self):
        self.request('POST').get_response(self.resource)
        self.assertIsNone(self.controller.show.call_args.kwargs['body'])

    def test_absent_content_type_defaults_to_json(self):
        self.request('POST', b'{}').get_response(self.resource)
        self.assertEqual({}, self.controller.show.call_args.kwargs['body'])

    def test_get_body_is_not_read(self):
        with mock.patch.object(self.resource, 'get_body') as get_body:
            self.request().get_response(self.resource)
        get_body.assert_not_called()
        self.assertNotIn('body', self.controller.show.call_args.kwargs)

    def test_invalid_json(self):
        for body in (b'{', b'\xff'):
            with self.subTest(body=body):
                response = self.request('POST', body).get_response(
                    self.resource
                )
                self.assertEqual(400, response.status_int)
        self.controller.show.assert_not_called()

    def test_unsupported_media_type(self):
        response = self.request(
            'POST',
            b'hello',
            {
                'Content-Type': 'text/plain',
            },
        ).get_response(self.resource)
        self.assertEqual(415, response.status_int)
        self.controller.show.assert_not_called()

    def test_accept(self):
        for accept, status in (
            ('application/json', 200),
            ('*/*', 200),
            ('application/*', 200),
            ('text/plain', 406),
            ('application/json;q=0', 406),
        ):
            with self.subTest(accept=accept):
                response = self.request(
                    headers={'Accept': accept}
                ).get_response(self.resource)
                self.assertEqual(status, response.status_int)

    def test_none_response(self):
        self.controller.show.return_value = None
        response = self.request().get_response(self.resource)
        self.assertEqual(200, response.status_int)
        self.assertEqual(b'', response.body)

    def test_json_values(self):
        values: tuple[object, ...] = ({}, [], 'caf\u00e9', 1, False)
        for value in values:
            with self.subTest(value=value):
                self.controller.show.return_value = value
                response = self.request().get_response(self.resource)
                self.assertEqual(value, response.json)

    def test_response_passthrough(self):
        chunks = iter([b'\x00', b'\xff'])
        response = webob.Response(
            app_iter=chunks,
            status=202,
            headers={
                'Content-Type': 'application/octet-stream',
                'X-Custom': 'value',
            },
        )
        result = self.resource.serialize(
            self.request(), response, 'application/json'
        )
        self.assertIs(response, result)
        self.assertIs(chunks, result.app_iter)
        self.controller.show.return_value = response
        actual = self.request().get_response(self.resource)
        self.assertEqual(202, actual.status_int)
        self.assertEqual('value', actual.headers['X-Custom'])
        self.assertEqual('application/octet-stream', actual.content_type)
        self.assertEqual(b'\x00\xff', actual.body)

    def test_http_exception(self):
        self.controller.show.side_effect = webob.exc.HTTPConflict()
        self.assertEqual(
            409, self.request().get_response(self.resource).status_int
        )

    def test_unexpected_exception_propagates(self):
        for error in (TypeError('implementation bug'), RuntimeError('bug')):
            with self.subTest(error=error):
                self.controller.show.side_effect = error
                self.assertRaises(
                    type(error), self.request().get_response, self.resource
                )

    def test_head(self):
        response = self.request('HEAD').get_response(self.resource)
        self.assertEqual(200, response.status_int)
        self.assertEqual(b'', response.body)

    def test_extraction_and_serialization_hooks(self):
        class CustomResource(resource.Resource):
            body_methods = frozenset({'DELETE'})

            def negotiate_content_type(self, request):
                return 'text/plain'

            def get_method(self, request, action):
                return super().get_method(request, 'show')

            def get_body(self, request):
                return b'custom'

            def deserialize(self, request, body):
                return body.decode('ascii')

            def serialize(self, request, result, content_type):
                return webob.Response(text=result, content_type=content_type)

        self.controller.show.return_value = 'custom response'
        response = self.request('DELETE', action='alias').get_response(
            CustomResource(self.controller)
        )
        self.controller.show.assert_called_once_with(
            req=mock.ANY, id='123', body='custom'
        )
        self.assertEqual('text/plain', response.content_type)
        self.assertEqual('custom response', response.text)

    def test_preparation_errors_are_finalized(self):
        class CustomResource(resource.Resource):
            def process_request(self, request):
                raise webob.exc.HTTPForbidden()

            def process_response(self, request, response):
                response.headers['X-API'] = 'custom'
                return response

        response = self.request().get_response(CustomResource(self.controller))
        self.assertEqual(403, response.status_int)
        self.assertEqual('custom', response.headers['X-API'])
        self.controller.show.assert_not_called()

    def test_finalizer_errors_propagate(self):
        with (
            mock.patch.object(
                self.resource, 'process_response', side_effect=RuntimeError()
            ),
            mock.patch.object(self.resource, 'handle_exception') as handler,
        ):
            self.assertRaises(
                RuntimeError, self.request().get_response, self.resource
            )
        handler.assert_not_called()

    def test_hooks_and_error_finalization(self):
        events = []

        class CustomResource(resource.Resource):
            def process_request(self, request):
                events.append('request')
                request.environ['custom'] = True

            def get_action_args(self, environ):
                self_custom = environ['custom']
                return {'action': 'show', 'custom': self_custom}

            def dispatch(self, method, request, action_args):
                events.append('dispatch')
                return super().dispatch(method, request, action_args)

            def handle_exception(self, request, error):
                events.append('error')
                if isinstance(error, ValueError):
                    return webob.Response(status=422)
                return super().handle_exception(request, error)

            def process_response(self, request, response):
                events.append('response')
                response.headers['X-API'] = 'custom'
                return response

        app = CustomResource(self.controller)
        self.request().get_response(app)
        self.controller.show.assert_called_once_with(req=mock.ANY, custom=True)
        self.assertEqual(['request', 'dispatch', 'response'], events)
        events.clear()
        self.controller.show.side_effect = ValueError()
        response = self.request().get_response(app)
        self.assertEqual(422, response.status_int)
        self.assertEqual('custom', response.headers['X-API'])
        self.assertEqual(['request', 'dispatch', 'error', 'response'], events)
