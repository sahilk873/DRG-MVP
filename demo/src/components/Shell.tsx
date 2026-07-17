import {
  Activity,
  BookOpenCheck,
  Boxes,
  ChevronDown,
  DatabaseZap,
  LayoutDashboard,
  Menu,
  Play,
  ShieldCheck,
  X,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { ViewId } from '../types'
import { opportunities } from '../data'

interface ShellProps {
  activeView: ViewId
  onNavigate: (view: ViewId) => void
  onStartTour: () => void
  children: ReactNode
}

const navItems: Array<{ id: ViewId; label: string; icon: typeof Activity }> = [
  { id: 'overview', label: 'Command center', icon: LayoutDashboard },
  { id: 'queue', label: 'Review queue', icon: BookOpenCheck },
  { id: 'case', label: 'Encounter graph', icon: Boxes },
  { id: 'ingestion', label: 'Data onboarding', icon: DatabaseZap },
  { id: 'governance', label: 'Governance', icon: ShieldCheck },
]

export function Shell({ activeView, onNavigate, onStartTour, children }: ShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false)

  const navigate = (view: ViewId) => {
    onNavigate(view)
    setMobileOpen(false)
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? 'sidebar--open' : ''}`}>
        <div className="brand" aria-label="Encounter home">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
          </div>
          <div>
            <strong>Encounter</strong>
            <small>Revenue integrity</small>
          </div>
        </div>

        <nav className="primary-nav" aria-label="Product navigation">
          <span className="nav-label">Workspace</span>
          {navItems.map(({ id, label, icon: Icon }) => (
            <button
              className={activeView === id ? 'nav-item nav-item--active' : 'nav-item'}
              key={id}
              onClick={() => navigate(id)}
              type="button"
            >
              <Icon size={18} strokeWidth={1.8} />
              <span>{label}</span>
              {id === 'queue' && <b className="nav-count">{opportunities.length}</b>}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <button className="tour-card" onClick={onStartTour} type="button">
          <span className="tour-card__icon"><Play size={15} fill="currentColor" /></span>
          <span>
            <strong>Guided pitch demo</strong>
            <small>5 steps · 3 minutes</small>
          </span>
        </button>

        <div className="workspace-switcher">
          <div className="workspace-avatar">AM</div>
          <div>
            <strong>Alpha Medical</strong>
            <small>Demo workspace</small>
          </div>
          <ChevronDown size={15} />
        </div>
      </aside>

      {mobileOpen && <button aria-label="Close navigation" className="sidebar-scrim" onClick={() => setMobileOpen(false)} />}

      <div className="main-column">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileOpen(value => !value)} type="button" aria-label="Open navigation">
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="environment-pill"><span /> Synthetic pitch environment</div>
          <div className="topbar-spacer" />
          <div className="sync-status"><Activity size={15} /> Last scan 12 min ago</div>
          <button className="topbar-avatar" type="button" aria-label="Open user menu">SK</button>
        </header>
        <main className="page" key={activeView}>{children}</main>
      </div>
    </div>
  )
}
