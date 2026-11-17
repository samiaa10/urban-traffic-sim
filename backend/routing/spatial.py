"""A spatial index for "which junction is nearest this point?".

The brute-force answer checks every junction (5,058 on Norwich) on every
call. This uniform grid buckets junctions by latitude/longitude and
searches outwards ring by ring from the query's cell, stopping as soon as
no unsearched cell could hold anything closer. It returns exactly what the
brute-force scans returned, ties included:

* the same distance (straight line in degrees, the same formula as
  dispatch.nearest_junction and the optimiser's snapping),
* ties go to the junction that comes first in graph.nodes (what a scan
  with a strict "<" keeps, and what Python's stable sort keeps).

Why it is exact: a point in the query's cell is at Chebyshev ring 0. Any
junction in a cell at ring r + 1 or further is at least r cell-widths
away along latitude or longitude, so once r rings are searched and k
candidates closer than r x cell are known, nothing unsearched can beat or
tie them.

Built once per graph (weakly cached, so it goes when the graph goes).
"""

import math
import weakref


_INDEXES = weakref.WeakKeyDictionary()


def index_for(graph):
    """The grid index of a graph (built on first use)."""

    index = _INDEXES.get(graph)
    if index is None or index.size != len(graph.nodes):
        index = GridIndex(graph)
        _INDEXES[graph] = index
    return index


class GridIndex:

    # Average junctions per cell the cell size aims for
    PER_CELL = 4

    def __init__(self, graph):
        self.size = len(graph.nodes)
        self.cells = {}

        points = [
            (data["latitude"], data["longitude"], order, node)
            for order, (node, data) in enumerate(graph.nodes.items())
        ]

        if not points:
            self.points = []
            self.cell = 1.0
            self.origin = (0.0, 0.0)
            self.extent = (0, 0)
            return

        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        self.origin = (min(lats), min(lons))
        height = max(lats) - self.origin[0]
        width = max(lons) - self.origin[1]
        area = max(height * width, 1e-12)
        # At least 1/1000 of the map's size (and a tiny absolute floor), so a
        # map that is a single point or a line still gets usable cells
        # Cells hold about PER_CELL junctions; on a long, thin map (or a
        # line) the length-based size keeps the grid from being far finer
        # than the junctions themselves
        self.cell = max(
            math.sqrt(area * self.PER_CELL / len(points)),
            max(height, width) * self.PER_CELL / len(points),
            1e-6,
        )
        self.points = points

        for point in points:
            self.cells.setdefault(self._cell_of(point[0], point[1]), []).append(point)

        self.extent = (int(height / self.cell) + 1, int(width / self.cell) + 1)

    def _cell_of(self, latitude, longitude):
        return (
            math.floor((latitude - self.origin[0]) / self.cell),
            math.floor((longitude - self.origin[1]) / self.cell),
        )

    def _ring(self, centre, radius):
        row, column = centre
        if radius == 0:
            yield centre
            return
        for c in range(column - radius, column + radius + 1):
            yield (row - radius, c)
            yield (row + radius, c)
        for r in range(row - radius + 1, row + radius):
            yield (r, column - radius)
            yield (r, column + radius)

    def _max_radius(self, centre):
        """A ring radius beyond which no cell holds junctions."""
        rows, columns = self.extent
        row, column = centre
        return max(abs(row), abs(row - rows), abs(column), abs(column - columns)) + 1

    def nearest(self, latitude, longitude, count=1, squared=False):
        """The `count` nearest junctions, nearest first.

        Distance is sqrt(dlat^2 + dlon^2) (squared=True: dlat^2 + dlon^2,
        the optimiser's ordering); ties are broken by graph order.
        """

        if not self.cells or count < 1:
            return []

        centre = self._cell_of(latitude, longitude)
        limit = self._max_radius(centre)

        # Far outside the map, rings would mostly be empty: check every
        # junction instead (the same answer)
        if limit > 2 * max(self.extent) + 2:
            return self._scan(latitude, longitude, count, squared)

        seen = []
        visited = 0

        for radius in range(limit + 1):
            # Visiting more cells than there are junctions costs more than
            # checking every junction: do that instead (the same answer)
            visited += 8 * radius or 1
            if visited > len(self.points):
                return self._scan(latitude, longitude, count, squared)

            for cell in self._ring(centre, radius):
                for lat, lon, order, node in self.cells.get(cell, ()):
                    value = (lat - latitude) ** 2 + (lon - longitude) ** 2
                    if not squared:
                        value = math.sqrt(value)
                    seen.append((value, order, node))

            # Anything not yet seen is at least radius x cell away
            reach = radius * self.cell
            if not squared:
                closer = sum(1 for value, _, _ in seen if value < reach)
            else:
                closer = sum(1 for value, _, _ in seen if value < reach * reach)
            if closer >= count:
                break

        seen.sort()
        return [node for _, _, node in seen[:count]]

    def _scan(self, latitude, longitude, count, squared):
        ranked = []
        for lat, lon, order, node in self.points:
            value = (lat - latitude) ** 2 + (lon - longitude) ** 2
            ranked.append((value if squared else math.sqrt(value), order, node))
        ranked.sort()
        return [node for _, _, node in ranked[:count]]
