# Recorded runs for `spec-to-pr replay`

`osmnx-1334/` is an unedited run from the SWE-bench Live pilot: the full harness (verification, skills,
planning, context) on Claude Sonnet 5.5, fixing [gboeing/osmnx](https://github.com/gboeing/osmnx) issue
"features_from_xml fails on export containing incomplete multipolygon". It took 15 steps, 105 seconds and
$0.11, and the hidden tests passed.

    uv run spec-to-pr replay examples/replay/osmnx-1334

The trajectory quotes parts of OSMnx's source and tests. OSMnx is MIT-licensed, copyright Geoff Boeing.
