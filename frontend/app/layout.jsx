import ChaosBanner from "./ChaosBanner";
import Dash0Web from "./Dash0Web";

export const metadata = {
  title: "Homeroom",
  description: "Find the KubeCon talks you're ready for.",
};

// Read the browser monitoring settings when the page is served, so they come from the pod's
// configuration rather than being baked into the image or the repo.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Dash0Web url={process.env.DASH0_WEB_URL} authToken={process.env.DASH0_WEB_TOKEN} />
        <ChaosBanner />
        {children}
      </body>
    </html>
  );
}
