import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  clearSession,
  loadSession,
  saveSession,
  type AuthSession,
} from '../lib/authStorage'

type AuthContextValue = {
  session: AuthSession | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(() =>
    loadSession(),
  )

  const login = useCallback(async (username: string, password: string) => {
    const u = username.trim()
    if (!u) throw new Error('请输入用户名')
    if (password.length < 4)
      throw new Error('密码至少 4 位（演示环境，非真实校验）')

    const next: AuthSession = {
      username: u,
      token: `demo-${crypto.randomUUID()}`,
    }
    saveSession(next)
    setSession(next)
  }, [])

  const logout = useCallback(() => {
    clearSession()
    setSession(null)
  }, [])

  const value = useMemo(
    () => ({ session, login, logout }),
    [session, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/* Hook 与 Provider 同属认证模块，拆文件会增加样板代码 */
// eslint-disable-next-line react-refresh/only-export-components -- useAuth 与 Provider 配对使用
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 AuthProvider 内使用')
  return ctx
}
