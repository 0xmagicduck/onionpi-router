import { FormEvent, useState } from 'react'
import { LockKeyhole, ShieldCheck, Wifi } from 'lucide-react'
import { Logo } from '../components/Logo'

type Props = {
  onLogin: (username: string, password: string) => Promise<void>
}

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await onLogin(username, password)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Connexion impossible')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-intro">
        <Logo />
        <div className="login-copy">
          <ShieldCheck size={44} />
          <h1>Votre réseau.<br />Votre vie privée.</h1>
          <p>OnionPi protège les appareils de votre foyer en faisant passer leur trafic TCP par le réseau Tor.</p>
        </div>
        <div className="login-foot"><Wifi size={17} /> Administration disponible uniquement sur le réseau local</div>
      </section>
      <section className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <span className="login-lock"><LockKeyhole size={24} /></span>
          <h2>Administration</h2>
          <p>Connectez-vous pour gérer votre routeur.</p>
          <label>Nom d’utilisateur<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>Mot de passe<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary login-submit" disabled={busy}>{busy ? 'Connexion…' : 'Se connecter'}</button>
        </form>
      </section>
    </main>
  )
}
