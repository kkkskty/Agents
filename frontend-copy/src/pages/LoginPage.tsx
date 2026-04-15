import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function LoginPage() {
  const { session, login } = useAuth()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  if (session) {
    return <Navigate to="/" replace />
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await login(username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-logo" aria-hidden="true">
            ◆
          </span>
          <div>
            <h1 className="login-title">游戏社区 · 智能客服</h1>
            <p className="login-subtitle">
              为普通玩家解答：限时抢购、发帖动态、好友社交与账号充值等常见问题
            </p>
          </div>
        </div>

        <form className="login-form" onSubmit={onSubmit}>
          <label className="field">
            <span className="field-label">用户名</span>
            <input
              className="field-input"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="例如：玩家昵称或账号"
            />
          </label>
          <label className="field">
            <span className="field-label">密码</span>
            <input
              className="field-input"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="演示：至少 4 位字符"
            />
          </label>

          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}

          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? '登录中…' : '登录'}
          </button>
        </form>

        <p className="login-hint">
          演示环境：任意昵称 + 至少 4 位密码即可进入客服对话。正式环境请接入平台统一登录。
        </p>
        {from !== '/login' ? (
          <p className="login-hint login-hint--muted">
            登录后将返回先前访问的页面。
          </p>
        ) : null}
      </div>
    </div>
  )
}
