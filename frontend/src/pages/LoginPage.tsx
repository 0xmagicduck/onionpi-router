import { FormEvent, useState } from 'react'
import { Eye, EyeOff, Globe, LockKeyhole, ShieldCheck, Wifi } from 'lucide-react'
import { Logo } from '../components/Logo'
import { api } from '../api'

type Props = {
  onLogin: (username: string, password: string) => Promise<void>
}

const POINTS = [
  { icon: ShieldCheck, text: 'Tout le trafic TCP des appareils passe par Tor' },
  { icon: Globe, text: 'Les requêtes DNS sont résolues à travers le réseau Tor' },
  { icon: Wifi, text: 'Un coupe-circuit nftables bloque toute fuite directe' },
]

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
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
          <ShieldCheck size={42} />
          <h1>Votre réseau.<br />Votre vie privée.</h1>
          <p>OnionPi protège les appareils de votre foyer en faisant passer leur trafic par le réseau Tor.</p>
          <ul className="login-points">
            {POINTS.map((point) => {
              const Icon = point.icon
              return <li key={point.text}><Icon size={18} />{point.text}</li>
            })}
          </ul>
        </div>
        <p className="login-foot"><Wifi size={15} /> Administration disponible uniquement sur le réseau local</p>
      </section>

      <section className="login-form-wrap">
        <form className="login-form" onSubmit={recovering ? recover : submit}>
          <span className="login-lock"><LockKeyhole size={22} /></span>
          <div>
            <h2>{recovering ? 'Récupération locale' : 'Administration'}</h2>
            <p>
              {recovering
                ? 'À la console, lancez « sudo onionpi-maintenance --open », puis utilisez votre code sauvegardé.'
                : 'Connectez-vous pour gérer votre routeur.'}
            </p>
          </div>

          {!recovering && (
            <>
              <label>
                Nom d’utilisateur
                <input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required />
              </label>
              <label>
                Mot de passe
                <span className="password-field">
                  <input
                    type={reveal ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    autoFocus
                  />
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => setReveal((value) => !value)}
                    aria-label={reveal ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}
                  >
                    {reveal ? <EyeOff size={17} /> : <Eye size={17} />}
                  </button>
                </span>
              </label>
            </>
          )}

          {recovering && (
            <>
              <label>
                Code de récupération
                <input className="mono" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} required autoFocus />
              </label>
              <label>
                Nouveau mot de passe
                <input type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
              </label>
              <label>
                Confirmation
                <input type="password" minLength={12} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required />
              </label>
            </>
          )}

          {error && <p className="form-error" role="alert">{error}</p>}

          <button className="button button-primary login-submit" disabled={busy}>
            {busy ? 'Traitement…' : recovering ? 'Remplacer le mot de passe' : 'Se connecter'}
          </button>
          <button
            type="button"
            className="login-recovery-link"
            onClick={() => { setRecovering((value) => !value); setError('') }}
          >
            {recovering ? 'Retour à la connexion' : 'Mot de passe oublié ?'}
          </button>
        </form>
      </section>
    </main>
  )
}
