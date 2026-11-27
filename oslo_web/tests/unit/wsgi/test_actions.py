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

import json
from unittest import mock

import routes
import webob
import webob.exc

from oslo_web.tests import base
from oslo_web.wsgi import actions
from oslo_web.wsgi import resource
from oslo_web.wsgi import router


class ActionMetadataTest(base.BaseTestCase):
    def test_decorator_preserves_method(self):
        def method(req, body):
            return body

        self.assertIs(method, actions.action('restart')(method))
        self.assertEqual('restart', getattr(method, 'wsgi_action'))

    def test_invalid_name(self):
        for name in ('', None, 42):
            with self.subTest(name=name):
                self.assertRaises(ValueError, actions.action, name)

    def test_inheritance_and_overrides(self):
        class Parent:
            @actions.action('inherited')
            def inherited(self):
                pass

            @actions.action('removed')
            def removed(self):
                pass

            @actions.action('old')
            def renamed(self):
                pass

        class Child(Parent):
            @actions.action('new')
            def renamed(self):
                pass

        setattr(Child, 'removed', None)
        self.assertEqual(
            {'inherited': 'inherited', 'new': 'renamed'},
            actions.collect_actions(Child),
        )
        self.assertEqual(
            {'inherited': 'inherited', 'removed': 'removed', 'old': 'renamed'},
            actions.collect_actions(Parent),
        )

    def test_undecorated_override_removes_action(self):
        class Parent:
            @actions.action('restart')
            def restart(self):
                pass

        class Child(Parent):
            def restart(self):
                pass

        self.assertEqual({}, actions.collect_actions(Child))
        snapshot = actions.collect_actions(Parent)
        snapshot.clear()
        self.assertEqual(
            {'restart': 'restart'}, actions.collect_actions(Parent)
        )

    def test_multiple_inheritance_uses_mro(self):
        class Root:
            @actions.action('root')
            def handler(self):
                pass

        class Left(Root):
            @actions.action('left')
            def handler(self):
                pass

        class Right(Root):
            @actions.action('right')
            def handler(self):
                pass

        class Child(Left, Right):
            pass

        self.assertEqual({'left': 'handler'}, actions.collect_actions(Child))

    def test_duplicate_actions_rejected(self):
        class Parent:
            @actions.action('restart')
            def first(self):
                pass

        class Child(Parent):
            @actions.action('restart')
            def second(self):
                pass

        self.assertRaises(ValueError, resource.Resource, Child())

    def test_properties_not_evaluated(self):
        class Controller:
            @property
            def property(self):
                raise AssertionError('must not be evaluated')

        self.assertEqual({}, actions.collect_actions(Controller))


class ActionDispatchTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.calls = mock.Mock(return_value={'ok': True})
        calls = self.calls

        class Controller:
            @actions.action('restart')
            def _restart(self, req, id, body):
                return calls(req=req, id=id, body=body)

        self.controller = Controller()
        self.resource = resource.Resource(self.controller)

    def request(
        self, body=b'{"restart": null}', action='action', headers=None
    ):
        request = webob.Request.blank(
            '/', method='POST', body=body, headers=headers or {}
        )
        request.environ['wsgiorg.routing_args'] = (
            (),
            {'controller': self.resource, 'action': action, 'id': '123'},
        )
        return request

    def test_action_dispatch_decodes_once(self):
        body = {'restart': {'force': True}}
        with mock.patch.object(
            self.resource, 'deserialize', wraps=self.resource.deserialize
        ) as deserialize:
            response = self.request(json.dumps(body).encode()).get_response(
                self.resource
            )
        self.assertEqual({'ok': True}, response.json)
        self.calls.assert_called_once_with(req=mock.ANY, id='123', body=body)
        deserialize.assert_called_once()

    def test_inherited_actions_bind_to_each_controller(self):
        calls = self.calls

        class Parent:
            @actions.action('restart')
            def _restart(self, req, id, body):
                return calls(owner=self, req=req, id=id, body=body)

        class Child(Parent):
            pass

        first, second = Child(), Child()
        for controller in (first, second):
            response = self.request().get_response(
                resource.Resource(controller)
            )
            self.assertEqual(200, response.status_int)
            self.assertIs(controller, calls.call_args.kwargs['owner'])
        self.assertEqual(2, calls.call_count)

    def test_routes_integration(self):
        mapper = routes.Mapper()
        mapper.connect(
            'action',
            '/items/{id}/action',
            controller=self.resource,
            action='action',
            conditions={'method': ['POST']},
        )
        request = webob.Request.blank(
            '/items/123/action', method='POST', json_body={'restart': None}
        )
        response = request.get_response(router.Router(mapper))
        self.assertEqual(200, response.status_int)
        self.calls.assert_called_once_with(
            req=mock.ANY, id='123', body={'restart': None}
        )

    def test_bad_action_envelopes(self):
        for body in (
            b'',
            b'{',
            b'{}',
            b'[]',
            b'[1]',
            b'null',
            b'"x"',
            b'1',
            b'{"restart": null, "other": null}',
        ):
            with self.subTest(body=body):
                response = self.request(body).get_response(self.resource)
                self.assertEqual(400, response.status_int)
        self.calls.assert_not_called()

    def test_body_errors_precede_missing_method_errors(self):
        response = self.request(b'{', action='missing').get_response(
            self.resource
        )
        self.assertEqual(400, response.status_int)
        self.calls.assert_not_called()

    def test_unknown_action(self):
        response = self.request(b'{"unknown": null}').get_response(
            self.resource
        )
        self.assertEqual(400, response.status_int)
        self.calls.assert_not_called()

    def test_no_fallback_for_other_routes_or_private_names(self):
        for action in ('missing', 'restart', '_restart'):
            with self.subTest(action=action):
                response = self.request(action=action).get_response(
                    self.resource
                )
                self.assertEqual(404, response.status_int)
        self.calls.assert_not_called()

    def test_no_actions_is_not_found(self):
        response = self.request().get_response(resource.Resource(object()))
        self.assertEqual(404, response.status_int)

    def test_explicit_method_takes_precedence(self):
        calls = self.calls

        class Controller:
            @actions.action('restart')
            def _restart(self, req, id, body):
                return calls(req=req, id=id, body=body)

            def action(self, req, id, body):
                return {'explicit': body}

        response = self.request(b'{"unregistered": null}').get_response(
            resource.Resource(Controller())
        )
        self.assertEqual({'explicit': {'unregistered': None}}, response.json)
        self.calls.assert_not_called()

    def test_negotiation_errors(self):
        for headers, status in (
            ({'Content-Type': 'text/plain'}, 415),
            ({'Accept': 'text/plain'}, 406),
        ):
            with self.subTest(headers=headers):
                response = self.request(headers=headers).get_response(
                    self.resource
                )
                self.assertEqual(status, response.status_int)
        self.calls.assert_not_called()

    def test_action_errors_use_pipeline_hooks(self):
        class CustomResource(resource.Resource):
            def process_response(self, request, response):
                response.headers['X-API'] = 'custom'
                return response

        app = CustomResource(self.controller)
        self.calls.side_effect = webob.exc.HTTPConflict()
        response = self.request().get_response(app)
        self.assertEqual(409, response.status_int)
        self.assertEqual('custom', response.headers['X-API'])
        self.calls.side_effect = TypeError('implementation bug')
        self.assertRaises(TypeError, self.request().get_response, app)

    def test_custom_action_envelope(self):
        class CustomResource(resource.Resource):
            def get_action_name(self, request, body):
                return 'restart'

        response = self.request(b'[]').get_response(
            CustomResource(self.controller)
        )
        self.assertEqual(200, response.status_int)
        self.calls.assert_called_once_with(req=mock.ANY, id='123', body=[])
