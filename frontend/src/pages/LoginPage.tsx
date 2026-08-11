import { FormEvent, useState } from 'react'
import { LockKeyhole, ShieldCheck, Wifi } from 'lucide-react'
import { Logo } from '../components/Logo'
import { api } from '../api'

type Props = {
  onLogin: (username: string, password: string) => Promise<void>
}

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [recoveryCode, setRecoveryCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')

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

  const recover = async (event: FormEvent) => {
    event.preventDefault()
    if (newPassword !== confirmation) {
      setError('Les deux mots de passe diffèrent.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.recoverAccount(recoveryCode, newPassword)
      setPassword(newPassword)
      setRecovering(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Récupération impossible')
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
        <form className="login-form" onSubmit={recovering ? recover : submit}>
          <span className="login-lock"><LockKeyhole size={24} /></span>
          <h2>{recovering ? 'Récupération locale' : 'Administration'}</h2>
          <p>{recovering ? 'À la console, lancez « sudo onionpi-maintenance --open », puis utilisez votre code sauvegardé.' : 'Connectez-vous pour gérer votre routeur.'}</p>
          {!recovering && <label>Nom d’utilisateur<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>}
          {!recovering && <label>Mot de passe<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></label>}
          {recovering && <label>Code de récupération<input className="mono" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} required autoFocus /></label>}
          {recovering && <label>Nouveau mot de passe<input type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>}
          {recovering && <label>Confirmation<input type="password" minLength={12} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>}
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary login-submit" disabled={busy}>{busy ? 'Traitement…' : recovering ? 'Remplacer le mot de passe' : 'Se connecter'}</button>
          <button type="button" className="login-recovery-link" onClick={() => { setRecovering((value) => !value); setError('') }}>{recovering ? 'Retour à la connexion' : 'Mot de passe oublié ?'}</button>
        </form>
      </section>
    </main>
  )
}
