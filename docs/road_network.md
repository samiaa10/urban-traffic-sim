# The Norwich road network and its attributes

The map (`data/osm/norwich_graph.pkl`) is the drivable road network of Norwich from OpenStreetMap, downloaded with osmnx on 8 September 2026. Every directed road keeps its length (the edge weight routing has always used) and, since the OSM road attributes work, what OpenStreetMap says about it: its class, speed limit, lanes, whether it is one-way, and its name.

## Rebuilding it

```bash
python -m backend.routing.osm_loader --cache-only   # from the saved OpenStreetMap response in cache/
python -m backend.routing.road_report --save        # refresh the tables below
```

The raw OpenStreetMap responses the map was made from (the Nominatim boundary of Norwich and the Overpass road data, 8 September 2026) are committed, gzipped exactly as osmnx saved them, in `data/osm/source/` (777 KB; OpenStreetMap data under the ODbL, see `data/osm/README.md`). The loader restores them into osmnx's `cache/` when missing, and `--cache-only` refuses to download anything, so on any machine the map is rebuilt from exactly that data: the same 5,058 junctions (same ids and coordinates) and the same 10,980 roads (same lengths) as the map saved before attributes were kept (checked when the attributes were added, and by `backend/tests/test_road_attributes.py`). Without `--cache-only` a request not in the cache would download today's OpenStreetMap, which would be a different network.

Licence: OpenStreetMap data is under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). The graph is a Derivative Database, so the graph file and the source responses stay under the ODbL with credit to OpenStreetMap (`data/osm/README.md`); the project's code is separate. The web map credits "© OpenStreetMap contributors" with a link to openstreetmap.org/copyright, as the [attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines) ask.

## From tags to numbers (`backend/routing/road_attributes.py`)

| Attribute | How it is read | When it is missing or unusable |
|---|---|---|
| Speed limit (`maxspeed`) | `"20 mph"` is 8.94 m/s; a bare number is km/h (the OpenStreetMap convention); the UK national-limit codes (`GB:nsl_single` 60 mph, `GB:nsl_dual` 70 mph) are understood. Several values (osmnx joins OpenStreetMap ways into one road) or `"30 mph;20 mph"`: the lowest, because a vehicle must obey the slowest stretch | The UK built-up default for the road's class: 30 mph, 20 mph on living streets, 70 mph on motorways. `none` is not a valid UK limit and is treated as missing |
| Lanes (`lanes`) | On a two-way road the tag counts both directions, so each direction gets half (at least 1); a one-way road gets them all. Several values: the lowest (the bottleneck) | 1 per direction; 2 on one-way trunk and primary roads (dual carriageways) |
| Road class (`highway`) | Several classes: the most important (primary before residential) | `road` |
| One-way (`oneway`) | As osmnx gives it: a two-way road is two directed roads, a one-way road one | - |
| Name (`name`) | Several names are joined with " / "; without a name, the road number (`ref`, for example A147) | None |

Parallel roads: where OpenStreetMap has two roads between the same two junctions, both stay in the graph (as before) and the attributes kept are the shorter road's, the one routing uses.

## What the map knows

<!-- road-report:start -->
5,058 junctions, 10,980 directed roads (10,881 junction pairs with attributes; where OpenStreetMap has two parallel roads between the same junctions, the shorter one's).

| Attribute | From OpenStreetMap | Default used |
|---|---|---|
| Speed limit | 10,685 (98.2%) | 196 |
| Lanes | 2,587 (23.8%) | 8,294 |
| Name (or road number) | 10,810 (99.3%) | 71 have none |
| One-way | 843 one-way, 10,038 two-way | - |

| Speed limit | Roads | Share |
|---|---|---|
| 5 mph | 16 | 0.1% |
| 10 mph | 62 | 0.6% |
| 15 mph | 6 | 0.1% |
| 20 mph | 6,328 | 58.2% |
| 30 mph | 4,298 | 39.5% |
| 40 mph | 171 | 1.6% |

| Road class | Roads | Lanes per direction (mean) |
|---|---|---|
| residential | 7,764 | 1.00 |
| unclassified | 990 | 1.02 |
| tertiary | 883 | 1.04 |
| primary | 691 | 1.24 |
| trunk | 353 | 1.38 |
| secondary | 143 | 1.03 |
| living_street | 42 | 1.00 |
| trunk_link | 12 | 1.42 |
| primary_link | 2 | 1.00 |
| tertiary_link | 1 | 1.00 |
<!-- road-report:end -->

## How the attributes are used

The osm traffic model (`backend/simulation/road_model.py`) uses them; the simple model (the original rules) ignores them. See [the architecture guide](ARCHITECTURE.md#traffic-models) for the model itself and [the traffic benchmark](traffic_benchmark.md) for its effect.
