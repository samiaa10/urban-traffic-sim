# Population: Census 2021 by output area (Norwich)

`norwich_output_areas_2021.csv` has one row per 2021 Census output area (OA) in the
Norwich local authority (E07000148): its code, its usual resident population, and
its population-weighted centroid (latitude, longitude). 471 output areas,
143,901 people.

* Population: Census 2021 table TS001 (number of usual residents), from Nomis
  (dataset NM_2021_1), fetched 17 November 2026.
* Centroids: "2021 Output Area (Population Weighted Centroid)" from the ONS Open
  Geography Portal (ArcGIS item c66f0f0126174450b931cb179c4dfc17), fetched
  17 November 2026, converted to WGS84 latitude/longitude.

Source: Office for National Statistics, licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Contains National Statistics data © Crown copyright and database right 2021.
Contains OS data © Crown copyright and database right 2021.

Used by `backend/simulation/population.py` to place emergencies where people
live (each output area's population goes to the road junction nearest its
centroid).
