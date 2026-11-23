# Spec — Dispatch: rerouting, cost-to-serve and truck-crisis calls

Owner: merciful-anchor (Denis). Split agreed in group:stockline: Leïla's copilot and Jev in the live call belong to nibbling-cattail.

## Why

Today's runs are sequenced with no geography: departure + 12 minutes per stop, stops grouped by city then name (`services/routing.py`). Trucks cross Marseille in random order, and nobody knows what delivering each client costs. When a truck breaks down at 7 am, Leïla phones 8 clients one by one.

## What

1. **Geo** (`services/geo.py`): client coordinates from the postcode in the address. A fixed table of postcode centres, plus a small deterministic offset per client. Fictional data, and no external geocoding call. The depot is MIN des Arnavaux (13014). Distance = haversine × 1.35 (road factor). Time = 30 km/h inside Marseille, 55 km/h outside, plus 8 minutes per drop.
2. **Optimizer** (`services/optimize.py`, `GET /api/routing/optimize?date=`): for every run of the date:
   - current plan: km, driving time, last ETA, load;
   - optimized plan: nearest neighbour + 2-opt within each truck, then moves of stops between trucks when they save km;
   - constraints: truck capacity (kg) and delivery window (last ETA ≤ 11:00);
   - output: before/after per truck and in total (km, hours, € at 0,55 €/km + 30 €/h driver), with new ETAs.
3. **Apply** (`POST /api/routing/apply {date}`): writes the optimized stops and ETAs to the route runs, and moves `routeRunId` on any order that changed truck.
4. **Cost-to-serve** (part of optimize): per stop, the detour cost (insertion km + drop time, in €) against the order's margin.
   - Flags: below the 120 € minimum (house rule: collection at the dock); delivery cost over 25 % of the margin.
   - Suggestion per client: collection at the dock, a delivery fee, or merging with another delivery day.
5. **Breakdown** (`POST /api/routing/breakdown {runId, afterStop}`): the truck stops after stop N. Its remaining stops are offered to the other trucks, at the cheapest feasible insertion (capacity, ETA ≤ 11:30). Otherwise the client is offered collection at the dock or delivery tomorrow. Output: affected clients, each with old ETA → new ETA or the fallback.
6. **Crisis calls** (`apps/ai/app/campaign/`): for each affected client, in parallel:
   - the agent speaks first (brief: breakdown, new ETA or the options);
   - a simulated client answers, played by a model from the persona card (as in the sim);
   - Jev classifies the reply (accepts / collects at the dock / tomorrow / other);
   - the outcome is written: client event, and the order date moves for "tomorrow".
   - Progress streams to the web.
7. **Page `/dispatch`**:
   - SVG map: depot, clients coloured by truck, before/after lines;
   - KPI tiles: km, hours, € saved;
   - a cost-to-serve table;
   - a "Panne" button on a truck, then a war room with one tile per crisis call (live transcript, Jev decision, outcome).

## Done when

- `optimize` on today's 38 stops shows fewer km than the current plan, and every truck stays within capacity and the window. There's a backend test.
- `breakdown` on one truck reassigns or flags every remaining stop, with a test.
- The crisis campaign runs about 8 calls in parallel against simulated clients, and each ends with a Jev-classified outcome written to the ERP.
- `/dispatch` shows it all, and `make check` passes.
