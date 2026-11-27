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

import functools
from http import HTTPStatus
from unittest import mock

import microversion_parse
import microversion_parse.middleware
import webob
import webob.exc

from oslo_web import microversion
from oslo_web.tests import base
from oslo_web.wsgi import actions
from oslo_web.wsgi import decorators
from oslo_web.wsgi import resource


class ResponseDecoratorTest(base.BaseTestCase):
    def test_method_unchanged(self):
        def method(req):
            """Controller documentation."""
            return {'ok': True}

        self.assertIs(method, decorators.response(HTTPStatus.CREATED)(method))
        self.assertEqual(201, getattr(method, 'wsgi_code'))
        self.assertEqual({'ok': True}, method(None))
        self.assertEqual('Controller documentation.', method.__doc__)

    def test_invalid_code(self):
        for code in (None, '202', 202.0, True, False, 99, 600):
            with self.subTest(code=code):
                self.assertRaises(ValueError, decorators.response, code)

    def test_redecoration_rejects_overlap(self):
        @decorators.response(202)
        def method(req):
            pass

        self.assertRaises(
            microversion.CoverageError, decorators.response(201), method
        )
        self.assertEqual(202, getattr(method, 'wsgi_code'))


def status_version(request):
    return request.environ['compute.microversion']


service_response = functools.partial(
    decorators.response, version_getter=status_version
)


class VersionedResponseTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.handler = mock.Mock(return_value=None)
        handler = self.handler

        class Controller:
            @service_response(
                204, min_version=microversion_parse.Version(2, 20)
            )
            @service_response(
                202, max_version=microversion_parse.Version(2, 19)
            )
            def show(self, req):
                return handler(req=req)

        self.controller = Controller()
        self.resource = resource.Resource(self.controller)

    def request(
        self, version, app=None, action='show', method='GET', body=b''
    ):
        request = webob.Request.blank('/', method=method, body=body)
        request.environ['compute.microversion'] = version
        request.environ['wsgiorg.routing_args'] = ((), {'action': action})
        return request.get_response(app or self.resource)

    def test_range_selection(self):
        for minor, status in ((0, 202), (19, 202), (20, 204), (99, 204)):
            with self.subTest(minor=minor):
                response = self.request(microversion_parse.Version(2, minor))
                self.assertEqual(status, response.status_int)
                self.assertEqual(b'', response.body)

    def test_reverse_registration_order(self):
        @service_response(202, max_version=microversion_parse.Version(2, 19))
        @service_response(204, min_version=microversion_parse.Version(2, 20))
        def method(req):
            pass

        request = webob.Request.blank('/')
        for minor, status in ((19, 202), (20, 204)):
            request.environ['compute.microversion'] = (
                microversion_parse.Version(2, minor)
            )
            self.assertEqual(status, getattr(method, 'wsgi_codes')(request))

    def test_invalid_coverage_is_rejected_atomically(self):
        @service_response(
            202,
            min_version=microversion_parse.Version(2, 10),
            max_version=microversion_parse.Version(2, 19),
        )
        def method(req):
            pass

        original = getattr(method, 'wsgi_codes')
        for minimum, maximum in (
            (microversion_parse.Version(2, 19), None),
            (microversion_parse.Version(2, 21), None),
            (microversion_parse.Version(3, 0), None),
            (None, None),
        ):
            with self.subTest(minimum=minimum):
                decorate = decorators.response(
                    204, minimum, maximum, version_getter=status_version
                )
                self.assertRaises(microversion.CoverageError, decorate, method)
                self.assertIs(original, getattr(method, 'wsgi_codes'))
        decorators.response(
            204,
            min_version=microversion_parse.Version(2, 20),
            version_getter=status_version,
        )(method)

    def test_unversioned_cannot_mix_with_bounded(self):
        @decorators.response(202)
        def method(req):
            pass

        self.assertRaises(
            microversion.CoverageError,
            decorators.response(
                204,
                min_version=microversion_parse.Version(2, 20),
                version_getter=status_version,
            ),
            method,
        )

    def test_invalid_configuration(self):
        self.assertRaises(
            ValueError,
            decorators.response,
            202,
            min_version=microversion_parse.Version(2, 1),
        )
        for minimum, maximum, error in (
            ('2.1', None, TypeError),
            (microversion_parse.Version(2, -1), None, ValueError),
            (
                microversion_parse.Version(2, 20),
                microversion_parse.Version(2, 19),
                ValueError,
            ),
            (
                microversion_parse.Version(2, 1),
                microversion_parse.Version(3, 0),
                ValueError,
            ),
        ):
            self.assertRaises(
                error,
                decorators.response,
                202,
                minimum,
                maximum,
                version_getter=status_version,
            )

    def test_mismatched_getters_rejected(self):
        self.assertRaises(
            ValueError,
            decorators.response(
                201,
                min_version=microversion_parse.Version(2, 100),
                version_getter=mock.Mock(),
            ),
            self.controller.show,
        )

    def test_unmatched_version_is_finalized_as_500(self):
        class CustomResource(resource.Resource):
            def process_response(self, request, response):
                response.headers['X-Finalized'] = 'yes'
                return response

        response = self.request(
            microversion_parse.Version(3, 0), CustomResource(self.controller)
        )
        self.assertEqual(500, response.status_int)
        self.assertEqual('yes', response.headers['X-Finalized'])

    def test_finite_coverage_does_not_require_external_ranges(self):
        class Controller:
            @service_response(
                202,
                min_version=microversion_parse.Version(2, 10),
                max_version=microversion_parse.Version(2, 19),
            )
            def show(self, req):
                pass

        app = resource.Resource(Controller())
        for minor, status in ((9, 500), (10, 202), (19, 202), (20, 500)):
            self.assertEqual(
                status,
                self.request(
                    microversion_parse.Version(2, minor), app
                ).status_int,
            )

    def test_unversioned_status_does_not_call_getter(self):
        getter = mock.Mock(side_effect=AssertionError('must not be called'))

        class Controller:
            service_response = functools.partial(
                decorators.response, version_getter=getter
            )

            @service_response(202)
            def show(self, req):
                pass

        response = self.request(None, resource.Resource(Controller()))
        self.assertEqual(202, response.status_int)
        getter.assert_not_called()

    def test_invalid_getter_results_propagate(self):
        for version in (None, '2.20', 2, (2, 20)):
            with self.subTest(version=version):
                self.assertRaises(TypeError, self.request, version)
        self.assertRaises(
            ValueError, self.request, microversion_parse.Version(2, -1)
        )

    def test_explicit_responses_bypass_version_selection(self):
        # Even an invalid negotiated version is ignored for explicit responses.
        for result in (webob.Response(status=201), webob.exc.HTTPConflict()):
            self.handler.return_value = result
            response = self.request(None)
            self.assertEqual(result.status_int, response.status_int)
        self.handler.side_effect = webob.exc.HTTPForbidden()
        self.assertEqual(403, self.request(None).status_int)

    def test_wrapped_metadata_is_not_shared_mutably(self):
        @service_response(202, max_version=microversion_parse.Version(2, 19))
        def original(req):
            pass

        @service_response(204, min_version=microversion_parse.Version(2, 20))
        @functools.wraps(original)
        def wrapped(req):
            return original(req)

        request = webob.Request.blank('/')
        request.environ['compute.microversion'] = microversion_parse.Version(
            2, 20
        )
        self.assertEqual(204, getattr(wrapped, 'wsgi_codes')(request))
        self.assertRaises(
            webob.exc.HTTPInternalServerError,
            getattr(original, 'wsgi_codes'),
            request,
        )

    def test_inheritance_and_action_version_guards(self):
        class Parent:
            @actions.action('delete')
            @service_response(
                204, min_version=microversion_parse.Version(2, 20)
            )
            @service_api_version('2.20')
            def delete(self, req, body):
                pass

        class Child(Parent):
            pass

        class Override(Parent):
            @actions.action('delete')
            @decorators.response(202)
            def delete(self, req, body):
                pass

        for controller, minor, status in (
            (Child(), 19, 404),
            (Child(), 20, 204),
            (Override(), 19, 202),
        ):
            response = self.request(
                microversion_parse.Version(2, minor),
                resource.Resource(controller),
                action='action',
                method='POST',
                body=b'{"delete": null}',
            )
            self.assertEqual(status, response.status_int)


class ResponseStatusTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.handler = mock.Mock(return_value=None)
        handler = self.handler

        class Controller:
            @decorators.response(HTTPStatus.ACCEPTED)
            def show(self, req):
                return handler(req=req)

        self.controller = Controller()
        self.resource = resource.Resource(self.controller)

    def request(self, action='show', method='GET', body=b''):
        request = webob.Request.blank('/', method=method, body=body)
        request.environ['wsgiorg.routing_args'] = ((), {'action': action})
        return request

    def test_json_values_and_empty_response(self):
        values: tuple[object, ...] = ({'ok': True}, [], 'text', 0, False, None)
        for value in values:
            with self.subTest(value=value):
                self.handler.return_value = value
                response = self.request().get_response(self.resource)
                self.assertEqual(202, response.status_int)
                self.assertEqual('application/json', response.content_type)
                if value is None:
                    self.assertEqual(b'', response.body)
                else:
                    self.assertEqual(value, response.json)

    def test_no_content(self):
        class Controller:
            @decorators.response(HTTPStatus.NO_CONTENT)
            def delete(self, req):
                pass

        response = self.request(action='delete', method='DELETE').get_response(
            resource.Resource(Controller())
        )
        self.assertEqual(204, response.status_int)
        self.assertEqual(b'', response.body)

    def test_status_does_not_rewrite_serialized_body(self):
        class Controller:
            @decorators.response(HTTPStatus.NO_CONTENT)
            def show(self, req):
                return {'unchanged': True}

        response = self.request().get_response(resource.Resource(Controller()))
        self.assertEqual(204, response.status_int)
        self.assertEqual({'unchanged': True}, response.json)

    def test_head(self):
        self.handler.return_value = {'ok': True}
        response = self.request(method='HEAD').get_response(self.resource)
        self.assertEqual(202, response.status_int)
        self.assertEqual(b'', response.body)

    def test_explicit_response_and_stream_are_unchanged(self):
        consumed = []

        def stream():
            consumed.append(True)
            yield b'binary'

        chunks = stream()
        expected = webob.Response(
            status=201,
            app_iter=chunks,
            headers={
                'Content-Type': 'application/octet-stream',
                'X-Test': 'ok',
            },
        )
        self.handler.return_value = expected

        def finalize(request, response):
            self.assertIs(expected, response)
            self.assertIs(chunks, response.app_iter)
            self.assertEqual([], consumed)
            return response

        with (
            mock.patch.object(self.resource, 'process_response', finalize),
            mock.patch.object(self.resource, 'get_response_status') as status,
        ):
            response = self.request().get_response(self.resource)
        status.assert_not_called()
        self.assertEqual(201, response.status_int)
        self.assertEqual('ok', response.headers['X-Test'])
        self.assertEqual('application/octet-stream', response.content_type)
        self.assertEqual(b'binary', response.body)

    def test_http_exceptions_not_overridden(self):
        for raised in (False, True):
            with self.subTest(raised=raised):
                error = webob.exc.HTTPConflict()
                self.handler.return_value = error
                self.handler.side_effect = error if raised else None
                with mock.patch.object(
                    self.resource, 'get_response_status'
                ) as status:
                    response = self.request().get_response(self.resource)
                status.assert_not_called()
                self.assertEqual(409, response.status_int)

    def test_inherited_and_overridden_metadata(self):
        class Parent:
            @decorators.response(202)
            def show(self, req):
                pass

        class Inherited(Parent):
            pass

        class Undecorated(Parent):
            def show(self, req):
                return None

        class Redecorated(Parent):
            @decorators.response(201)
            def show(self, req):
                return {'created': True}

        for controller, status in (
            (Inherited(), 202),
            (Undecorated(), 200),
            (Redecorated(), 201),
        ):
            with self.subTest(controller=controller):
                response = self.request().get_response(
                    resource.Resource(controller)
                )
                self.assertEqual(status, response.status_int)

    def test_action_decorator_orders(self):
        class Controller:
            @actions.action('first')
            @decorators.response(201)
            def _first(self, req, body):
                return body

            @decorators.response(202)
            @actions.action('second')
            def _second(self, req, body):
                return body

        app = resource.Resource(Controller())
        for body, status in (
            (b'{"first": null}', 201),
            (b'{"second": null}', 202),
        ):
            with self.subTest(body=body):
                response = self.request('action', 'POST', body).get_response(
                    app
                )
                self.assertEqual(status, response.status_int)
                self.assertEqual(body, response.body)

    def test_status_selection_and_finalization_hooks(self):
        seen = []

        class CustomResource(resource.Resource):
            def get_response_status(self, request, method):
                seen.append(super().get_response_status(request, method))
                return 201

            def process_response(self, request, response):
                seen.append(response.status_int)
                response.headers['X-Test'] = 'finalized'
                return response

        response = self.request().get_response(CustomResource(self.controller))
        self.assertEqual([202, 201], seen)
        self.assertEqual(201, response.status_int)
        self.assertEqual('finalized', response.headers['X-Test'])

    def test_existing_serialization_hook(self):
        class CustomResource(resource.Resource):
            def serialize(self, request, result, content_type):
                return webob.Response(text='custom', status=203)

        response = self.request().get_response(CustomResource(self.controller))
        self.assertEqual(202, response.status_int)
        self.assertEqual('custom', response.text)

    def test_undecorated_serialization_status_preserved(self):
        class Controller:
            def show(self, req):
                pass

        class CustomResource(resource.Resource):
            def serialize(self, request, result, content_type):
                return webob.Response(status=203)

        response = self.request().get_response(CustomResource(Controller()))
        self.assertEqual(203, response.status_int)

    def test_status_selection_errors_use_error_hooks(self):
        with (
            mock.patch.object(
                self.resource,
                'get_response_status',
                side_effect=webob.exc.HTTPForbidden(),
            ),
            mock.patch.object(
                self.resource,
                'process_response',
                wraps=self.resource.process_response,
            ) as finalize,
        ):
            response = self.request().get_response(self.resource)
        self.assertEqual(403, response.status_int)
        finalize.assert_called_once()


def request_version(controller, req, **kwargs):
    return req.environ['compute.microversion']


def service_api_version(min_version=None, max_version=None):
    """Example service adapter hiding negotiation details from controllers."""
    return decorators.api_version(
        microversion_parse.parse_version_string(min_version)
        if min_version is not None
        else None,
        microversion_parse.parse_version_string(max_version)
        if max_version is not None
        else None,
        version_getter=request_version,
    )


class APIVersionTest(base.BaseTestCase):
    def test_inclusive_range_and_metadata(self):
        calls = mock.Mock(return_value='ok')
        getter = mock.Mock()
        minimum = microversion_parse.Version(2, 10)
        maximum = microversion_parse.Version(2, 20)

        service_api_version = functools.partial(
            decorators.api_version, version_getter=getter
        )

        @service_api_version(minimum, maximum)
        def method(req):
            """Controller documentation."""
            return calls(req)

        self.assertEqual('method', method.__name__)
        self.assertEqual('Controller documentation.', method.__doc__)
        self.assertEqual(minimum, getattr(method, 'min_version'))
        self.assertEqual(maximum, getattr(method, 'max_version'))
        self.assertEqual(
            microversion_parse.VersionRange(minimum, maximum),
            getattr(method, 'wsgi_version_range'),
        )
        for major, minor, allowed in (
            (2, 9, False),
            (2, 10, True),
            (2, 15, True),
            (2, 20, True),
            (2, 21, False),
            (3, 10, False),
        ):
            with self.subTest(major=major, minor=minor):
                getter.return_value = microversion_parse.Version(major, minor)
                calls.reset_mock()
                if allowed:
                    self.assertEqual('ok', method(req='request'))
                    calls.assert_called_once_with('request')
                else:
                    self.assertRaises(
                        webob.exc.HTTPNotFound, method, 'request'
                    )
                    calls.assert_not_called()

    def test_one_sided_ranges(self):
        for minimum, maximum, minors in (
            (microversion_parse.Version(2, 10), None, (10, 99)),
            (None, microversion_parse.Version(2, 10), (0, 10)),
        ):
            getter = mock.Mock()

            service_api_version = functools.partial(
                decorators.api_version, version_getter=getter
            )

            @service_api_version(minimum, maximum)
            def method():
                return 'ok'

            for minor in minors:
                getter.return_value = microversion_parse.Version(2, minor)
                self.assertEqual('ok', method())
            getter.return_value = microversion_parse.Version(3, 0)
            self.assertRaises(webob.exc.HTTPNotFound, method)

    def test_no_bounds_does_not_require_or_call_getter(self):
        getter = mock.Mock(side_effect=AssertionError('must not be called'))

        service_api_version = functools.partial(
            decorators.api_version, version_getter=getter
        )

        @service_api_version()
        def method():
            return 'ok'

        @decorators.api_version()
        def other():
            return 'other'

        self.assertEqual('ok', method())
        self.assertEqual('other', other())
        getter.assert_not_called()

    def test_invalid_configuration(self):
        self.assertRaises(
            ValueError,
            decorators.api_version,
            microversion_parse.Version(2, 1),
        )
        for minimum, maximum, error in (
            ('2.1', None, TypeError),
            (2, None, TypeError),
            (microversion_parse.Version(2, -1), None, ValueError),
            (
                microversion_parse.Version(2, 5),
                microversion_parse.Version(2, 4),
                ValueError,
            ),
            (
                microversion_parse.Version(2, 1),
                microversion_parse.Version(3, 1),
                ValueError,
            ),
        ):
            self.assertRaises(
                error,
                decorators.api_version,
                minimum,
                maximum,
                version_getter=mock.Mock(),
            )

    def test_repeated_declaration_rejected(self):
        @decorators.api_version()
        def method():
            pass

        self.assertRaises(ValueError, decorators.api_version(), method)

    def test_getter_errors_are_not_hidden(self):
        getter = mock.Mock()

        service_api_version = functools.partial(
            decorators.api_version, version_getter=getter
        )

        @service_api_version(microversion_parse.Version(2, 1))
        def method():
            raise AssertionError('must not be called')

        for value in (None, '2.1', 2, (2, 1)):
            getter.return_value = value
            self.assertRaises(TypeError, method)
        getter.return_value = microversion_parse.Version(2, -1)
        self.assertRaises(ValueError, method)
        getter.side_effect = KeyError('missing negotiated version')
        self.assertRaises(KeyError, method)

    def test_direct_method_calls_positional_and_keyword(self):
        class Controller:
            @service_api_version('2.10', '2.19')
            def show(self, req):
                return 'ok'

        controller = Controller()
        request = webob.Request.blank('/')
        request.environ['compute.microversion'] = microversion_parse.Version(
            2, 10
        )
        self.assertEqual('ok', controller.show(request))
        self.assertEqual('ok', controller.show(req=request))
        request.environ['compute.microversion'] = microversion_parse.Version(
            2, 9
        )
        self.assertRaises(webob.exc.HTTPNotFound, controller.show, request)
        self.assertRaises(webob.exc.HTTPNotFound, controller.show, req=request)
        request.environ['compute.microversion'] = microversion_parse.Version(
            2, 19
        )
        self.assertEqual('ok', controller.show(req=request))
        request.environ['compute.microversion'] = microversion_parse.Version(
            2, 20
        )
        self.assertRaises(webob.exc.HTTPNotFound, controller.show, req=request)
        self.assertEqual(
            microversion_parse.Version(2, 10),
            getattr(controller.show, 'min_version'),
        )
        self.assertEqual(
            microversion_parse.Version(2, 19),
            getattr(controller.show, 'max_version'),
        )


class VersionedResourceTest(base.BaseTestCase):
    def request(self, app, version, action='show', method='GET', body=b''):
        request = webob.Request.blank('/', method=method, body=body)
        request.environ['wsgiorg.routing_args'] = ((), {'action': action})
        request.environ['compute.microversion'] = version
        return request.get_response(app)

    def test_inherited_and_overridden_methods(self):
        class Parent:
            @service_api_version('2.10')
            def show(self, req):
                return {'ok': True}

        class Child(Parent):
            pass

        class Override(Parent):
            def show(self, req):
                return {'unrestricted': True}

        version = microversion_parse.Version(2, 9)
        self.assertEqual(
            404, self.request(resource.Resource(Child()), version).status_int
        )
        self.assertEqual(
            200,
            self.request(resource.Resource(Override()), version).status_int,
        )

    def test_action_and_status_decorator_orders(self):
        calls = mock.Mock()

        class Controller:
            @service_api_version('2.10')
            @decorators.response(202)
            @actions.action('first')
            def _first(self, req, body):
                calls()

            @actions.action('second')
            @decorators.response(202)
            @service_api_version('2.10')
            def _second(self, req, body):
                calls()

        class CustomResource(resource.Resource):
            def process_response(self, request, response):
                response.headers['X-Finalized'] = 'yes'
                return response

        app = CustomResource(Controller())
        for body in (b'{"first": null}', b'{"second": null}'):
            for minor, status in ((9, 404), (10, 202)):
                with self.subTest(body=body, minor=minor):
                    calls.reset_mock()
                    response = self.request(
                        app,
                        microversion_parse.Version(2, minor),
                        action='action',
                        method='POST',
                        body=body,
                    )
                    self.assertEqual(status, response.status_int)
                    self.assertEqual('yes', response.headers['X-Finalized'])
                    self.assertEqual(int(status == 202), calls.call_count)

    def test_microversion_middleware_integration(self):
        class Controller:
            @service_api_version('2.10')
            def show(self, req):
                return {'ok': True}

        app = microversion_parse.middleware.MicroversionMiddleware(
            resource.Resource(Controller()), 'compute', ['2.1', '2.10']
        )
        for version, status in (('2.1', 404), ('2.10', 200)):
            request = webob.Request.blank(
                '/', headers={'OpenStack-API-Version': f'compute {version}'}
            )
            request.environ['wsgiorg.routing_args'] = ((), {'action': 'show'})
            response = request.get_response(app)
            self.assertEqual(status, response.status_int)
            self.assertEqual(
                f'compute {version}', response.headers['OpenStack-API-Version']
            )
