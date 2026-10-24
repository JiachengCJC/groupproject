import { useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from '../api';
import { Icon, Notice } from './ui';
export default function Auth({
  setup,
  tokenRequired,
  onAuthenticated,
}: {
  setup: boolean;
  tokenRequired?: boolean;
  onAuthenticated: () => Promise<void>;
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    try {
      await api(setup ? '/auth/setup' : '/auth/login', {
        email: String(form.get('email')),
        password: String(form.get('password')),
        ...(setup
          ? { name: String(form.get('name')), setup_token: String(form.get('setup_token') || '') }
          : {}),
      });
      await onAuthenticated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-layout">
      <section className="auth-story">
        <a className="brand" href="#analysis">
          <span className="brand-mark">
            <Icon name="spark" size={24} />
          </span>
          <span>
            Market Lens<small>INTELLIGENCE WORKSPACE</small>
          </span>
        </a>
        <div>
          <p className="eyebrow">FROM EVIDENCE TO ACTION</p>
          <h1>
            Understand change.
            <br />
            Make informed decisions.
          </h1>
          <p>
            Bring business data, market intelligence,
            <br />
            and team judgment into one workspace.
          </p>
          <div className="auth-orbit">
            <span>Data</span>
            <span>Evidence</span>
            <span>Decisions</span>
          </div>
        </div>
        <small>Reliable evidence · Human review · Traceable decisions</small>
      </section>
      <section className="auth-form-wrap">
        <form className="auth-form" onSubmit={submit}>
          <span className="badge">TEAM WORKSPACE</span>
          <h2>{setup ? 'Create your workspace' : 'Welcome back'}</h2>
          <p className="subtitle">
            {setup
              ? 'Set up the first administrator to start working with your team.'
              : 'Sign in to continue reviewing sources, analyses, and decisions.'}
          </p>
          {error && <Notice error>{error}</Notice>}
          {setup && (
            <label>
              Name
              <input name="name" autoComplete="name" required minLength={2} maxLength={100} />
            </label>
          )}
          <label>
            Email
            <input name="email" type="email" autoComplete="email" required maxLength={254} />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete={setup ? 'new-password' : 'current-password'}
              required
              minLength={setup ? 12 : undefined}
              maxLength={128}
            />
          </label>
          {setup && (
            <>
              <p className="help">Use a password with at least 12 characters.</p>
              <label>
                Setup token
                <input
                  name="setup_token"
                  type="password"
                  autoComplete="off"
                  required={tokenRequired}
                />
                <small>
                  Provided by your deployment administrator; required only for initial setup.
                </small>
              </label>
            </>
          )}
          <button className="button primary full" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : <Icon name="arrow" size={18} />}{' '}
            {busy ? 'Verifying…' : setup ? 'Create workspace' : 'Sign in to workspace'}
          </button>
          <p className="help auth-help">
            {setup
              ? 'Accounts are stored only in your deployed Market Lens instance.'
              : 'Contact your workspace administrator for an account or password reset.'}
          </p>
        </form>
      </section>
    </div>
  );
}
