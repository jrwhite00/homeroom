/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // In the cluster the gateway sends /api/* to the backend. For `npm run dev`, set BACKEND_URL
  // (for example http://127.0.0.1:8000) and the dev server does the same.
  async rewrites() {
    const backend = process.env.BACKEND_URL;
    return backend ? [{ source: "/api/:path*", destination: `${backend.replace(/\/$/, "")}/:path*` }] : [];
  },
};

export default nextConfig;
