# Run report: finished

- **Detail:** Agent called finish
- **Steps:** 15 of 50
- **Tokens:** 150,410 of 2,000,000
- **Cost:** $0.1128 of $2.00
- **Time:** 105.0s
- **Models:** claude-sonnet-5-5
- **Verified:** True (fails without change: True, passes with change: True)
- **Plan:** approve by auto (see plan.md)
- **Reviews:** approve
- **Test-weakening flags:** none

## Task

features_from_xml fails on export containing incomplete multipolygon
### Contributing guidelines

- [x] I understand the contributing guidelines

### Documentation

- [x] My problem is not addressed by the documentation or examples

### Existing issues

- [x] My problem does not appear in an existing issue

### What operating system and Python version are you using?

Python 3.14

### What OSMnx version are you using?

2.0.5

### Environment packages and versions

```shell
Package            Version
------------------ -----------
certifi            2025.7.14
charset-normalizer 3.4.2
contourpy          1.3.3
cycler             0.12.1
fonttools          4.59.0
geopandas          1.1.1
idna               3.10
iniconfig          2.1.0
kiwisolver         1.4.8
matplotlib         3.10.5
mock               5.2.0
networkx           3.5
numpy              2.3.2
osmium             4.0.2
osmnx              2.0.5
packaging          25.0
pandas             2.2.3
pillow             11.3.0
pip                25.1.1
pluggy             1.6.0
Pygments           2.19.2
pyogrio            0.11.1
pyparsing          3.2.3
pyproj             3.7.1
pytest             8.4.1
python-dateutil    2.9.0.post0
pytz               2025.2
requests           2.32.4
shapely            2.1.1
six                1.17.0
tzdata             2025.2
urllib3            2.5.0
```

### How did you install OSMnx?

pip or uv (via PyPI)

### Problem description

`featuress_from_xml` will fail with the following stacktrace if run on an export that contains an incomplete multipolygon.
```
Traceback (most recent call last):
  File "<python-input-5>", line 2, in <module>
    osmnx.features_from_xml("./osm_export_xml.osm")
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/osmnx/features.py", line 374, in features_from_xml
    gdf = _create_gdf(response_jsons, polygon, tags)
  File "/usr/local/lib/python3.13/site-packages/osmnx/features.py", line 420, in _create_gdf
    data=_process_features(elements, set(tags.keys())),
         ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/osmnx/features.py", line 503, in _process_features
    relation["geometry"] = _build_relation_geometry(relation.pop("members"), way_geoms)
                           ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.13/site-packages/osmnx/features.py", line 601, in _build_relation_geometry
    geom = way_geoms[member["ref"]]
           ~~~~~~~~~^^^^^^^^^^^^^^^
KeyError: 44098878
```
This came up while testing a regression in a test on https://github.com/chrieke/prettymapp when updating from osmnx v1 to v2. In v1 (tested on 1.9.3) it succeeds with no error, seemingly having ignored the relation. In v2 (tested on 2.0.0 and 2.0.5) it errors.

If you move/zoom the export so that the entire multipolygon is exported then osmnx ingests it correctly. For example around https://www.openstreetmap.org/export#map=19/52.508763/13.387299 it's rather easy to trigger due to the large number of multipolygons.

### Minimal standalone reproducible example

```python
Export an .osm file that only partially contains a relation. For example https://github.com/chrieke/prettymapp/blob/3a6ef573f343718f001be8ee8038c15b687876f9/prettymapp/tests/mock_data/osm_export_xml.osm

Run

import osmnx
osmnx.features_from_xml("./osm_export_xml.osm")
```

## Agent summary

`_build_relation_geometry` in osmnx/features.py now skips member ways that aren't in the response and logs a warning. Before, it raised KeyError. I added a fixture, tests/input_data/incomplete_multipolygon.osm, and an assertion in `test_features`. The fixture has a relation that references a missing way. The new assertion failed with `KeyError: 99999` before the fix and passes after it. I ran only `test_features`, not the full test file.

Partway through, my first test edit and fixture disappeared from the working tree. `git status` was clean and my edit to features.py had been refused. I recreated both and re-ran the test to see it fail before applying the fix.

## Diff

```diff
diff --git a/osmnx/features.py b/osmnx/features.py
index ca472816..ad047fbd 100644
--- a/osmnx/features.py
+++ b/osmnx/features.py
@@ -598,6 +598,11 @@ def _build_relation_geometry(
     # sort member geometries by member role and geometry type
     for member in members:
         if member["type"] == "way":
+            if member["ref"] not in way_geoms:
+                # member way is missing from the response (incomplete relation)
+                msg = f"Skipping member way {member['ref']} missing from response"
+                utils.log(msg, level=lg.WARNING)
+                continue
             geom = way_geoms[member["ref"]]
             role = member["role"]
             if role == "outer" and geom.geom_type == "LineString":
diff --git a/tests/input_data/incomplete_multipolygon.osm b/tests/input_data/incomplete_multipolygon.osm
new file mode 100644
index 00000000..d9b2d8c4
--- /dev/null
+++ b/tests/input_data/incomplete_multipolygon.osm
@@ -0,0 +1,19 @@
+<?xml version='1.0' encoding='UTF-8'?>
+<osm version="0.6" generator="test">
+	<node id="1" version="1" lat="48.0" lon="10.0"/>
+	<node id="2" version="1" lat="48.0" lon="10.001"/>
+	<node id="3" version="1" lat="48.001" lon="10.001"/>
+	<way id="10" version="1">
+		<nd ref="1"/>
+		<nd ref="2"/>
+		<nd ref="3"/>
+		<nd ref="1"/>
+		<tag k="building" v="yes"/>
+	</way>
+	<relation id="100" version="1">
+		<member type="way" ref="10" role="outer"/>
+		<member type="way" ref="99999" role="outer"/>
+		<tag k="type" v="multipolygon"/>
+		<tag k="landuse" v="forest"/>
+	</relation>
+</osm>
diff --git a/tests/test_osmnx.py b/tests/test_osmnx.py
index f32e358c..500deb5d 100755
--- a/tests/test_osmnx.py
+++ b/tests/test_osmnx.py
@@ -736,6 +736,10 @@ def test_features() -> None:
     # features_from_xml - tests error handling of clipped XMLs with incomplete geometry
     gdf = ox.features_from_xml("tests/input_data/planet_10.068,48.135_10.071,48.137.osm")
 
+    # features_from_xml - relation with a member way missing from the XML
+    gdf = ox.features_from_xml("tests/input_data/incomplete_multipolygon.osm")
+    assert len(gdf) == 2
+
     # test loading a geodataframe from a local .osm xml file
     with bz2.BZ2File("tests/input_data/West-Oakland.osm.bz2") as f:
         handle, temp_filename = tempfile.mkstemp(suffix=".osm")
```
