// Small presentational pieces shared across the screen: icons, the logo,
// KPI cards and a spinner. No logic or network calls in here.

import type { ReactNode } from "react"

// ------------------------------------------------
// Icons: simple 24x24 line icons, drawn inline so no icon library is needed
// ------------------------------------------------

const ICONS = {
  route: (
    <>
      <circle cx="6" cy="19" r="3" />
      <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
      <circle cx="18" cy="5" r="3" />
    </>
  ),
  siren: (
    <>
      <path d="M7 18v-6a5 5 0 1 1 10 0v6" />
      <path d="M5 21a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-1a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2z" />
      <path d="M21 12h1M2 12h1M12 2v1M18.5 4.5 18 5M5.5 4.5 6 5" />
    </>
  ),
  barrier: (
    <>
      <rect x="2" y="6" width="20" height="8" rx="1" />
      <path d="M17 14v7M7 14v7M17 3v3M7 3v3M10 14 2.3 6.3M14 6l7.7 7.7M8 6l8 8" />
    </>
  ),
  cross: <path d="M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z" />,
  stations: (
    <>
      <path d="M12 21s-7-5.6-7-11a7 7 0 0 1 14 0c0 5.4-7 11-7 11z" />
      <path d="M12 7v6M9 10h6" />
    </>
  ),
  close: <path d="M18 6 6 18M6 6l12 12" />,
  check: <path d="M20 6 9 17l-5-5" />,
  alert: (
    <>
      <path d="m21.7 18-8-14a2 2 0 0 0-3.5 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4M12 8h.01" />
    </>
  ),
  pin: (
    <>
      <path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z" />
      <circle cx="12" cy="10" r="3" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  gauge: (
    <>
      <path d="m12 14 4-4" />
      <path d="M3.3 19a10 10 0 1 1 17.4 0" />
    </>
  ),
  car: (
    <>
      <path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2" />
      <circle cx="7" cy="17" r="2" />
      <path d="M9 17h6" />
      <circle cx="17" cy="17" r="2" />
    </>
  ),
  chart: (
    <>
      <path d="M3 3v18h18" />
      <path d="M8 17v-5M13 17V8M18 17v-9" />
    </>
  ),
  pulse: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
  github: (
    <>
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.1-1.3-.3-2.5-1-3.5.3-1.2.3-2.4 0-3.5 0 0-1 0-3 1.5-2.6-.5-5.4-.5-8 0C6 2 5 2 5 2c-.3 1.1-.3 2.3 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.4.5-.7 1.1-.9 1.7-.2.6-.2 1.2-.1 1.8v4" />
      <path d="M9 18c-4.5 2-5-2-7-2" />
    </>
  ),
} as const

export type IconName = keyof typeof ICONS

export function Icon({
  name,
  size = 18,
}: {
  name: IconName
  size?: number
}) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name]}
    </svg>
  )
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />
}

// ------------------------------------------------
// Logo
// ------------------------------------------------

export function Logo() {
  return (
    <svg
      width="34"
      height="34"
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="32" height="32" rx="7" fill="#173e60" />
      <circle cx="9" cy="23" r="2.6" fill="#7fc4b0" />
      <circle cx="23" cy="9" r="2.6" fill="#7fc4b0" />
      <path
        d="M11.4 23h6.2a3.6 3.6 0 0 0 0-7.2h-3.2a3.6 3.6 0 0 1 0-7.2h6"
        fill="none"
        stroke="#fff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// ------------------------------------------------
// KPI card
// ------------------------------------------------

export function KpiCard({
  label,
  value,
  sub,
  icon,
  children,
}: {
  label: string
  value?: ReactNode
  sub?: ReactNode
  icon: IconName
  children?: ReactNode
}) {
  return (
    <div className="kpi">
      <div className="kpi-label">
        <Icon name={icon} size={15} />
        {label}
      </div>
      {value !== undefined && <div className="kpi-value">{value}</div>}
      {children}
      {sub !== undefined && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

// A titled card used for each group of controls
export function Panel({
  id,
  title,
  caption,
  icon,
  children,
}: {
  id?: string
  title: string
  caption?: string
  icon: IconName
  children: ReactNode
}) {
  // Every panel is a named region, so screen-reader users can jump to it
  const headingId = `${id ?? title.toLowerCase().replace(/\W+/g, "-")}-title`

  return (
    <section id={id} className="panel" aria-labelledby={headingId}>
      <header className="panel-head">
        <span className="panel-icon">
          <Icon name={icon} size={17} />
        </span>
        <div>
          <h2 id={headingId}>{title}</h2>
          {caption && <p>{caption}</p>}
        </div>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  )
}
