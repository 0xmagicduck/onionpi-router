import { FormEvent, ReactNode, useState } from 'react'
import { CheckCircle2, Download } from 'lucide-react'
import { api } from '../api'
import { Logo } from '../components/Logo'
import { Badge } from '../components/ui'
import type { OnboardingState } from '../types'

type Props = {
  state: OnboardingState
  refresh: () => Promise<void>
  onPasswordChanged: () => void
}

const STEP_COUNT = 5

function Step({
  index,
  done,
  title,
  description,
  children,
}: {
  index: number
  done: boolean
  title: string
  description: ReactNode
  children?: ReactNode
}) {
  return (
    <section className={`panel onboarding-step ${done ? 'onboarding-done' : ''}`}>
      <div className="onboarding-step-title">
        <span className="onboarding-step-index" aria-hidden="true">{done ? <CheckCircle2 size={17} /> : index}</span>
        <div>
          <strong>{title}</strong>
          <p>{description}</p>
        </div>
        {done && <Badge tone="success" dot>Fait</Badge>}
        {!done && <span className="sr-only">Étape {index} sur {STEP_COUNT}, à faire</span>}
      </div>
      {!done && children}
    </section>
  )
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
        <div className="onboarding-progress">
          <span>{completed} sur {STEP_COUNT} vérifications</span>
          <div
            className="meter-track"
            role="meter"
            aria-label="Progression de la première ouverture"
            aria-valuenow={completed}
            aria-valuemin={0}
            aria-valuemax={STEP_COUNT}
          >
            <span style={{ width: `${(completed / STEP_COUNT) * 100}%` }} />
          </div>
        </div>
      </header>

      <section className="onboarding-content">
        <div className="page-title">
          <h1>Rendre cette OnionPi sûre et récupérable</h1>
          <p>Ces cinq contrôles restent locaux. Le Wi-Fi ne sera déclaré prêt qu’une fois le coupe-circuit testé.</p>
        </div>

        {error && <p className="form-error" role="alert">{error}</p>}

        <div className="onboarding-steps">
          <Step
            index={1}
            done={state.steps.password}
            title="Mot de passe administrateur"
            description="Choisissez le secret définitif de l’interface."
          >
            <form className="settings-form" onSubmit={(event) => void changePassword(event)}>
              <label>Mot de passe actuel<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
              <label>Nouveau mot de passe<input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
              <label>Confirmation<input type="password" autoComplete="new-password" minLength={12} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
              <button className="button button-primary" disabled={busy !== ''}>
                {busy === 'password' ? 'Modification…' : 'Modifier et se reconnecter'}
              </button>
            </form>
          </Step>

          <Step
            index={2}
            done={state.steps.interfaces}
            title="Interfaces réseau"
            description={<>Internet sur <code className="mono">{state.interfaces.wan}</code>, point d’accès sur <code className="mono">{state.interfaces.access_point}</code>.</>}
          >
            <button
              className="button button-secondary"
              disabled={busy !== ''}
              onClick={() => void run('interfaces', () => api.confirmOnboardingInterfaces(state.interfaces.wan, state.interfaces.access_point))}
            >
              Je confirme ce câblage
            </button>
          </Step>

          <Step
            index={3}
            done={state.steps.firewall}
            title="Coupe-circuit"
            description="Recharge nftables et prouve que toute sortie directe reste bloquée."
          >
            <button className="button button-secondary" disabled={busy !== ''} onClick={() => void run('firewall', () => api.testOnboardingFirewall())}>
              {busy === 'firewall' ? 'Test en cours…' : 'Tester le coupe-circuit'}
            </button>
          </Step>

          <Step
            index={4}
            done={state.steps.clock}
            title="Heure système"
            description={state.clock_synchronised
              ? 'L’heure est synchronisée, Tor peut vérifier ses certificats.'
              : 'La synchronisation NTP n’est pas encore confirmée.'}
          >
            <button
              className="button button-secondary"
              disabled={busy !== '' || !state.clock_synchronised}
              onClick={() => void run('clock', () => api.confirmOnboardingClock())}
            >
              Confirmer l’heure
            </button>
          </Step>

          <Step
            index={5}
            done={state.steps.recovery}
            title="Code de récupération"
            description="Permet de remplacer le mot de passe uniquement pendant une présence physique."
          >
            {!recoveryCode ? (
              <button className="button button-secondary" disabled={busy !== ''} onClick={() => void generateCode()}>
                {busy === 'recovery' ? 'Création…' : 'Créer le code'}
              </button>
            ) : (
              <div className="recovery-code">
                <code>{recoveryCode}</code>
                <button className="button button-primary" disabled={busy !== ''} onClick={() => void saveCode()}>
                  <Download size={15} /> Télécharger et confirmer
                </button>
              </div>
            )}
          </Step>
        </div>
      </section>
    </main>
  )
}
