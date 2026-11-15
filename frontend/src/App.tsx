import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  LayoutDashboard, ScanSearch, BadgeCheck, Gauge, ShieldAlert, AlertTriangle,
  CheckCircle2, Link2, Sparkles, ArrowRight, Activity,
} from 'lucide-react'
import './App.css'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
const SEV = ['critical', 'serious', 'moderate', 'minor'] as const
const SEV_COLOR: Record<string, string> = { critical: '#c8443f', serious: '#e07a2a', moderate: '#e3b023', minor: '#3f86c4' }

function App() {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  async function runAudit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    setResult(null)

    try {
      const response = await fetch(`${API}/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Scan failed')
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to connect to backend')
    } finally {
      setLoading(false)
    }
  }

  const counts = result?.counts || {}
  const violations = result?.violations || []
  const groups = result?.groups || []
  const total = SEV.reduce((s, k) => s + (counts[k] || 0), 0)

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">F</div>
          <span>FixProof</span>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <div className="nav-item active"><LayoutDashboard size={18} /> <span>Audit dashboard</span></div>
        <a className="nav-item" href="#results"><ScanSearch size={18} /> <span>Scan results</span></a>
        <a className="nav-item" href="#verification"><BadgeCheck size={18} /> <span>Verified fixes</span></a>

        <div className="sidebar-bottom">
          <div className="status-dot" />
          Backend connection
          <small>{API.includes('127.0.0.1') ? 'Local development' : 'Production'}</small>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <span className="breadcrumb">Workspace / </span>
            <strong>Audit dashboard</strong>
          </div>
          <span className="version">BETA · 0.1</span>
        </header>

        <section className={`welcome ${result ? 'compact' : ''}`}>
          <div>
            <div className="eyebrow">ACCESSIBILITY INTELLIGENCE</div>
            <h1>Make every fix <span>provable.</span></h1>
            {!result && (
              <p>Find accessibility issues, understand their root causes, and verify each fix in a live browser before you ship.</p>
            )}
          </div>
          {result?.after_score != null ? (
            <div className="before-after" aria-label={`Score improved from ${result.score} to ${result.after_score}`}>
              <div><small>BEFORE</small><b>{result.score}</b></div>
              <ArrowRight size={26} />
              <div className="after"><small>VERIFIED AFTER</small><b>{result.after_score}</b></div>
            </div>
          ) : (
            <div className="hero-badge" aria-hidden="true"><Sparkles size={28} /></div>
          )}
        </section>

        <section className="scan-card">
          <div className="section-heading">
            <div>
              <h2>New accessibility scan</h2>
              <p>Enter a public website URL to begin testing.</p>
            </div>
            <span className="live-tag">● {loading ? 'SCANNING' : 'READY TO SCAN'}</span>
          </div>

          <form onSubmit={runAudit} className="scan-form">
            <label className="url-input">
              <span aria-hidden="true"><Link2 size={18} /></span>
              <input
                type="url"
                aria-label="Website URL"
                placeholder="https://example.com"
                value={url}
                onChange={e => setUrl(e.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={loading}>
              {loading ? 'Scanning...' : <>Run scan <ArrowRight size={16} /></>}
            </button>
          </form>

          {loading && (
            <>
              <div className="loading-line"><span /></div>
              <p className="loading-note" role="status">
                Scanning, explaining, then testing each fix in a live browser. This can take up to a minute.
              </p>
            </>
          )}
          {error && <div className="error-box" role="alert">{error}</div>}
          {result?.warning && <div className="warning-box" role="status">{result.warning}</div>}
        </section>

        <section className="metrics">
          <Metric tone="violet" icon={<Gauge size={21} />} label="Accessibility score" value={result ? `${result.score ?? 0}` : '—'} suffix="/100" />
          <Metric tone="coral" icon={<ShieldAlert size={21} />} label="Critical issues" value={counts.critical ?? '—'} />
          <Metric tone="amber" icon={<AlertTriangle size={21} />} label="Serious issues" value={counts.serious ?? '—'} />
          <Metric tone="teal" icon={<CheckCircle2 size={21} />} label="Rules passed" value={result ? result.passed ?? 0 : '—'} />
        </section>

        {result ? (
          <>
            <section className="results-grid" id="results">
              <div className="panel">
                <div className="panel-title">
                  <div><h2>Scan results</h2><p>Detected accessibility violations</p></div>
                  <span className="result-count">{violations.length} rules</span>
                </div>

                {total > 0 && (
                  <>
                    <div className="sev-bar" role="img" aria-label="Severity distribution">
                      {SEV.map(k => counts[k] > 0 && (
                        <span key={k} className={`seg ${k}`} style={{ flex: counts[k] }} title={`${k}: ${counts[k]}`} />
                      ))}
                    </div>
                    <div className="sev-legend">
                      {SEV.map(k => (
                        <span key={k}><i style={{ background: SEV_COLOR[k] }} />{k} {counts[k] ?? 0}</span>
                      ))}
                    </div>
                  </>
                )}

                {violations.length === 0 ? (
                  <div className="empty-state">
                    <div className="success-icon"><CheckCircle2 size={20} /></div>
                    <h3>Looking good!</h3>
                    <p>No axe-core violations were detected on this page.</p>
                  </div>
                ) : (
                  <div className="issue-list">
                    {violations.map((item: any, i: number) => {
                      const sev = (item.impact || 'moderate').toLowerCase()
                      return (
                        <div className={`issue sev-${sev}`} key={i}>
                          <div className="issue-marker">!</div>
                          <div>
                            <strong>{item.id || item.rule_id}</strong>
                            <p>{item.help || item.description || 'Accessibility violation detected'}</p>
                          </div>
                          <span className={`severity ${sev}`}>{sev}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              <div className="panel">
                <div className="panel-title">
                  <div><h2>AI recommendations</h2><p>Root-cause analysis by FixProof AI</p></div>
                  <span className="ai-tag"><Sparkles size={13} /> AI</span>
                </div>

                {groups.length === 0 ? (
                  <div className="empty-state compact">
                    <div className="success-icon"><CheckCircle2 size={20} /></div>
                    <h3>No recommendations to show</h3>
                    <p>AI analysis was not needed or did not complete for this scan.</p>
                  </div>
                ) : groups.map((group: any, i: number) => (
                  <div className="recommendation" key={i}>
                    <strong>{group.title || group.name || `Issue group ${i + 1}`}</strong>
                    <p>{group.explanation || group.why_it_matters}</p>

                    <div className="group-meta">
                      {group.rule_ids?.map((r: string) => <span className="chip" key={r}>{r}</span>)}
                      {group.affected_users?.map((u: string) => <span className="chip user" key={u}>{u}</span>)}
                    </div>

                    {group.recommendation && <div className="recommendation-tip">{group.recommendation}</div>}

                    {group.patches?.map((patch: any, j: number) => (
                      <div className={`patch ${patch.status || 'pending'}`} key={j}>
                        <div className="patch-head">
                          <span className="patch-state">
                            {patch.status === 'verified' ? '✓ Verified'
                              : patch.status === 'failed' ? '✕ Failed'
                              : patch.status === 'skipped' ? '– Skipped' : '○ Pending'}
                          </span>
                          <code>{patch.selector}</code>
                        </div>
                        <pre>{patch.op === 'set_attributes' ? JSON.stringify(patch.attributes, null, 2) : patch.html}</pre>
                        {(patch.note || patch.detail) && <p className="patch-note">{patch.note} {patch.detail}</p>}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </section>

            <section className="verification panel" id="verification">
              <div>
                <div className="eyebrow">REMEDIATION CHECK</div>
                <h2>Fix verification</h2>
                <p>
                  {result.after_score != null
                    ? `Score ${result.score} → ${result.after_score} after applying verified fixes (axe-core, live DOM).`
                    : 'No remediation verification was performed for this scan.'}
                </p>
              </div>
              <div className={`verification-score ${result.after_score != null ? 'verified' : ''}`}>
                {result.after_score ?? '—'}
                <small>{result.after_score != null ? 'AFTER' : 'N/A'}</small>
              </div>
            </section>
          </>
        ) : (
          <section className="empty-dashboard">
            <div className="empty-illustration"><Activity size={26} /></div>
            <h2>Your accessibility workspace</h2>
            <p>Run your first scan to see violations, AI recommendations, and verified improvements here.</p>
          </section>
        )}

        <footer>FIXPROOF · Don't just find accessibility bugs. Prove the fix.</footer>
      </main>
    </div>
  )
}

function Metric({ label, value, suffix = '', tone, icon }: {
  label: string; value: string | number; suffix?: string; tone: string; icon: ReactNode
}) {
  return (
    <div className={`metric-card tone-${tone}`}>
      <i className="metric-icon" aria-hidden="true">{icon}</i>
      <div className="metric-body">
        <span>{label}</span>
        <strong>{value}<small>{suffix}</small></strong>
      </div>
    </div>
  )
}

export default App