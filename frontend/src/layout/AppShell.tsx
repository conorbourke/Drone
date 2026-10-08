/**
 * Top bar (brand, project name, workspace tabs, settings link, logout) and the page body,
 * with an optional right rail for the Versions and Assistant panels.
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, NavLink, useNavigate } from 'react-router';
import { useAuth } from '../auth/AuthContext';

export type TabKey = 'inputs' | 'design' | 'parts' | 'files' | 'flight';

/** Workspace tabs, left to right, in the order the owner works through them. */
export const TABS: ReadonlyArray<{ key: TabKey; label: string }> = [
  { key: 'inputs', label: 'Inputs' },
  { key: 'design', label: 'Design' },
  { key: 'parts', label: 'Parts' },
  { key: 'files', label: 'Files' },
  { key: 'flight', label: 'Flight data' },
];

export function parseTab(value: string | null): TabKey {
  const found = TABS.find((tab) => tab.key === value);
  return found ? found.key : 'inputs';
}

export function WorkspaceTabs({ active }: { active: TabKey }) {
  return (
    <nav className="tabs" aria-label="Workspace tabs">
      {TABS.map((tab) => (
        <Link
          key={tab.key}
          to={{ search: `?tab=${tab.key}` }}
          className="tab"
          data-testid={`tab-${tab.key}`}
          aria-current={active === tab.key ? 'page' : undefined}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}

interface AppShellProps {
  /** Project name (or page title) shown after the brand. */
  title?: string;
  /** Tab strip for the project workspace. */
  tabs?: ReactNode;
  /** Right rail content (Versions and Assistant panels). */
  rail?: ReactNode;
  /** Narrower reading width for simple pages. */
  narrow?: boolean;
  children: ReactNode;
}

export function AppShell({ title, tabs, rail, narrow, children }: AppShellProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  const onLogout = async () => {
    setSigningOut(true);
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand" aria-label="VTOL Drone Designer, all projects">
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="7" fill="#1f5f8b" />
              <path d="M4 15.5 L16 12 L28 15.5 L16 18 Z" fill="#fff" />
              <circle cx="7" cy="12" r="2.4" fill="#ffd166" />
              <circle cx="25" cy="12" r="2.4" fill="#ffd166" />
              <rect x="14.6" y="9" width="2.8" height="14" rx="1.4" fill="#fff" />
            </svg>
            <span>VTOL Designer</span>
          </Link>
          {title ? (
            <>
              <span className="topbar-sep" aria-hidden="true">
                /
              </span>
              <span className="topbar-title" title={title}>
                {title}
              </span>
            </>
          ) : null}
          {tabs}
          <div className="topbar-actions">
            {user ? (
              <span className="muted small nowrap" title={user.email}>
                {user.display_name || user.email}
              </span>
            ) : null}
            <NavLink to="/settings" className="button button-ghost button-sm" data-testid="settings-link">
              Settings
            </NavLink>
            <button
              type="button"
              className="button button-sm"
              data-testid="logout"
              onClick={() => void onLogout()}
              disabled={signingOut}
            >
              {signingOut ? 'Signing out…' : 'Log out'}
            </button>
          </div>
        </div>
      </header>
      <main className={`page${narrow ? ' page-narrow' : ''}`}>
        {rail ? (
          <div className="workspace">
            <div className="workspace-main">{children}</div>
            <aside className="rail" aria-label="Versions and assistant">
              {rail}
            </aside>
          </div>
        ) : (
          children
        )}
      </main>
    </div>
  );
}
