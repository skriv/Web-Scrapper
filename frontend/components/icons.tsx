import type { CSSProperties } from "react";

export function Icon({ name, size = 20, className = "", style }: {
  name: "building" | "link" | "spark" | "download" | "copy" | "check" | "arrow" | "file" | "globe" | "refresh" | "info";
  size?: number; className?: string; style?: CSSProperties;
}) {
  const paths = {
    building: <><path d="M5 21V5l7-3 7 3v16M2 21h20M9 8h.01M15 8h.01M9 12h.01M15 12h.01M10 21v-5h4v5" /></>,
    link: <><path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(0 1) scale(.94)" /></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM21 2v4M19 4h4" /></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" /></>,
    copy: <><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    file: <><path d="M14 2H5v20h14V7l-5-5ZM14 2v5h5M8 12h8M8 16h8" /></>,
    globe: <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></>,
    refresh: <><path d="M20 7a9 9 0 1 0 1 9M20 2v6h-6" /></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.01" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className} style={style} aria-hidden="true">{paths[name]}</svg>;
}
