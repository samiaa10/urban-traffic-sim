# NORWICH//SIM frontend

The map interface for NORWICH//SIM, built with React, TypeScript, Vite and Leaflet (via `react-leaflet`).

It needs the backend running. See the [main README](../README.md) for the full setup, including generating the Norwich road-network data the backend depends on.

## Run it

```bash
npm install
npm run dev
```

Then open **http://localhost:5173**. Use `localhost` rather than `127.0.0.1`: the backend only allows requests from that origin.

## Backend address

The frontend calls the backend at `http://127.0.0.1:8000`. This address is written directly in `src/App.tsx` rather than read from a configuration file, so if you run the backend on a different port you will need to change it there (and update the allowed origin in `backend/api/main.py` if you change the frontend port).

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Start the Vite development server |
| `npm run build` | Type-check with `tsc -b`, then build with Vite |
| `npm run lint` | Run ESLint |
| `npm run preview` | Serve the production build locally |

## Code

- `src/main.tsx`: React entry point
- `src/App.tsx`: the interface: map, controls, simulation loop and all calls to the backend

The frontend has no automated tests.
