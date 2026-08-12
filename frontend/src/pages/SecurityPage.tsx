import { useCallback, useEffect, useState } from 'react'
import {
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  Info,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import { api } from '../api'
import { Panel } from '../components/Panel'
import { LoadingPanel } from '../components/ui'
import { relativeTime } from '../lib'
import type { Page } from '../navigation'
import type { AuditFinding, AuditSeverity, SecurityAudit } from '../types'

const SEVERITY_LABELS: Record<AuditSeverity, string> = {
  critical: 'Critique',
  high: 'Important',
  medium: 'Moyen',
  low: 'Mineur',
}

type Props = {
  notify: (message: string, error?: boolean) => void
  onOpenPage: (page: Page) => void
}

export function SecurityPage({ notify, onOpenPage }: Props) {
  const [report, setReport] = useState<SecurityAudit>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      setReport(await api.securityAudit())
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Audit indisponible')
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const download = () => {
    if (!report) return
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = `onionpi-audit-${report.generated_at}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  const runAction = async (finding: AuditFinding) => {
    setBusy(true)
    try {
      const result = await api.runSystemAction(finding.action)
      notify(result.message)
      await load()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="page operational-page">
        <div className="page-title"><h1>Audit de sécurité</h1></div>
        <Panel title="Audit indisponible"><p className="form-error">{error}</p></Panel>
      </div>
    )
  }
  if (!report) {
    return (
      <div className="page operational-page">
        <div className="page-title"><h1>Audit de sécurité</h1></div>
        <LoadingPanel label="Analyse de la configuration…" />
      </div>
    )
  }

  const pending = report.findings.filter((finding) => !finding.ok)
  const passed = report.findings.filter((finding) => finding.ok)

  return (
    <div className="page operational-page">
      <div className="page-title">
        <h1>Audit de sécurité</h1>
        <p>Ce que cette configuration expose aujourd’hui, et le geste qui le corrige.</p>
      </div>

      <section className={`audit-hero audit-${report.level}`}>
        <div className="audit-score" role="img" aria-label={`Score de ${report.score} sur 100`}>
          <strong>{report.score}</strong>
          <span>/ 100</span>
        </div>
        <div>
          <span className="section-label">{report.label}</span>
          <h2>{report.summary}</h2>
          <p className="muted">
            Dernière analyse : {relativeTime(report.generated_at).toLocaleLowerCase('fr')} ·
            {' '}{report.counts.total} contrôles
          </p>
        </div>
        <div className="protection-actions">
          <button className="button button-secondary" disabled={busy} onClick={() => void load()}>
            <RefreshCw size={15} /> {busy ? 'Analyse…' : 'Relancer l’audit'}
          </button>
          <button className="button button-ghost" onClick={download}>
            <Download size={15} /> Exporter le rapport
          </button>
        </div>
      </section>

      <Panel
        title={pending.length ? `À corriger (${pending.length})` : 'Rien à corriger'}
        className="audit-panel"
      >
        {!pending.length && (
          <p className="prose">
            Tous les contrôles passent. L’audit reste utile après chaque changement de
            configuration : il relit l’exposition, la fraîcheur des mises à jour et l’âge des secrets.
          </p>
        )}
        <ul className="audit-list">
          {pending.map((finding) => (
            <li className={`audit-item audit-${finding.severity}`} key={finding.id}>
              <span className="audit-icon">
                {finding.severity === 'critical' ? <AlertOctagon /> : finding.severity === 'low' ? <Info /> : <AlertTriangle />}
              </span>
              <div className="audit-body">
                <strong>{finding.title}</strong>
                <span className="audit-severity">{SEVERITY_LABELS[finding.severity]}</span>
                <p>{finding.detail}</p>
                <p className="muted">{finding.remedy}</p>
              </div>
              <div className="audit-actions">
                {finding.action && (
                  <button className="button button-small button-primary" disabled={busy} onClick={() => void runAction(finding)}>
                    <RefreshCw size={14} /> Réparer
                  </button>
                )}
                {finding.page && (
                  <button className="button button-small button-secondary" onClick={() => onOpenPage(finding.page as Page)}>
                    Ouvrir <ArrowRight size={14} />
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title={`Contrôles satisfaits (${passed.length})`} className="audit-panel">
        <ul className="audit-list audit-list-passed">
          {passed.map((finding) => (
            <li className="audit-item audit-ok" key={finding.id}>
              <span className="audit-icon"><CheckCircle2 /></span>
              <div className="audit-body">
                <strong>{finding.title}</strong>
                <p>{finding.detail}</p>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Ce que l’audit ne regarde pas" className="audit-panel">
        <p className="prose">
          <ShieldCheck size={16} /> Aucun nom de domaine visité, aucune adresse de client et aucun
          secret n’entre dans ce rapport : il décrit la configuration, jamais l’usage. C’est
          justement ce qui permet de l’exporter et de le montrer à quelqu’un pour obtenir de l’aide.
        </p>
      </Panel>
    </div>
  )
}
