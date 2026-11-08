const paths = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  zones: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15"/></>,
  monitoring: <><path d="M3 12h4l3-7 4 14 3-7h4"/></>,
  schedule: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18m-12 5 2 2 4-4"/></>,
  analytics: <><path d="M4 3v17h17M7 15l4-5 4 3 5-7"/></>,
  history: <><path d="M3 11a9 9 0 1 1 2.6 7M3 5v6h6M12 7v5l3 2"/></>,
  settings: <><path d="m10 3-.7 2.4-2 .9L5 5.6 3 9l1.7 1.8v2.4L3 15l2 3.4 2.3-.7 2 .9L10 21h4l.7-2.4 2-.9 2.3.7 2-3.4-1.7-1.8v-2.4L21 9l-2-3.4-2.3.7-2-.9L14 3z"/><circle cx="12" cy="12" r="3"/></>,
}
export default function NavIcon({name}) {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.overview}</svg>
}
