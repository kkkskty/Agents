const AUTH_KEY = 'assistant_auth'

export type AuthSession = {
  username: string
  token: string
}

export function loadSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as AuthSession
    if (data?.username && data?.token) return data
  } catch {
    /* ignore */
  }
  return null
}

export function saveSession(session: AuthSession): void {
  localStorage.setItem(AUTH_KEY, JSON.stringify(session))
}

export function clearSession(): void {
  localStorage.removeItem(AUTH_KEY)
}
