/** Password login. After success the owner returns to the route they were trying to open. */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router';
import { ApiError, errorMessage } from '../api/client';
import { useAuth } from '../auth/AuthContext';

/** Only return to same-origin app routes, never to /login itself or protocol-relative URLs. */
function safeReturnPath(value: unknown): string {
  if (typeof value !== 'string') return '/';
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/login')) return '/';
  return value;
}

export function LoginPage() {
  const { user, status, login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const returnTo = safeReturnPath((location.state as { from?: unknown } | null)?.from);

  if (status === 'ready' && user) {
    return <Navigate to={returnTo} replace />;
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      navigate(returnTo, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.status === 401) {
          setError(caught.hasDetail ? caught.detail : 'Wrong password. Please try again.');
        } else if (caught.status === 429) {
          setError(
            caught.hasDetail ? caught.detail : 'Too many failed attempts. Please wait 15 minutes and try again.',
          );
        } else {
          setError(caught.detail);
        }
      } else {
        setError(errorMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="card login-card stack" onSubmit={(event) => void onSubmit(event)} aria-labelledby="login-title">
        <div>
          <h1 id="login-title">VTOL Drone Designer</h1>
          <p className="muted small">Sign in with the owner password.</p>
        </div>
        <div className="form-row">
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input"
            data-testid="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
            required
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'login-error' : undefined}
          />
        </div>
        {error ? (
          <p id="login-error" className="form-error" role="alert" data-testid="login-error">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          className="button button-primary button-block"
          data-testid="login-submit"
          disabled={busy || status === 'loading'}
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
