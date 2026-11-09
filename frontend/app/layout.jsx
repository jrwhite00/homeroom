import ChaosBanner from "./ChaosBanner";

export const metadata = {
  title: "Homeroom",
  description: "Find the KubeCon talks you're ready for.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <ChaosBanner />
        {children}
      </body>
    </html>
  );
}
