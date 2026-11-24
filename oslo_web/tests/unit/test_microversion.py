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

import microversion_parse

from oslo_web import microversion
from oslo_web.tests import base


class TestVersionedRegistry(base.BaseTestCase):
    def test_one_sided_ranges_stay_within_major_version(self):
        for minimum, maximum in (
            (microversion_parse.Version(2, 1), None),
            (None, microversion_parse.Version(2, 9)),
        ):
            with self.subTest(minimum=minimum, maximum=maximum):
                registry = microversion.VersionedRegistry[str]()
                registry.add('value', minimum, maximum)
                self.assertEqual(
                    'value', registry.get(microversion_parse.Version(2, 5))
                )
                for version in (
                    microversion_parse.Version(1, 5),
                    microversion_parse.Version(3, 5),
                ):
                    self.assertIsNone(registry.get(version))

    def test_cross_major_range_is_rejected(self):
        registry = microversion.VersionedRegistry[str]()
        self.assertRaises(
            ValueError,
            registry.add,
            'invalid',
            microversion_parse.Version(1, 99),
            microversion_parse.Version(2, 1),
        )
        registry.validate()
        self.assertIsNone(registry.get(microversion_parse.Version(2, 0)))

    def test_version_independent_registration(self):
        registry = microversion.VersionedRegistry[str]()
        registry.add('all')
        registry.validate()
        for major in (0, 1, 2, 3):
            self.assertEqual(
                'all', registry.get(microversion_parse.Version(major, 0))
            )

    def test_invalid_lookup_versions(self):
        for populated in (False, True):
            registry = microversion.VersionedRegistry[str]()
            if populated:
                registry.add('all')
            for version in ('2.1', 'latest', 1, (2, 1), None):
                self.assertRaises(TypeError, registry.get, version)
            self.assertRaises(
                ValueError, registry.get, microversion_parse.Version(2, -1)
            )

    def test_selection_out_of_order(self):
        registry = microversion.VersionedRegistry[str]()
        registry.add('new', microversion_parse.Version(2, 10))
        registry.add('old', None, microversion_parse.Version(2, 9))
        for minor, expected in (
            (0, 'old'),
            (9, 'old'),
            (10, 'new'),
            (100, 'new'),
        ):
            self.assertEqual(
                expected, registry.get(microversion_parse.Version(2, minor))
            )

    def test_lookup_outside_coverage(self):
        registry = microversion.VersionedRegistry[int]()
        self.assertIsNone(registry.get(microversion_parse.Version(2, 0)))
        registry.add(
            201,
            microversion_parse.Version(2, 1),
            microversion_parse.Version(2, 1),
        )
        self.assertIsNone(registry.get(microversion_parse.Version(2, 0)))
        self.assertEqual(201, registry.get(microversion_parse.Version(2, 1)))
        self.assertIsNone(registry.get(microversion_parse.Version(2, 2)))
        self.assertIsNone(registry.get(microversion_parse.Version(2, 3)))

    def test_contiguous(self):
        registry = microversion.VersionedRegistry[str]()
        registry.add('new', microversion_parse.Version(2, 10))
        registry.add('old', None, microversion_parse.Version(2, 9))
        registry.validate()
        self.assertEqual('old', registry.get(microversion_parse.Version(2, 9)))
        self.assertEqual(
            'new', registry.get(microversion_parse.Version(2, 10))
        )

    def test_failed_registration_is_atomic(self):
        for lower, upper in (
            ((2, 2), (2, 3)),  # Shared inclusive endpoint.
            ((2, 1), (2, 2)),  # Duplicate.
            ((2, 0), (2, 3)),  # Enclosing range.
            ((2, 4), None),  # Gap.
            ((2, 3), (2, 2)),  # Reversed range.
            ((3, 0), None),  # No inferred cross-major adjacency.
        ):
            registry = microversion.VersionedRegistry[str]()
            registry.add(
                'original',
                microversion_parse.Version(2, 1),
                microversion_parse.Version(2, 2),
            )
            self.assertRaises(
                ValueError,
                registry.add,
                'invalid',
                microversion_parse.Version(*lower),
                microversion_parse.Version(*upper) if upper else None,
            )
            self.assertEqual(
                'original', registry.get(microversion_parse.Version(2, 1))
            )
            self.assertIsNone(registry.get(microversion_parse.Version(2, 3)))
            registry.validate()

    def test_version_independent_overlap_is_atomic(self):
        registry = microversion.VersionedRegistry[str]()
        registry.add('bounded', microversion_parse.Version(2, 1))
        self.assertRaises(microversion.CoverageError, registry.add, 'all')
        self.assertEqual(
            'bounded', registry.get(microversion_parse.Version(2, 1))
        )
        self.assertIsNone(registry.get(microversion_parse.Version(1, 0)))

    def test_unbounded_overlap(self):
        registry = microversion.VersionedRegistry[str]()
        registry.add('all')
        self.assertRaises(
            microversion.CoverageError,
            registry.add,
            'some',
            microversion_parse.Version(2, 1),
        )
        self.assertEqual('all', registry.get(microversion_parse.Version(2, 1)))

    def test_handler_identity(self):
        def handler():
            return 'result'

        registry = microversion.VersionedRegistry[object]()
        registry.add(handler)
        self.assertIs(handler, registry.get(microversion_parse.Version(2, 1)))
