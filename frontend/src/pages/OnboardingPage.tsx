import { FormEvent, useState } from 'react'
import { CheckCircle2, Clock3, Download, KeyRound, Network, ShieldCheck } from 'lucide-react'
import { api } from '../api'
import { Logo } from '../components/Logo'
import type { OnboardingState } from '../types'

type Props = {
  state: OnboardingState
  refresh: () => Promise<void>
  onPasswordChanged: () => void
}

export function OnboardingPage({ state, refresh, onPasswordChanged }: Props) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [recoveryCode, setRecoveryCode] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name)
    setError('')
    try {
      await action()
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Étape impossible')
    } finally {
      setBusy('')
    }
  }

  const changePassword = async (event: FormEvent) => {
    event.preventDefault()
    if (newPassword !== confirmation) {
      setError('Les deux nouveaux mots de passe diffèrent.')
      return
    }
    await run('password', async () => {
      await api.changePassword(currentPassword, newPassword)
      onPasswordChanged()
    })
  }

  const generateCode = async () => {
    setBusy('recovery')
    setError('')
    try {
      const result = await api.createRecoveryCode()
      setRecoveryCode(result.code)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Code impossible à créer')
    } finally {
      setBusy('')
    }
  }

  const saveCode = async () => {
    const blob = new Blob([
      `Code de récupération OnionPi\n\n${recoveryCode}\n\nConservez ce fichier hors de la Raspberry Pi.\n`,
    ], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'onionpi-code-recuperation.txt'
    link.click()
    URL.revokeObjectURL(url)
    await run('recovery-confirm', () => api.confirmRecoveryCodeSaved())
  }

  const completed = Object.values(state.steps).filter(Boolean).length

  return (
    <main className="onboarding-page">
      <header className="onboarding-head">
        <Logo />
        <div><strong>Première ouverture</strong><span>{completed}/5 vérifications</span></div>
      </header>
      <section className="onboarding-content">
        <div className="page-title">
          <h1>Rendre cette OnionPi sûre et récupérable</h1>
          <p>Ces cinq contrôles restent locaux. Le Wi-Fi ne sera déclaré prêt qu’une fois le coupe-circuit testé.</p>
        </div>

        {error && <div className="form-error" role="alert">{error}</div>}

        <div className="onboarding-steps">
          <section className={`panel onboarding-step ${state.steps.password ? 'onboarding-done' : ''}`}>
            <div className="onboarding-step-title"><KeyRound /><div><strong>1. Mot de passe administrateur</strong><p>Choisissez le secret définitif de l’interface.</p></div>{state.steps.password && <CheckCircle2 />}</div>
            {!state.steps.password && (
              <form className="settings-form" onSubmit={(event) => void changePassword(event)}>
                <label>Mot de passe actuel<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
                <label>Nouveau mot de passe<input type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
                <label>Confirmation<input type="password" minLength={12} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
                <button className="button button-primary" disabled={busy !== ''}>{busy === 'password' ? 'Modification…' : 'Modifier et se reconnecter'}</button>
              </form>
            )}
          </section>

          <section className={`panel onboarding-step ${state.steps.interfaces ? 'onboarding-done' : ''}`}>
            <div className="onboarding-step-title"><Network /><div><strong>2. Interfaces réseau</strong><p>Internet sur <code>{state.interfaces.wan}</code>, point d’accès sur <code>{state.interfaces.access_point}</code>.</p></div>{state.steps.interfaces && <CheckCircle2 />}</div>
            {!state.steps.interfaces && <button className="button button-secondary" disabled={busy !== ''} onClick={() => void run('interfaces', () => api.confirmOnboardingInterfaces(state.interfaces.wan, state.interfaces.access_point))}>Je confirme ce câblage</button>}
          </section>

          <section className={`panel onboarding-step ${state.steps.firewall ? 'onboarding-done' : ''}`}>
            <div className="onboarding-step-title"><ShieldCheck /><div><strong>3. Coupe-circuit</strong><p>Recharge nftables et prouve que toute sortie directe reste bloquée.</p></div>{state.steps.firewall && <CheckCircle2 />}</div>
            {!state.steps.firewall && <button className="button button-secondary" disabled={busy !== ''} onClick={() => void run('firewall', () => api.testOnboardingFirewall())}>{busy === 'firewall' ? 'Test en cours…' : 'Tester le coupe-circuit'}</button>}
          </section>

          <section className={`panel onboarding-step ${state.steps.clock ? 'onboarding-done' : ''}`}>
            <div className="onboarding-step-title"><Clock3 /><div><strong>4. Heure système</strong><p>{state.clock_synchronised ? 'L’heure est synchronisée, Tor peut vérifier ses certificats.' : 'La synchronisation NTP n’est pas encore confirmée.'}</p></div>{state.steps.clock && <CheckCircle2 />}</div>
            {!state.steps.clock && <button className="button button-secondary" disabled={busy !== '' || !state.clock_synchronised} onClick={() => void run('clock', () => api.confirmOnboardingClock())}>Confirmer l’heure</button>}
          </section>

          <section className={`panel onboarding-step ${state.steps.recovery ? 'onboarding-done' : ''}`}>
            <div className="onboarding-step-title"><Download /><div><strong>5. Code de récupération</strong><p>Permet de remplacer le mot de passe uniquement pendant une présence physique.</p></div>{state.steps.recovery && <CheckCircle2 />}</div>
            {!state.steps.recovery && !recoveryCode && <button className="button button-secondary" disabled={busy !== ''} onClick={() => void generateCode()}>{busy === 'recovery' ? 'Création…' : 'Créer le code'}</button>}
            {recoveryCode && !state.steps.recovery && <div className="recovery-code"><code>{recoveryCode}</code><button className="button button-primary" disabled={busy !== ''} onClick={() => void saveCode()}><Download size={15} /> Télécharger et confirmer</button></div>}
          </section>
        </div>
      </section>
    </main>
  )
}
