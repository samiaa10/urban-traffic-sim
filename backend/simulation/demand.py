"""
Travel demand: where trips start and end
-----------------------------------------

There is no population or employment data in this project, so demand is a
simple, explainable model rather than a calibrated one:

  * Origins are spread evenly over road junctions. Junctions are denser
    where there are more streets, so this loosely follows built-up areas.
  * A share of destinations (centre_share) is drawn towards the city
    centre, with a Gaussian weight exp(-(distance / centre_scale)^2 / 2);
    the rest are spread evenly. City centres attract more trips (work,
    shops), so traffic converges there. (A Gaussian, not a plain
    exponential decay: there are more junctions the further out you go,
    and an exponential decay is too gentle to outweigh that, so it barely
    pulls trips inwards at all. With the Gaussian, about 40% of
    centre-bound trips end within centre_scale metres of the centre.)
  * Every trip starts and ends inside RoadNetwork.main_component() (the
    junctions that can all reach each other), so every trip has a route,
    and trips shorter than min_trip_metres (straight line) are not made.

Zones (a centre point and a radius) restrict origins or destinations to
an area. A traffic surge uses them: inbound rush hour sends trips INTO a
zone, the evening peak sends them OUT of it.
"""

import math


# Norwich city centre (near the Market / City Hall)
CITY_CENTRE = (52.6286, 1.2927)


class Zone:
    """A circular area: centre (latitude, longitude) and radius in metres."""

    def __init__(self, latitude, longitude, radius_metres):
        if not radius_metres > 0 or radius_metres == math.inf:
            raise ValueError("radius_metres must be a positive number")

        self.latitude = latitude
        self.longitude = longitude
        self.radius_metres = radius_metres

    def key(self):
        return (self.latitude, self.longitude, self.radius_metres)

    def describe(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "radius_metres": self.radius_metres,
        }


def metres_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


class DemandModel:

    def __init__(
        self,
        network,
        centre=CITY_CENTRE,
        centre_share=0.5,
        centre_scale_metres=1200,
        min_trip_metres=800,
    ):
        if not 0 <= centre_share <= 1:
            raise ValueError("centre_share must be between 0 and 1")

        self.network = network
        self.centre = centre
        self.centre_share = centre_share
        self.min_trip_metres = min_trip_metres

        # Sorted, so the same seed always gives the same trips
        self.nodes = sorted(network.main_component())

        if len(self.nodes) < 2:
            raise ValueError("The road network has no two connected junctions")

        # Running totals of the centre weights, for weighted sampling
        total = 0.0
        self.centre_weights = []

        for node in self.nodes:
            latitude, longitude = network.position[node]
            distance = metres_between(latitude, longitude, *centre)
            total += math.exp(-0.5 * (distance / centre_scale_metres) ** 2)
            self.centre_weights.append(total)

        self._zone_nodes = {}

    def nodes_in(self, zone):
        """Junctions inside a zone (only those trips can use)."""

        if zone.key() not in self._zone_nodes:
            self._zone_nodes[zone.key()] = [
                node for node in self.nodes
                if metres_between(
                    *self.network.position[node], zone.latitude, zone.longitude
                ) <= zone.radius_metres
            ]

        return self._zone_nodes[zone.key()]

    def _origin(self, generator, zone):
        if zone is not None:
            return generator.choice(self.nodes_in(zone))

        return generator.choice(self.nodes)

    def _destination(self, generator, zone):
        if zone is not None:
            return generator.choice(self.nodes_in(zone))

        if generator.random() < self.centre_share:
            return generator.choices(
                self.nodes, cum_weights=self.centre_weights
            )[0]

        return generator.choice(self.nodes)

    def random_trip(
        self,
        generator,
        origin_zone=None,
        destination_zone=None,
        attempts=200,
    ):
        """One (origin, destination) pair of junctions, or None.

        None means no pair at least min_trip_metres apart was found in
        `attempts` tries (for example when both ends must be in one tiny
        zone). Raises ValueError if a zone contains no usable junction.
        """

        for zone in (origin_zone, destination_zone):
            if zone is not None and not self.nodes_in(zone):
                raise ValueError("No usable road junction inside the zone")

        for _ in range(attempts):
            origin = self._origin(generator, origin_zone)
            destination = self._destination(generator, destination_zone)

            if origin == destination:
                continue

            if (
                self.network.straight_line_metres(origin, destination)
                >= self.min_trip_metres
            ):
                return origin, destination

        return None
