## Files to change
- osmnx/features.py: `_build_relation_geometry` skips (and logs a warning for) member ways whose ref is not in `way_geoms` instead of raising KeyError.
- tests/test_osmnx.py: extend `test_features`.
- tests/input_data/incomplete_multipolygon.osm: new tiny XML fixture with a relation referencing a missing way.

## Steps
1. Add fixture + test assertion (already done; fails with KeyError: 99999).
2. Add the missing-member check in `_build_relation_geometry`.
3. Rerun test_features.

## Tests to add
In `test_features`, call `ox.features_from_xml` on the fixture and assert 2 features (building way + relation) are returned.

## Acceptance criteria
- features_from_xml no longer raises on relations with missing member ways; present members still build the geometry.
- test fails before the fix and passes after.