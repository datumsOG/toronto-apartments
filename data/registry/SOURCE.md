# Where these files came from

**Fetched:** 2026-10-09 21:50 UTC / 17:50 Toronto (EDT), from the VM.

| File | Dataset | City's last refresh | Rows |
|---|---|---|---|
| `registration.csv` | Apartment Building Registration | 2026-07-05 | 3,611 |
| `evaluations.csv` | Apartment Building Evaluations, 2023 to current (RentSafeTO) | 2026-10-08 | 6,902 |

Fetch again:

```
curl -o registration.csv https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/3ad76a8c-0518-4df2-b94e-8c747d62f8c1
curl -o evaluations.csv  https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/244f7a02-da5c-425b-b55f-fbdd133dd732
```

Contains information licensed under the Open Government Licence – Toronto.

Notes from profiling: the join key is `RSN`. The registration file has no coordinates; the
evaluations file has `LATITUDE`/`LONGITUDE` on 6,666 of 6,902 rows and `X`/`Y` (a local
projected grid) on 6,851. Joined to the registry: 3,463 buildings have lat/lon, another 101
have only X/Y, 24 have neither. Eight trailing columns in `registration.csv` are empty
duplicates of earlier ones.
