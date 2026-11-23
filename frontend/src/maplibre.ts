// MapLibre GL, set up for this build. Since version 6 MapLibre runs its
// tile work in a separate worker file and finds it next to its own module;
// Vite bundles that module into the app, so the worker is shipped as an
// asset (the ?url import) and MapLibre is told where it is. Import
// MapLibre from here, never from "maplibre-gl" directly.

import * as maplibregl from "maplibre-gl"
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url"

maplibregl.setWorkerUrl(workerUrl)

export default maplibregl
