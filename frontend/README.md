# NORWICH//SIM frontend

The map interface for NORWICH//SIM, built with React, TypeScript, Vite and Leaflet (via `react-leaflet`).

It needs the backend running. See the [main README](../README.md) for the full setup, including generating the Norwich road-network data the backend depends on.

## Run it

```bash
npm install
npm run dev
```

Then open **http://localhost:5173**. Use `localhost` rather than `127.0.0.1`: by default the backend only allows requests from that origin (its `CORS_ORIGINS` setting changes this).

## Backend address

The frontend calls the backend at `http://127.0.0.1:8000` by default. Set `VITE_API_URL` to use a different address, for example:

```bash
VITE_API_URL=http://127.0.0.1:9000 npm run dev
```

The address is read in `src/config.ts`. Vite copies it into the JavaScript when the app is built, so a deployed build must be rebuilt after changing it. If you serve the frontend from a different origin, add that origin to the backend's `CORS_ORIGINS`.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Start the Vite development server |
| `npm run build` | Type-check with `tsc -b`, then build with Vite |
| `npm run lint` | Run ESLint |
| `npm test` | Run all frontend tests: `test:unit`, then `test:ui` |
| `npm run test:unit` | Logic tests in `tests/` (Node's built-in test runner) |
| `npm run test:ui` | Interaction tests in `ui-tests/`: type-check them, then run them with Vitest, jsdom and React Testing Library |
| `npm run preview` | Serve the production build locally |

## Code

- `src/main.tsx`: React entry point
- `src/App.tsx`: the interface: map, controls, simulation loop and the calls to the backend
- `src/simulationHelpers.ts`: status text, emergency text, traffic summary and route conversion
- `src/OptimiserPanel.tsx`, `src/OptimiserResults.tsx`, `src/optimiser.ts`: the station optimiser (controls and map markers, result tables, requests and types)
- `src/config.ts`: the backend address
- `src/mapStyles.ts`, `src/components.tsx`, `src/index.css`: map colours and sizes, shared UI pieces, the design system
- `tests/`: logic tests
- `ui-tests/`: interaction tests, plus their helpers (`helpers.ts` fake backend and polling control, `fakeLeaflet.tsx` stand-in map, `mapState.ts`, `setup.ts`)

The logic tests cover the logic behind the screens, the optimiser's requests and error handling, and the optimiser's result tables. The interaction tests render the whole app in jsdom and click through it with a fake backend and a stand-in for Leaflet. Neither kind opens a real browser, so real map rendering is not tested.
