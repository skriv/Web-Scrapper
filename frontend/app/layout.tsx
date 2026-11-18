import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SKRIV Scrapper — Business profile",
  description: "Business profiles from websites and Google Maps.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
