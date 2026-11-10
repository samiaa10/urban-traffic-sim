// Where the backend lives.
//
// Local development: no setup needed, it uses the address below.
// Deployment: set VITE_API_URL when the frontend is built (see the README,
// "Deploying to Render"). Vite copies that value into the built JavaScript
// at build time, so changing it later means building again.
export const API_URL: string = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "")
