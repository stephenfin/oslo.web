====================
Microversion support
====================

``oslo_web.microversion`` provides a generic versioned-value registry using
``microversion_parse.VersionRange`` for range matching. It does not negotiate
versions or introduce new version or range types. Use
``microversion_parse.Version`` and ``microversion_parse.parse_version_string``
for concrete versions, and
``microversion_parse.middleware.MicroversionMiddleware`` for WSGI negotiation.
Resolve ``latest`` during negotiation, before looking up a value.

For middleware configuration and PasteDeploy integration, see the
`microversion-parse documentation
<https://docs.openstack.org/microversion-parse/latest/#pastedeploy-integration>`_.
Services can also retain their existing negotiation and supply its result
through ``version_getter``.

Ranges and selection
--------------------

Bounds are inclusive. An omitted bound is unrestricted within the supplied
bound's major API version, not a default or negotiated service version.
Omitting both bounds registers a version-independent value; decorators with
no bounds likewise apply without consulting a version getter. This is handled
separately from ``VersionRange``, which requires at least one bound.
Comparisons use major/minor pairs, so ``2.10`` is later than ``2.9``. Invalid
bounds and overlapping registrations are rejected; a failed registration
leaves the registry unchanged.

The registry can hold schemas, handlers, status codes, or other values::

    import microversion_parse

    from oslo_web import microversion

    statuses = microversion.VersionedRegistry[int]()
    statuses.add(
        202,
        microversion_parse.Version(2, 1),
        microversion_parse.Version(2, 9),
    )
    statuses.add(200, microversion_parse.Version(2, 10))
    status = statuses.get(microversion_parse.Version(2, 10))  # 200

``get()`` returns the registered value unchanged, or ``None`` if no range
matches. Prefer non-``None`` values when the caller must distinguish a match
from missing coverage. Handler selection does not invoke or bind handlers.

Gaps and overlaps between registrations are rejected. Add adjacent ranges in
ascending or descending order. Coverage is checked between registrations, not
outside their bounds.
Adjacency means consecutive minor versions within the same major version;
there is no inferred final minor version before a major rollover. Cross-major
ranges are rejected by ``VersionRange``, and registrations across major
versions are rejected by the registry's contiguous-coverage policy.

Validation integration
-----------------------

Request-query, request-body, and response-body decorators accept ``Version``
objects for their bounds and from their explicit ``version_getter`` callback::

    import microversion_parse

    from oslo_web.validation import decorators

    @decorators.request_body_schema(
        {'type': 'object', 'required': ['name']},
        min_version=microversion_parse.Version(2, 10),
        version_getter=lambda req, body: req.environ['compute.microversion'],
    )
    def create(req, body):
        return body

The getter runs with the decorated function's arguments. In this example,
``microversion-parse`` middleware configured with service type ``compute`` has
already populated the request environment. Other frameworks can supply a
getter for their own negotiated-version storage.

Decorator schema metadata uses the same registry and range semantics.
``decorators.Schemas`` and stacked decorators require contiguous coverage.

Bounds and getter results must be ``microversion_parse.Version`` objects.
For Ironic-style integer minor versions, use matching major/minor bounds
and adapt the stored value in the getter. For the signature above::

    def version_getter(req, body):
        return microversion_parse.Version(1, req.version.minor)

Service adapters
----------------

Legacy request headers can be configured on the negotiation middleware;
legacy response headers remain service-owned. Keep experimental opt-in
separate from the concrete version returned by ``version_getter``. Gate only
handlers marked experimental: opting in must not block ordinary handlers or
their validation schemas.

When selecting handlers, translate a missing registry match (``None``) into
the service's own missing-handler error. This is distinct from
``CoverageError``, which rejects invalid registrations, and from negotiation
errors for unsupported service versions. The registry neither invokes
handlers nor translates exceptions into HTTP responses.

See :doc:`/reference/microversion` for the API reference.
