/**
 * Lab check — thin UI over the real LegalAkshi FastAPI backend.
 *
 * Flow: create inspection -> add manual declaration -> analyze ->
 * findings + score + violations -> inspector verification.
 * The declaration form is MANUALLY ENTERED (not OCR) and labelled as such.
 * No mock legal results are ever shown here: if the backend is unreachable,
 * a friendly error is displayed instead of fake findings.
 */
import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import {
  ArrowLeft,
  Check,
  CircleAlert,
  FlaskConical,
  Info,
  ScanLine,
  ShieldCheck,
} from 'lucide-react';
import { api, type AnalysisResponse, type Finding } from '@/lib/api';

type Phase = 'idle' | 'working' | 'done' | 'error';

const STATUS_TONE: Record<string, string> = {
  PASS: 'bg-[#e3f7ed] text-[#08784e]',
  FAIL: 'bg-[#fce6e4] text-[#b43b37]',
  NEEDS_REVIEW: 'bg-[#fff4cf] text-[#946b09]',
  NOT_APPLICABLE: 'bg-[#edf1ef] text-[#53625b]',
  PENDING: 'bg-[#fff4cf] text-[#946b09]',
  CONFIRMED: 'bg-[#e3f7ed] text-[#08784e]',
  REJECTED: 'bg-[#edf1ef] text-[#53625b]',
  REQUIRES_REVIEW: 'bg-[#fff4cf] text-[#946b09]',
};

function StatusPill({ value }: { value: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${STATUS_TONE[value] ?? STATUS_TONE.NOT_APPLICABLE}`}>
      {value.replaceAll('_', ' ')}
    </span>
  );
}

function friendlyError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return `Backend unavailable (${api.base}). Start the FastAPI backend (see DEMO.md), then retry. No demo findings are shown here.`;
  }
  const code = msg.match(/API (\d{3})/)?.[1];
  if (code === '401') return 'Backend requires an authenticated officer for this action. In local dev the app sends the X-LegalAkshi-Role header automatically; in production sign in with an officer account (Clerk).';
  if (code === '404') return 'Inspection or product not found on the backend. It may have been created against a restarted (in-memory) backend.';
  if (code === '422') return `Invalid declaration: ${msg}. Check required fields (product name, category) and retry.`;
  if (code === '500') return 'Analysis failed on the backend. Check backend logs; your declaration was not judged.';
  return msg;
}

type Violation = {
  violation_id: string;
  description: string;
  inspector_status: string;
  violation_type?: string;
};

export function LabCheckPage() {
  const [live, setLive] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [form, setForm] = useState({
    product_name: 'Demo Whole Wheat Atta',
    category: 'GENERAL',
    manufacturer: 'Demo Foods Ltd, Kolkata',
    quantity: '5',
    quantity_unit: 'kg',
    manufacturing_date: '2024-05-01',
    mrp: '250',
    consumer_care: '1800-000-000',
    imported: false,
    ecommerce: false,
  });
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [violations, setViolations] = useState<Violation[]>([]);
  const [inspectorId, setInspectorId] = useState('demo-officer');
  const [verifying, setVerifying] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => !cancelled && setLive(false), 4000);
    api.health()
      .then(() => !cancelled && setLive(true))
      .catch(() => !cancelled && setLive(false))
      .finally(() => clearTimeout(timer));
    // One-shot draft from the scan workflow (manual declaration, not OCR).
    try {
      const raw = sessionStorage.getItem('legalakshi:lab-draft');
      if (raw) {
        sessionStorage.removeItem('legalakshi:lab-draft');
        const draft = JSON.parse(raw) as Partial<typeof form>;
        if (!cancelled) setForm((f) => ({ ...f, ...draft }));
      }
    } catch {
      /* ignore malformed drafts */
    }
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: v }));
  };

  async function runAnalysis() {
    setPhase('working');
    setError('');
    setResult(null);
    setViolations([]);
    try {
      const inspection = await api.createInspection({
        inspector_id: 'demo-officer',
        inspector_name: 'Demo Officer',
        business_name: 'Demo Store',
        inspection_date: new Date().toISOString().slice(0, 10),
      });
      const product = await api.addProduct(inspection.inspection_id, {
        product_name: form.product_name,
        category: form.category,
        is_prepackaged: true,
        manufacturer: form.manufacturer || undefined,
        quantity: form.quantity === '' ? null : Number(form.quantity),
        quantity_unit: form.quantity_unit,
        quantity_type: 'weight',
        manufacturing_date: form.manufacturing_date || undefined,
        mrp: form.mrp === '' ? null : Number(form.mrp),
        consumer_care: form.consumer_care || undefined,
        unit_sale_price: 'Rs.50 per kg',
        imported: form.imported,
        ecommerce: form.ecommerce,
      });
      const analysis = await api.analyze(inspection.inspection_id, {
        product_id: product.product_id,
      });
      setResult(analysis);
      const v = await api.violations(inspection.inspection_id);
      setViolations((v.items as Violation[]) ?? []);
      setPhase('done');
    } catch (err) {
      setError(friendlyError(err));
      setPhase('error');
    }
  }

  async function verify(violationId: string, decision: 'CONFIRMED' | 'REJECTED') {
    setVerifying(violationId);
    try {
      // Dev officer header only when the explicit demo switch is enabled;
      // production authenticates via the Clerk JWT bound in ApiAuthBinder.
      await api.verifyViolation(violationId, decision, inspectorId || 'demo-officer', '',
        api.devHeadersEnabled ? { devRole: 'officer' } : undefined);
      setViolations((vs) => vs.map((v) => v.violation_id === violationId ? { ...v, inspector_status: decision } : v));
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setVerifying(null);
    }
  }

  const counts = (result?.findings ?? []).reduce<Record<string, number>>((acc, f: Finding) => {
    acc[f.status] = (acc[f.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Backend lab</p>
          <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Verify against the rule engine.</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">
            Manually entered declarations are checked by the FastAPI backend against PostgreSQL legal rules.
            This is not OCR — values you type are recorded as manual declarations.
          </p>
        </div>
        <Link href="/dashboard" className="inline-flex items-center gap-2 rounded-lg border border-[#dce5df] bg-white px-4 py-2.5 text-sm font-semibold text-[#173a2a]" data-testid="link-lab-back">
          <ArrowLeft size={16} />Back
        </Link>
      </div>

      <div className={`mb-5 flex items-start gap-3 rounded-xl border p-4 text-xs leading-relaxed ${live === false ? 'border-[#f0c9c6] bg-[#fce6e4] text-[#8D3834]' : 'border-[#cfeedd] bg-[#eaf8f1] text-[#4c7761]'}`} data-testid="banner-data-source">
        {live === false ? <CircleAlert size={17} className="mt-0.5 shrink-0" /> : <ShieldCheck size={17} className="mt-0.5 shrink-0 text-[#18B978]" />}
        <span>
          {live === null && 'Checking backend connection…'}
          {live === true && <><b>Data source: LIVE backend</b> ({api.base}). Findings below come from PostgreSQL legal rules via the rule engine — not from mock data.</>}
          {live === false && <><b>Data source: unavailable.</b> Backend not reachable at {api.base}. Start it with <span className="font-mono">uvicorn app.main:app --port 8000</span> (backend/ directory). No findings will be fabricated.</>}
        </span>
      </div>

      <div className="grid gap-5 lg:grid-cols-[.9fr_1.1fr]">
        <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Manual declaration</h2>
          <p className="mt-1 text-xs text-[#849188]">Typed values are stored as manual declarations (confidence: none — never shown as OCR).</p>
          <div className="mt-5 space-y-4">
            {([
              ['Product name', 'product_name'],
              ['Category', 'category'],
              ['Manufacturer', 'manufacturer'],
              ['Quantity', 'quantity'],
              ['Unit', 'quantity_unit'],
              ['Manufacturing date', 'manufacturing_date'],
              ['MRP (blank = missing)', 'mrp'],
              ['Consumer care', 'consumer_care'],
            ] as const).map(([label, key]) => (
              <label key={key} className="block">
                <span className="mb-1.5 block text-xs font-bold text-[#3d5145]">{label}</span>
                <input value={String(form[key])} onChange={set(key)} className="focus-ring w-full rounded-lg border border-[#dbe6de] bg-white px-3.5 py-3 text-sm text-[#20382b] outline-none focus:border-[#18B978]" data-testid={`input-lab-${key}`} />
              </label>
            ))}
            <div className="flex gap-5">
              {(['imported', 'ecommerce'] as const).map((key) => (
                <label key={key} className="flex items-center gap-2 text-xs font-bold text-[#3d5145]">
                  <input type="checkbox" checked={form[key]} onChange={set(key)} data-testid={`input-lab-${key}`} />{key === 'imported' ? 'Imported' : 'E-commerce listing'}
                </label>
              ))}
            </div>
            <button onClick={runAnalysis} disabled={phase === 'working' || live === false} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_5px_12px_rgba(24,185,120,.18)] hover:bg-[#119e67] disabled:cursor-not-allowed disabled:opacity-50" data-testid="button-lab-analyze">
              <ScanLine size={17} />{phase === 'working' ? 'Analyzing…' : 'Run backend analysis'}
            </button>
            {phase === 'error' && <p className="rounded-lg bg-[#fce6e4] p-3 text-xs leading-relaxed text-[#8D3834]" data-testid="text-lab-error">{error}</p>}
          </div>
        </div>

        <div className="space-y-5">
          {!result && phase !== 'working' && (
            <div className="rounded-2xl border border-dashed border-[#cbdad0] bg-white px-6 py-14 text-center">
              <span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-[#eaf8f1] text-[#18B978]"><FlaskConical size={22} /></span>
              <h2 className="mt-4 font-bold text-[#30473a]">No analysis yet</h2>
              <p className="mx-auto mt-2 max-w-sm text-sm text-[#849188]">Enter a declaration and run the backend analysis to see rule-engine findings here.</p>
            </div>
          )}
          {phase === 'working' && <p className="text-sm font-semibold text-[#426050]">Creating inspection, adding product, analyzing…</p>}
          {result && (
            <>
              <div className="rounded-2xl bg-[#eaf8f1] p-7">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-bold uppercase tracking-[.14em] text-[#6a7f72]">Compliance score</p>
                    <div className="mt-4 text-6xl font-extrabold tracking-[-.07em] text-[#0d8556]">{result.score.value}<span className="text-2xl text-current/50">/{result.score.out_of}</span></div>
                  </div>
                  <div className="space-y-2 text-right">
                    <div><StatusPill value={result.status} /></div>
                    <div>{result.score.finalizable
                      ? <span className="inline-flex items-center gap-1 rounded-full bg-[#e3f7ed] px-2.5 py-1 text-[11px] font-bold text-[#08784e]"><Check size={12} />Finalizable</span>
                      : <span className="inline-flex items-center gap-1 rounded-full bg-[#fff4cf] px-2.5 py-1 text-[11px] font-bold text-[#946b09]"><Info size={12} />Review required — not finalizable</span>}</div>
                  </div>
                </div>
                <p className="mt-4 text-xs text-[#4c7761]">
                  {['PASS', 'FAIL', 'NEEDS_REVIEW', 'NOT_APPLICABLE'].map((s) => `${counts[s] ?? 0} ${s.replaceAll('_', ' ')}`).join(' · ')}
                  {' '}· policy {result.score.policy} v{result.score.policy_version}
                </p>
              </div>

              <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
                <h2 className="font-bold text-[#20382b]">Findings ({result.findings.length})</h2>
                <div className="mt-4 space-y-3">
                  {result.findings.map((f) => (
                    <div key={f.rule_id} className="rounded-lg border border-[#edf1ee] p-4" data-testid={`finding-${f.rule_id}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-mono text-xs font-bold text-[#30473a]">{f.rule_id}</p>
                        <StatusPill value={f.status} />
                      </div>
                      <p className="mt-2 text-sm font-semibold text-[#30473a]">{f.requirement}</p>
                      <p className="mt-1 text-xs leading-relaxed text-[#68766f]">{f.explanation}</p>
                      <p className="mt-2 font-mono text-[10px] text-[#849188]">
                        {f.source_reference ?? ''} · detected: {f.evidence?.detected_value ?? '—'} ·
                        confidence: {f.evidence?.confidence ?? 'none (manual)'} · origin: {f.evidence?.confidence_origin ?? 'MANUAL'}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
                <h2 className="font-bold text-[#20382b]">Potential violations ({violations.length})</h2>
                <p className="mt-1 text-xs text-[#849188]">Engine FAILs are potential violations only — an inspector decides.</p>
                <div className="mt-4 space-y-3">
                  {violations.length === 0 && <p className="text-xs text-[#849188]">No violations raised by this analysis.</p>}
                  {violations.map((v) => (
                    <div key={v.violation_id} className="rounded-lg border border-[#edf1ee] p-4" data-testid={`violation-${v.violation_id}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-bold text-[#30473a]">{v.description}</p>
                        <StatusPill value={v.inspector_status} />
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <input value={inspectorId} onChange={(e) => setInspectorId(e.target.value)} placeholder="Inspector ID" className="w-36 rounded-lg border border-[#dbe6de] px-2.5 py-2 text-xs" data-testid="input-lab-inspector" />
                        <button disabled={verifying === v.violation_id} onClick={() => verify(v.violation_id, 'CONFIRMED')} className="rounded-lg bg-[#18B978] px-3 py-2 text-xs font-bold text-white disabled:opacity-50" data-testid={`button-verify-confirm-${v.violation_id}`}>Confirm</button>
                        <button disabled={verifying === v.violation_id} onClick={() => verify(v.violation_id, 'REJECTED')} className="rounded-lg border border-[#dce5df] px-3 py-2 text-xs font-bold text-[#426050] disabled:opacity-50" data-testid={`button-verify-reject-${v.violation_id}`}>Reject</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
