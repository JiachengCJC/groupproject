import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { AuthStatus, Config, LocalModels, Sources } from './types';
import { WorkspaceContext } from './context';
import { Icon, Loading, Notice } from './components/ui';
import Auth from './components/Auth';
import Analysis from './pages/Analysis';
import Knowledge from './pages/Knowledge';
import History from './pages/History';
import Evaluation from './pages/Evaluation';
import Settings from './pages/Settings';
import { roleLabels } from './utils';
const nav = [
  ['analysis', 'Analysis workspace', 'spark'],
  ['sources', 'Knowledge library', 'book'],
  ['history', 'Analysis and review', 'clock'],
  ['evaluation', 'Evaluation', 'chart'],
  ['settings', 'Workspace', 'settings'],
];
function route() {
  const [view, query] = location.hash.slice(1).split('?');
  return {
    view: nav.some((n) => n[0] === view) ? view : 'analysis',
    id: new URLSearchParams(query).get('id') || undefined,
  };
}
export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [sources, setSources] = useState<Sources>({ datasets: [], documents: [] });
  const [models, setModels] = useState<LocalModels | null>(null);
  const [current, setCurrent] = useState(route);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<{ message: string; error: boolean; id: number } | null>(null);
  const [menu, setMenu] = useState(false);
  const notify = useCallback(
    (message: string, isError = false) => setToast({ message, error: isError, id: Date.now() }),
    [],
  );
  const refreshSources = useCallback(async () => setSources(await api<Sources>('/sources')), []);
  const refreshModels = useCallback(async () => {
    try {
      setModels(await api<LocalModels>('/models/ollama'));
    } catch (err) {
      setModels({ available: false, models: [], error: errorMessage(err) });
    }
  }, []);
  const loadAuth = useCallback(async () => {
    setError('');
    try {
      const result = await api<AuthStatus>('/auth/status');
      setAuth(result);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);
  useEffect(() => {
    void loadAuth();
  }, [loadAuth]);
  useEffect(() => {
    if (!auth || (auth.enabled && !auth.user)) return;
    let mounted = true;
    Promise.all([api<Config>('/config'), api<Sources>('/sources')])
      .then(([c, s]) => {
        if (mounted) {
          setConfig(c);
          setSources(s);
        }
      })
      .catch((err) => {
        if (mounted) setError(errorMessage(err));
      });
    void refreshModels();
    return () => {
      mounted = false;
    };
  }, [auth, refreshModels]);
  useEffect(() => {
    function changed() {
      setCurrent(route());
      setMenu(false);
    }
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), toast.error ? 12000 : 5000);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    function expired() {
      setAuth(null);
      setConfig(null);
      void loadAuth();
    }
    window.addEventListener('session-expired', expired);
    return () => window.removeEventListener('session-expired', expired);
  }, [loadAuth]);
  const navigate = useCallback((view: string, id?: string) => {
    location.hash = view + (id ? '?id=' + encodeURIComponent(id) : '');
  }, []);
  async function logout() {
    try {
      await api('/auth/logout', {});
      setConfig(null);
      await loadAuth();
    } catch (err) {
      notify(errorMessage(err), true);
    }
  }
  if (error && (!auth || !config))
    return (
      <main className="boot-state">
        <Icon name="alert" size={36} />
        <h1>Unable to connect to the workspace</h1>
        <Notice error>{error}</Notice>
        <button className="button primary" onClick={() => location.reload()}>
          Reconnect
        </button>
      </main>
    );
  if (!auth)
    return (
      <main className="boot-state">
        <Loading label="Connecting to the workspace…" />
      </main>
    );
  if (auth.enabled && !auth.user)
    return (
      <Auth
        setup={auth.setup_required}
        tokenRequired={auth.setup_token_required}
        onAuthenticated={loadAuth}
      />
    );
  if (!config)
    return (
      <main className="boot-state">
        <Loading label="Preparing your analysis workspace…" />
      </main>
    );
  return (
    <WorkspaceContext.Provider
      value={{ auth, config, sources, models, notify, refreshSources, refreshModels, navigate }}
    >
      <div className="app-layout">
        <a
          href="#main-content"
          className="skip-link"
          onClick={(event) => {
            event.preventDefault();
            document.getElementById('main-content')?.focus();
          }}
        >
          Skip to main content
        </a>
        {menu && (
          <button
            className="sidebar-scrim"
            onClick={() => setMenu(false)}
            aria-label="Close navigation"
          />
        )}
        <aside className={'sidebar' + (menu ? ' is-open' : '')}>
          <a href="#analysis" className="brand">
            <span className="brand-mark">
              <Icon name="spark" size={24} />
            </span>
            <span>
              Market Lens<small>INTELLIGENCE WORKSPACE</small>
            </span>
          </a>
          <div className="workspace-label">
            <span className="workspace-avatar">
              <Icon name="users" size={19} />
            </span>
            <div>
              Team workspace
              <small>{config.local_only ? 'Local-only mode' : 'Local and cloud models'}</small>
            </div>
          </div>
          <p className="nav-caption">WORKSPACE</p>
          <nav aria-label="Main navigation">
            {nav.map(([key, label, icon]) => (
              <a
                key={key}
                href={'#' + key}
                className={'nav-item' + (current.view === key ? ' selected' : '')}
                aria-current={current.view === key ? 'page' : undefined}
              >
                <Icon name={icon} size={19} />
                <span>{label}</span>
                {key === 'sources' ? (
                  <small>{sources.datasets.length + sources.documents.length}</small>
                ) : current.view === key ? (
                  <Icon name="arrow" size={15} />
                ) : null}
              </a>
            ))}
          </nav>
          <div className="sidebar-note">
            <span className="small-orbit">✦</span>
            <h3>
              Every decision,
              <br />
              backed by evidence.
            </h3>
            <p>
              From sources to recommendations,
              <br />
              with evidence and human review throughout.
            </p>
          </div>
          <div className="sidebar-user">
            <span className="user-avatar">
              {(auth.user?.name || 'Local').slice(0, 1).toUpperCase()}
            </span>
            <div>
              <b>{auth.user?.name || 'Local workspace'}</b>
              <small>{roleLabels[auth.user?.role || 'admin']}</small>
            </div>
            {auth.enabled && (
              <button
                className="icon-button"
                onClick={logout}
                title="Sign out"
                aria-label="Sign out"
              >
                <Icon name="logout" size={17} />
              </button>
            )}
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <div className="breadcrumb">
              <button
                className="icon-button mobile-menu"
                aria-label="Open navigation"
                onClick={() => setMenu(true)}
              >
                <Icon name="menu" />
              </button>
              <span>Workspace</span>
              <i>/</i>
              <b>{nav.find((n) => n[0] === current.view)?.[1]}</b>
            </div>
            <div className="topbar-right">
              <span className="connection-badge">
                <i />
                Service connected
              </span>
              <span className="review-badge">
                <Icon name="shield" size={14} />
                Human review
              </span>
            </div>
          </header>
          <main id="main-content" className="main-content" tabIndex={-1}>
            {current.view === 'analysis' && <Analysis runId={current.id} />}
            {current.view === 'sources' && <Knowledge />}
            {current.view === 'history' && <History />}
            {current.view === 'evaluation' && <Evaluation />}
            {current.view === 'settings' && <Settings />}
          </main>
          <footer className="footer">
            <span>
              MARKET LENS <i>·</i> TEAM WORKSPACE
            </span>
            <span>Evidence informs the analysis. You make the decision.</span>
          </footer>
        </div>
      </div>
      {toast && (
        <div
          className={'toast' + (toast.error ? ' error' : '')}
          role={toast.error ? 'alert' : 'status'}
        >
          <Icon name={toast.error ? 'alert' : 'check'} size={18} />
          <span>{toast.message}</span>
          <button
            onClick={() => setToast(null)}
            aria-label="Dismiss notification"
            className="icon-button"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
    </WorkspaceContext.Provider>
  );
}
