export const metadata = {
  title: "Homeroom",
  description: "Find the KubeCon talks you're ready for.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
