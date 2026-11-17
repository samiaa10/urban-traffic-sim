# Map data: OpenStreetMap, under the ODbL

`norwich_graph.pkl` is a road graph of Norwich derived from **OpenStreetMap** data
(© OpenStreetMap contributors), downloaded on 8 September 2026 through the Overpass
API with OSMnx and converted by `backend/routing/osm_loader.py`.

OpenStreetMap data is available under the
[Open Database Licence (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/);
see <https://www.openstreetmap.org/copyright>. The graph is a Derivative Database of
it, so **the files in this folder are licensed under the ODbL**, not under the terms
of the project's code. If you share them or a database made from them, credit
OpenStreetMap and keep that database under the ODbL.

`source/` holds the raw OpenStreetMap responses the graph was built from, gzipped
exactly as OSMnx saved them (the Nominatim boundary of Norwich and the Overpass road
data, 8 September 2026); they are OpenStreetMap data, under the ODbL as well.
`python -m backend.routing.osm_loader --cache-only` rebuilds the graph from them.

How the graph is made and what it contains: [docs/road_network.md](../../docs/road_network.md).
