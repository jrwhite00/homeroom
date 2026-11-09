"use client";

import { useEffect } from "react";

// Browser monitoring for the student page. The endpoint and token come from runtime configuration
// (DASH0_WEB_URL and DASH0_WEB_TOKEN, from a Kubernetes secret), passed in by the layout. Never from the repo.
// The token is ingest-only and, like every browser monitoring token, visible to the browser.
export default function Dash0Web({ url, authToken }) {
  useEffect(() => {
    if (!url || !authToken) return;
    import("@dash0/sdk-web").then(({ init }) =>
      init({
        serviceName: "homeroom-frontend-browser",
        endpoint: { url, authToken },
      }),
    );
  }, [url, authToken]);
  return null;
}
