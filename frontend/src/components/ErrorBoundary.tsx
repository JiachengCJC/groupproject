import { Component } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './ui';

/** Keep a failed render recoverable without losing server-side tasks or records. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="boot-state" role="alert">
        <Icon name="alert" size={36} />
        <h1>This page is temporarily unavailable</h1>
        <p>Background tasks and saved records are preserved. Reload the page to continue.</p>
        <button className="button primary" onClick={() => location.reload()}>
          Reload
        </button>
        <a
          className="button secondary"
          href="/#history"
          onClick={(event) => {
            event.preventDefault();
            location.hash = 'history';
            location.reload();
          }}
        >
          Return to task history
        </a>
      </main>
    );
  }
}
