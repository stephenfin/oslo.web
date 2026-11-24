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

"""Version selection, independent of HTTP and controller stacks.

Use ``microversion_parse`` for version objects, ranges, parsing, and
negotiation. These utilities operate only on concrete, already-negotiated
versions.
"""

import itertools
from typing import Generic, TypeVar

import microversion_parse

T = TypeVar('T')


class CoverageError(ValueError):
    """Overlapping ranges or a gap in version coverage."""


class VersionedRegistry(Generic[T]):
    """Select handlers, schemas, statuses, or other values by API version.

    Overlaps and gaps are rejected. Successive registrations must meet at
    consecutive minor versions of the same major version. There is no assumed
    final minor version before a major rollover, so cross-major adjacency
    cannot be inferred and is rejected. No coverage outside the registered
    bounds is required. Register adjoining ranges in ascending or descending
    order. Omitting both bounds registers a version-independent value that
    cannot coexist with other entries.
    """

    def __init__(self) -> None:
        self._entries: list[
            tuple[microversion_parse.VersionRange | None, T]
        ] = []

    def add(
        self,
        value: T,
        minimum: microversion_parse.Version | None = None,
        maximum: microversion_parse.Version | None = None,
    ) -> None:
        """Register a value atomically, rejecting invalid coverage."""
        bounds = (
            microversion_parse.VersionRange(minimum, maximum)
            if minimum is not None or maximum is not None
            else None
        )
        entries = [*self._entries, (bounds, value)]
        entries.sort(
            key=lambda entry: (
                entry[0] is not None and entry[0].minimum is not None,
                entry[0].minimum if entry[0] is not None else None,
            )
        )
        self._validate(entries)
        self._entries = entries

    def _validate(
        self, entries: list[tuple[microversion_parse.VersionRange | None, T]]
    ) -> None:
        for (previous, _), (current, _) in itertools.pairwise(entries):
            if (
                previous is None
                or current is None
                or previous.overlaps(current)
            ):
                raise CoverageError('overlapping version coverage')
            upper, lower = previous.maximum, current.minimum
            if (
                upper is None
                or lower is None
                or upper.major != lower.major
                or lower.minor != upper.minor + 1
            ):
                raise CoverageError('gaps in version coverage')

    def validate(self) -> None:
        """Check that registered ranges are contiguous and do not overlap."""
        self._validate(self._entries)

    def get(self, version: microversion_parse.Version) -> T | None:
        """Return the matching value, or ``None`` when no range matches."""
        # Validate even when the registry is empty or version-independent.
        if not isinstance(version, microversion_parse.Version):
            raise TypeError('expected a microversion_parse.Version')
        microversion_parse.VersionRange(minimum=version)
        for bounds, value in self._entries:
            if bounds is None or version in bounds:
                return value
        return None
