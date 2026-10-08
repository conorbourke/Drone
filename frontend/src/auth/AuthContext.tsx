/**
 * Who is signed in. Probes /api/auth/me once on load, exposes login/logout, and clears the
 * user whenever the API client sees a 401 so the router can send the owner to /login.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, isAbortError, setUnauthorizedHandler } from '../api/client';
import type { LoginResponse, User } from '../api/types';

export interface AuthContextValue {
  user: User | null;
  /** "loading" until the first /me probe has answered. */
  status: 'loading' | 'ready';
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  status: 'loading',
  login: async () => undefined,
  logout: async () => undefined,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready'>('loading');

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    const controller = new AbortController();
    api<User>('/api/auth/me', { skipAuthRedirect: true, signal: controller.signal })
      .then((me) => {
        if (!controller.signal.aborted) setUser(me);
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setUser(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setStatus('ready');
      });
    return () => {
      controller.abort();
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = useCallback(async (password: string) => {
    const response = await api<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: { password },
      skipAuthRedirect: true,
    });
    setUser(response.user);
  }, []);

  const logout = useCallback(async () => {
    setUser(null);
    try {
      await api<void>('/api/auth/logout', { method: 'POST', skipAuthRedirect: true });
    } catch {
      // The cookie may already be gone; the owner is signed out locally either way.
    }
  }, []);

  const value = useMemo<AuthContextValue>(() => ({ user, status, login, logout }), [user, status, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
