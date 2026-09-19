/**
 * Inspector workspace pages — dense, evidence-focused, action-oriented.
 *
 * Reuses existing backend APIs only (inspections, compliance, reports,
 * officer queue/cases, complaints, consumer lookup for product links).
 * No new backend, no OCR changes, no mock data: missing data renders
 * "Not available" / "No data yet" / "Not verified".
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'wouter';
import {
  ArrowLeft, ArrowRight, Box, Check, CheckCircle2, ChevronRight, CircleAlert,
  ClipboardCheck, Download, FileText, Info, Search,
} from 'lucide-react';
import { api, type AuthOptions, type Suggestion } from '@/lib/api';
import { parseComplaintCategory } from '@/pages/consumer-verify';

function useOfficerAuth(): AuthOptions | undefined {
  return api.devHeadersEnabled ? { devRole: 'officer' as const } : undefined;
}

type AnyRow = Record<string, any>;

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function Pill({ tone, children, testId }: { tone: 'green' | 'yellow' | 'red' | 'neutral'; children: React.ReactNode; testId?: string }) {
  const tones = { green: 'bg-[#e3f7ed] text-[#08784e]', yellow: 'bg-[#fff4cf] text-[#946b09]', red: 'bg-[#fce6e4] text-[#b43b37]', neutral: 'bg-[#edf1ef] text-[#53625b]' };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${tones[tone]}`} data-testid={testId}>{children}</span>;
}

export function reportTone(status?: string): 'green' | 'yellow' | 'red' | 'neutral' {
  if (status === 'COMPLIANT') return 'green';
  if (status === 'NON_COMPLIANT') return 'red';
  if (status === 'NEEDS_REVIEW') return 'yellow';
  return 'neutral';
}

function fmtDate(v?: string): string {
  return (v || '').slice(0, 10) || '—';
}

function SearchBox({ value, onChange, testId, placeholder }: { value: string; onChange: (v: string) => void; testId: string; placeholder: string }) {
  return <div className="relative flex-1"><Search size={16} className="absolute left-3 top-3 text-[#97a49d]" /><input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="focus-ring w-full rounded-lg border border-[#dce7df] bg-white py-2.5 pl-9 pr-3 text-sm outline-none" data-testid={testId} /></div>;
}

/* ------------------------------- Inspections ------------------------------ */

type InspectionRow = {
  inspection: AnyRow;
  products: AnyRow[];
  report?: AnyRow | null;
  violations: AnyRow[];
};

/**
 * Two-phase inspection rows: phase 1 renders immediately from the two
 * list endpoints (inspections + queue); phase 2 merges per-row products
 * and reports progressively as they resolve. List pages never block on
 * detail data.
 */
async function loadInspectionRows(
  auth: AuthOptions | undefined,
  onPhase: (rows: InspectionRow[], phase: 1 | 2 | 3) => void,
): Promise<void> {
  const [items, queue] = await Promise.all([
    api.listInspections().catch(() => [] as AnyRow[]),
    api.officerQueue({}, auth).catch(() => [] as AnyRow[]),
  ]);
  const light: InspectionRow[] = (items as AnyRow[]).map((insp: AnyRow) => ({
    inspection: insp, products: [], report: null,
    violations: (queue as AnyRow[]).filter((v) => v.inspection_id === insp.inspection_id),
  }));
  onPhase(light, 1);
  const withProducts = await Promise.all(light.map(async (r) => {
    try {
      const full = (await api.getInspection(r.inspection.inspection_id)) as AnyRow;
      return { ...r, products: full.products ?? [] };
    } catch {
      return r; // products unavailable for this row; row still renders
    }
  }));
  onPhase(withProducts, 2);
  const full = await Promise.all(withProducts.map(async (r) => {
    try {
      return { ...r, report: ((await api.report(r.inspection.inspection_id)) as AnyRow) ?? null };
    } catch {
      return r; // not analyzed yet; row still renders
    }
  }));
  onPhase(full, 3);
}

function inspectionDecision(violations: AnyRow[]): string {
  const open = violations.filter((v) => v.inspector_status === 'PENDING' || v.inspector_status === 'REQUIRES_REVIEW').length;
  const confirmed = violations.filter((v) => v.inspector_status === 'CONFIRMED').length;
  const cleared = violations.filter((v) => v.inspector_status === 'REJECTED').length;
  if (violations.length === 0) return 'No violations raised';
  const parts: string[] = [];
  if (open > 0) parts.push(`${open} open`);
  if (confirmed > 0) parts.push(`${confirmed} confirmed`);
  if (cleared > 0) parts.push(`${cleared} cleared`);
  return parts.join(' · ') || 'No violations raised';
}

export function InspectionsPage() {
  const auth = useOfficerAuth();
  const [rows, setRows] = useState<InspectionRow[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const load = () => {
    setLoading(true);
    setError('');
    setRows([]);
    loadInspectionRows(auth, (rows) => {
      setRows(rows);
      setLoading(false); // shell + first rows render after phase 1
    }).catch((e: unknown) => {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    });
  };
  useEffect(load, []);
  const visible = useMemo(() => rows.filter((r) => {
    const insp = r.inspection;
    const prod = r.products[0] ?? {};
    if (status === 'not-analyzed' && r.report) return false;
    if (status && status !== 'not-analyzed' && r.report?.status !== status) return false;
    const date = (insp.inspection_date || '').slice(0, 10);
    if (from && date < from) return false;
    if (to && date > to) return false;
    if (search.trim()) {
      const hay = [insp.inspection_id, prod.product_name, prod.brand, prod.manufacturer, prod.barcode, prod.category, prod.batch_lot]
        .map((v) => String(v ?? '')).join(' ').toLowerCase();
      if (!hay.includes(search.trim().toLowerCase())) return false;
    }
    return true;
  }), [rows, search, status, from, to]);
  return <div>
    <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Inspector · inspections</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Inspections.</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Every persisted inspection with its product, findings, and officer decision state. New inspections start at Scan &amp; Inspect.</p>
    <div className="mt-5 flex flex-col gap-3 lg:flex-row">
      <SearchBox value={search} onChange={setSearch} testId="input-inspection-search" placeholder="Search product, inspection ID, barcode, manufacturer, batch/lot…" />
      <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-inspection-status">
        <option value="">All statuses</option>
        <option value="COMPLIANT">Compliant</option>
        <option value="NON_COMPLIANT">Non-compliant</option>
        <option value="NEEDS_REVIEW">Needs review</option>
        <option value="not-analyzed">Not analyzed</option>
      </select>
      <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-inspection-from" />
      <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-inspection-to" />
    </div>
    {error && <p className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-inspections-error">{error}</p>}
    <div className="mt-5 overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">
      <div className="hidden grid-cols-[1.4fr_1fr_110px_130px_150px] gap-4 border-b border-[#edf1ee] bg-[#fbfcfb] px-5 py-3 text-[10px] font-bold uppercase tracking-[.13em] text-[#94a098] md:grid"><span>Inspection / product</span><span>Manufacturer</span><span>Findings</span><span>Status</span><span>Decision</span></div>
      {loading ? <p className="p-6 text-xs text-[#849188]">Loading inspections…</p>
        : visible.length === 0 ? <div className="p-6 text-center"><p className="text-sm font-bold text-[#30473a]">No data yet</p><p className="mx-auto mt-1 max-w-sm text-xs text-[#849188]">No inspections match. New inspections start at Scan &amp; Inspect.</p></div>
          : visible.map((r) => {
            const insp = r.inspection;
            const prod = r.products[0] ?? {};
            const findings = r.report?.findings?.length ?? 0;
            return <div key={insp.inspection_id} className="grid gap-3 border-b border-[#edf1ee] px-5 py-4 last:border-0 md:grid-cols-[1.4fr_1fr_110px_130px_150px] md:items-center md:gap-4" data-testid={`inspection-${String(insp.inspection_id).slice(0, 8)}`}>
              <div className="flex items-center gap-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[#e0f7eb] text-[#13885c]"><Box size={16} /></span><div className="min-w-0"><p className="truncate text-sm font-bold text-[#30473a]">{prod.product_name || 'Unnamed product'}</p><p className="mt-1 font-mono text-[11px] text-[#97a39b]">{String(insp.inspection_id).slice(0, 8)} · {fmtDate(insp.inspection_date)}</p></div></div>
              <span className="truncate text-xs text-[#68766f]">{prod.manufacturer || 'Not available'}</span>
              <span className="font-mono text-xs text-[#68766f]">{r.report ? `${findings} finding${findings === 1 ? '' : 's'}` : 'Not analyzed'}</span>
              <span>{r.report ? <Pill tone={reportTone(r.report.status)}>{(r.report.status || '').replaceAll('_', ' ')}</Pill> : <Pill tone="neutral">Not analyzed</Pill>}</span>
              <div className="flex items-center gap-3"><span className="text-[11px] text-[#68766f]">{inspectionDecision(r.violations)}</span><Link href={`/inspector/inspections/${insp.inspection_id}`} className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-inspection-${String(insp.inspection_id).slice(0, 8)}`}>Open <ChevronRight size={14} /></Link></div>
            </div>;
          })}
    </div>
  </div>;
}

/* ---------------------------- Inspection detail --------------------------- */

function StageStepper({ stages }: { stages: { label: string; state: 'done' | 'current' | 'todo' }[] }) {
  return <div className="flex flex-wrap items-center gap-2" data-testid="inspection-stages">
    {stages.map((s, i) => <span key={s.label} className="flex items-center gap-2">
      <span className={`rounded-full px-3 py-1 text-[11px] font-bold ${s.state === 'done' ? 'bg-[#e3f7ed] text-[#08784e]' : s.state === 'current' ? 'bg-[#fff4cf] text-[#946b09]' : 'bg-[#edf1ef] text-[#53625b]'}`}>{s.label}</span>
      {i < stages.length - 1 && <ArrowRight size={12} className="text-[#9aa69f]" />}
    </span>)}
  </div>;
}

function packSizeOf(p: AnyRow): string {
  const q = p.quantity;
  if (q === null || q === undefined || q === '') return 'Not available';
  return `${q} ${p.quantity_unit ?? ''}`.trim() || 'Not available';
}

export function InspectionDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id ?? '';
  const auth = useOfficerAuth();
  const [inspection, setInspection] = useState<AnyRow | null>(null);
  const [products, setProducts] = useState<AnyRow[]>([]);
  const [picked, setPicked] = useState(0);
  const [report, setReport] = useState<AnyRow | null>(null);
  const [findings, setFindings] = useState<AnyRow[]>([]);
  const [violations, setViolations] = useState<AnyRow[]>([]);
  const [history, setHistory] = useState<{ inspection: AnyRow; product: AnyRow; status?: string }[]>([]);
  const [previous, setPrevious] = useState<{ product: AnyRow; inspection: AnyRow; findings: AnyRow[] } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    setLoading(true);
    setError('');
    (async () => {
      // Independent reads go out together; only compliance waits on the
      // product id from the inspection itself.
      const [full, queue, all] = await Promise.all([
        api.getInspection(id) as Promise<AnyRow>,
        api.officerQueue({}, auth).catch(() => [] as AnyRow[]),
        api.listInspections().catch(() => [] as AnyRow[]),
      ]);
      setInspection(full);
      const prods: AnyRow[] = full.products ?? [];
      setProducts(prods);
      const prod = prods[picked] ?? prods[0];
      const pid = prod?.product_id ?? prod?.inspected_product_id;
      const [rep, comp] = await Promise.all([
        (api.report(id) as Promise<AnyRow>).catch(() => null),
        (pid ? api.compliance(id, pid) as Promise<AnyRow[]> : Promise.resolve([])).catch(() => [] as AnyRow[]),
      ]);
      setReport(rep);
      setFindings(comp);
      setViolations((queue as AnyRow[]).filter((v) => v.inspection_id === id));
      // Inspection history: other inspections of the same product name.
      // Bounded scan (newest 25) so history never N+1s the whole database.
      if (prod?.product_name) {
        const candidates = (all as AnyRow[])
          .filter((insp) => insp.inspection_id !== id)
          .sort((a, b) => (b.inspection_date || '').localeCompare(a.inspection_date || ''))
          .slice(0, 25);
        const scanned = await Promise.all(candidates.map(async (insp): Promise<{ inspection: AnyRow; product: AnyRow; status: string | undefined } | null> => {
          try {
            const f = (await api.getInspection(insp.inspection_id)) as AnyRow;
            const match = (f.products ?? []).find((p: AnyRow) => (p.product_name || '').toLowerCase() === String(prod.product_name).toLowerCase());
            if (!match) return null;
            let st: string | undefined;
            try {
              st = ((await api.report(insp.inspection_id)) as AnyRow).status as string | undefined;
            } catch { /* no report */ }
            return { inspection: insp, product: match as AnyRow, status: st };
          } catch {
            return null; // skip unreadable inspection
          }
        }));
        const others = scanned.filter((x): x is { inspection: AnyRow; product: AnyRow; status: string | undefined } => x !== null);
        setHistory(others);
        // Comparison: most recent previous inspection of the same product.
        const prevEntry = others[0];
        if (prevEntry) {
          const prevPid = prevEntry.product.product_id ?? prevEntry.product.inspected_product_id;
          let prevFindings: AnyRow[] = [];
          if (prevPid) {
            try {
              prevFindings = (await api.compliance(prevEntry.inspection.inspection_id, prevPid)) as AnyRow[];
            } catch { /* no findings */ }
          }
          setPrevious({ product: prevEntry.product, inspection: prevEntry.inspection, findings: prevFindings });
        } else {
          setPrevious(null);
        }
      }
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading inspection…</p>;
  if (error || !inspection) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-inspection-error">{error || 'Inspection not found.'}</div>;
  const prod: AnyRow = products[picked] ?? {};
  const pid = prod.product_id ?? prod.inspected_product_id;
  const pending = violations.filter((v) => v.inspector_status === 'PENDING' || v.inspector_status === 'REQUIRES_REVIEW');
  const decided = violations.filter((v) => v.inspector_status !== 'PENDING');
  const stages = [
    { label: 'Capture', state: (products.length > 0 ? 'done' : 'todo') as 'done' | 'current' | 'todo' },
    { label: 'Review', state: (report ? 'done' : 'current') as 'done' | 'current' | 'todo' },
    { label: 'Analyze', state: (report ? 'done' : 'todo') as 'done' | 'current' | 'todo' },
    { label: 'Decide', state: (report && pending.length === 0 ? 'done' : report ? 'current' : 'todo') as 'done' | 'current' | 'todo' },
    { label: 'Report', state: (report && pending.length === 0 ? 'current' : 'todo') as 'done' | 'current' | 'todo' },
  ];
  const violationFor = (ruleId: string) => violations.find((v) => v.check_id === ruleId);
  const compareFields: [string, string][] = [
    ['MRP', 'mrp'], ['Quantity', 'quantity'], ['Unit', 'quantity_unit'], ['Manufacturer', 'manufacturer'],
    ['Batch / lot', 'batch_lot'], ['Manufacturing date', 'manufacturing_date'], ['Best before', 'best_before'],
    ['FSSAI licence', 'fssai_license'], ['Consumer care', 'consumer_care'], ['Barcode', 'barcode'],
  ];
  const findingState = (ruleId: string) => findings.find((f) => f.rule_id === ruleId)?.status;
  const prevFindingState = (ruleId: string) => previous?.findings.find((f) => f.rule_id === ruleId)?.status;
  const downloadPdf = async () => {
    setDownloading(true);
    try {
      downloadBlob(await api.reportPdf(id), `legalakshi-official-report-${id.slice(0, 8)}.pdf`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };
  return <div>
    <Link href="/inspector/inspections" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-inspections"><ArrowLeft size={14} />Back to inspections</Link>
    <div className="mt-4 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Inspector · inspection detail</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{prod.product_name || 'Unnamed product'}</h1>
        <p className="mt-2 font-mono text-[11px] text-[#849188]">Inspection {String(id).slice(0, 8)} · {(inspection.inspection_date || '').slice(0, 10)} · {inspection.inspection_type || 'PHYSICAL'}</p></div>
      {report ? <Pill tone={reportTone(report.status)} testId="inspection-status">{(report.status || '').replaceAll('_', ' ')}</Pill> : <Pill tone="neutral">Not analyzed</Pill>}
    </div>
    <div className="mt-4"><StageStepper stages={stages} /></div>
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Inspection &amp; product</h2>
          {products.length > 1 && <div className="mt-3 flex flex-wrap gap-2">{products.map((p, i) => <button key={p.product_id ?? p.inspected_product_id ?? i} onClick={() => setPicked(i)} className={`rounded-full px-3 py-1.5 text-xs font-bold ${i === picked ? 'bg-[#dff5e9] text-[#12885c]' : 'bg-[#f4f7f5] text-[#607069]'}`} data-testid={`button-product-${i}`}>{p.product_name || `Product ${i + 1}`}</button>)}</div>}
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {[['Business', inspection.business_name || '—'], ['Product', prod.product_name || '—'], ['Brand', prod.brand || 'Not available'], ['Manufacturer', prod.manufacturer || 'Not available'], ['Pack size', packSizeOf(prod)], ['Barcode', prod.barcode || 'Not available'], ['Batch / lot', prod.batch_lot || 'Not available'], ['Category', prod.category || 'Not available']].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{String(value)}</p></div>)}
          </div>
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Rule-engine findings ({findings.length})</h2>
          {findings.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No data yet — run analysis from Scan &amp; Inspect.</p>
            : <div className="mt-3 space-y-3">{findings.map((f) => {
              const v = violationFor(f.rule_id);
              return <div key={f.rule_id} className="rounded-lg border border-[#edf1ee] p-4" data-testid={`finding-${f.rule_id}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-mono text-xs font-bold text-[#30473a]">{f.rule_id}</p>
                  <Pill tone={f.status === 'PASS' ? 'green' : f.status === 'FAIL' ? 'red' : 'yellow'}>{f.status}</Pill>
                </div>
                <p className="mt-2 text-sm font-semibold text-[#30473a]">{f.requirement}</p>
                <p className="mt-1 text-xs leading-relaxed text-[#68766f]">Observed: <b>{f.evidence?.detected_value ?? '—'}</b></p>
                <p className="mt-1 text-xs leading-relaxed text-[#68766f]">{f.explanation}</p>
                {v && <Link href={`/inspector/audit/${v.violation_id}`} className="mt-2 inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-finding-case-${f.rule_id}`}>Open enforcement case <ArrowRight size={12} /></Link>}
              </div>;
            })}</div>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Violations &amp; officer decision ({violations.length})</h2>
          {violations.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No violations raised for this inspection.</p>
            : <div className="mt-3 space-y-3">{violations.map((v) => <div key={v.violation_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#fff7df] p-3" data-testid={`violation-${String(v.violation_id).slice(0, 8)}`}>
              <div className="min-w-0"><p className="truncate text-xs font-bold text-[#6e5a27]">{v.description || v.violation_type}</p><p className="mt-1 font-mono text-[10px] text-[#a08c5a]">{v.check_id || ''} · {v.inspector_status}{v.verification_date ? ` · decided ${String(v.verification_date).slice(0, 10)}` : ''}{v.inspector_id ? ` by ${v.inspector_id}` : ''}</p></div>
              <Link href={`/inspector/audit/${v.violation_id}`} className="rounded-lg bg-[#18B978] px-3 py-2 text-xs font-bold text-white" data-testid={`link-violation-${String(v.violation_id).slice(0, 8)}`}>Decide <ArrowRight size={12} className="ml-1 inline" /></Link>
            </div>)}</div>}
        </div>
        {previous && <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft" data-testid="panel-comparison">
          <h2 className="font-bold text-[#20382b]">Comparison with previous inspection</h2>
          <p className="mt-1 text-[11px] text-[#849188]">{fmtDate(previous.inspection.inspection_date)} · differences are shown as “Changed” — significance is for the rule engine and the officer, never inferred here.</p>
          <div className="mt-3 space-y-2">{compareFields.map(([label, key]) => {
            const a = String(prod[key] ?? '');
            const b = String(previous.product[key] ?? '');
            const changed = a !== b && (a !== '' || b !== '');
            return <div key={key} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#fbfcfb] px-3 py-2 text-xs"><span className="font-bold text-[#42554a]">{label}</span><span className="font-mono text-[#586a5f]">{b || '—'} → {a || '—'}</span>{changed ? <Pill tone="yellow">Changed</Pill> : <Pill tone="neutral">Same</Pill>}</div>;
          })}</div>
        </div>}
      </div>
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Decision record</h2>
          {violations.length === 0 ? <p className="mt-2 text-xs text-[#849188]">{report ? 'No violations — nothing awaiting a decision.' : 'No data yet.'}</p>
            : <div className="mt-3 space-y-2 text-xs text-[#4c7761]"><div className="flex items-center justify-between"><span>Open</span><b>{pending.length}</b></div><div className="flex items-center justify-between"><span>Decided</span><b>{decided.length}</b></div></div>}
          <div className="mt-4 grid gap-2">
            <button onClick={downloadPdf} disabled={downloading || !report} className="inline-flex items-center justify-center gap-2 rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050] disabled:opacity-50" data-testid="button-inspection-pdf"><Download size={16} />{downloading ? 'Generating…' : 'Download report (PDF)'}</button>
            <Link href="/inspector/enforcement" className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#119e67]" data-testid="link-inspection-enforcement">Open enforcement queue</Link>
          </div>
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Inspection history</h2>
          <p className="mt-1 text-[11px] text-[#849188]">Previous inspections of {prod.product_name || 'this product'}</p>
          <div className="mt-3 space-y-2">{history.length === 0 ? <p className="text-xs text-[#849188]">No earlier inspections on record.</p>
            : history.map((h) => <Link key={h.inspection.inspection_id} href={`/inspector/inspections/${h.inspection.inspection_id}`} className="flex items-center justify-between gap-2 rounded-lg p-2.5 hover:bg-[#f4f8f5]" data-testid={`link-history-${String(h.inspection.inspection_id).slice(0, 8)}`}><span className="text-xs font-bold text-[#30473a]">{fmtDate(h.inspection.inspection_date)}</span>{h.status ? <Pill tone={reportTone(h.status)}>{h.status.replaceAll('_', ' ')}</Pill> : <Pill tone="neutral">No data yet</Pill>}</Link>)}</div>
        </div>
      </div>
    </div>
  </div>;
}

/* --------------------------- Complaint detail ----------------------------- */

export function parseExtraLines(description: string): { batch?: string; place?: string; date?: string; body: string } {
  const lines = (description || '').split('\n');
  let batch: string | undefined;
  let place: string | undefined;
  let date: string | undefined;
  const rest: string[] = [];
  for (const ln of lines) {
    let m = /^\s*batch\s*\/\s*lot\s*:\s*(.+?)\s*$/i.exec(ln);
    if (m && (m[1].trim() !== '—' && m[1].trim() !== '')) { batch = m[1].trim(); continue; }
    m = /^\s*purchased at\s*:\s*(.+?)\s*$/i.exec(ln);
    if (m) {
      const v = m[1].trim();
      const dm = /^(.*)\s+on\s+(\d{4}-\d{2}-\d{2})\s*$/.exec(v);
      if (dm) { place = dm[1].trim() || undefined; date = dm[2]; }
      else if (v !== '—' && v !== '') { place = v; }
      continue;
    }
    rest.push(ln);
  }
  return { batch, place, date, body: rest.join('\n').trim() };
}

export function ComplaintDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id ?? '';
  const auth = useOfficerAuth();
  const [complaint, setComplaint] = useState<AnyRow | null>(null);
  const [related, setRelated] = useState<{ inspection: AnyRow; product: AnyRow; status?: string; violations: AnyRow[] }[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const load = () => {
    setLoading(true);
    setError('');
    (async () => {
      const c = (await api.getComplaint(id)) as AnyRow;
      setComplaint(c);
      // Related inspections: existing inspections for the same product.
      // Lookup and queue are independent; per-inspection reads fan out.
      if (c.product_name) {
        const [found, queue] = await Promise.all([
          api.consumerLookup({ product_name: c.product_name }).catch(() => ({ matches: [] as AnyRow[] })) as Promise<{ matches: AnyRow[] }>,
          api.officerQueue({}, auth).catch(() => [] as AnyRow[]),
        ]);
        const rel = await Promise.all((found.matches ?? []).slice(0, 5).map(async (m): Promise<{ inspection: AnyRow; product: AnyRow; status?: string; violations: AnyRow[] } | null> => {
          try {
            const full = (await api.getInspection(m.inspection_id)) as AnyRow;
            return { inspection: full, product: (full.products ?? [])[0] ?? {}, status: m.status as string | undefined, violations: (queue as AnyRow[]).filter((v) => v.inspection_id === m.inspection_id) };
          } catch {
            return null; // skip unreadable inspection
          }
        }));
        setRelated(rel.filter((x): x is { inspection: AnyRow; product: AnyRow; status?: string; violations: AnyRow[] } => x !== null));
      } else {
        setRelated([]);
      }
    })().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))).finally(() => setLoading(false));
  };
  useEffect(load, [id]);
  const act = async (toStatus: string) => {
    setSaving(true);
    setError('');
    try {
      const updated = (await api.transitionComplaint(id, toStatus, note, auth)) as AnyRow;
      setComplaint(updated);
      setNote('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading complaint…</p>;
  if (error && !complaint) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-complaint-error">{error}</div>;
  if (!complaint) return <p className="text-xs text-[#849188]">No data yet.</p>;
  const { category } = parseComplaintCategory(complaint.description || '');
  const extra = parseExtraLines(complaint.description || '');
  const timeline: AnyRow[] = complaint.timeline ?? [];
  return <div>
    <Link href="/inspector/complaints" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-complaints"><ArrowLeft size={14} />Back to complaints</Link>
    <div className="mt-4 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Inspector · complaint detail</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{complaint.product_name || 'Unnamed product'}</h1>
        <p className="mt-2 font-mono text-[11px] text-[#849188]">Complaint {String(complaint.complaint_id).slice(0, 8)} · filed {fmtDate(complaint.created_at)}</p></div>
      <Pill tone={complaint.status === 'CLOSED' || complaint.status === 'RESOLVED' ? 'green' : 'yellow'} testId="complaint-status">{complaint.status}</Pill>
    </div>
    {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]">{error}</p>}
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Complaint</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {[['Category', category || 'Not available'], ['Batch / lot', extra.batch || 'Not available'], ['Purchase location', extra.place || complaint.retailer || 'Not available'], ['Purchase date', extra.date || 'Not available'], ['Submitted date', fmtDate(complaint.created_at)], ['Last update', fmtDate(complaint.updated_at)], ['Retailer', complaint.retailer || 'Not available'], ['City', complaint.city || 'Not available']].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{String(value)}</p></div>)}
          </div>
          {extra.body && <div className="mt-4 rounded-lg bg-[#fbfcfb] p-4"><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">Description</p><p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-[#586a5f]">{extra.body}</p></div>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Complaint timeline</h2>
          {timeline.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No timeline events yet.</p>
            : <div className="mt-3 space-y-2">{timeline.map((t, i) => <div key={`${t.event_type}-${i}`} className="flex items-center gap-2 text-xs text-[#586a5f]"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><Check size={11} /></span><span><b>{t.event_type}</b>{t.to_status ? ` → ${t.to_status}` : ''}{t.note ? ` · ${t.note}` : ''}</span><span className="ml-auto font-mono text-[10px] text-[#9aa69f]">{(t.created_at || '').slice(0, 16)}</span></div>)}</div>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Related inspections ({related.length})</h2>
          <p className="mt-1 text-[11px] text-[#849188]">Existing inspections for this product — no duplicate records are created from a complaint.</p>
          {related.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No previous inspections found for this product.</p>
            : <div className="mt-3 space-y-2">{related.map((r) => <div key={r.inspection.inspection_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#fbfcfb] p-3">
              <div className="min-w-0"><p className="text-xs font-bold text-[#30473a]">{fmtDate(r.inspection.inspection_date)} · {r.inspection.business_name || 'Inspection'}</p><p className="mt-1 font-mono text-[10px] text-[#9aa69f]">{String(r.inspection.inspection_id).slice(0, 8)}{r.status ? ` · ${r.status.replaceAll('_', ' ')}` : ''}{r.violations.length > 0 ? ` · ${r.violations.length} violation${r.violations.length === 1 ? '' : 's'}` : ''}</p></div>
              <Link href={`/inspector/inspections/${r.inspection.inspection_id}`} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-related-${String(r.inspection.inspection_id).slice(0, 8)}`}>View inspection <ArrowRight size={12} className="ml-1 inline" /></Link>
            </div>)}</div>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Related findings</h2>
          {related.every((r) => r.violations.length === 0) ? <p className="mt-2 text-xs text-[#849188]">No persisted violations or findings linked to these inspections.</p>
            : <div className="mt-3 space-y-2">{related.flatMap((r) => r.violations).map((v) => <div key={v.violation_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#fff7df] p-3">
              <div className="min-w-0"><p className="truncate text-xs font-bold text-[#6e5a27]">{v.description || v.violation_type}</p><p className="mt-1 font-mono text-[10px] text-[#a08c5a]">{v.check_id || ''} · {v.inspector_status}</p></div>
              <Link href={`/inspector/audit/${v.violation_id}`} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-finding-${String(v.violation_id).slice(0, 8)}`}>Open case</Link>
            </div>)}</div>}
        </div>
      </div>
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Officer actions</h2>
          <p className="mt-1 text-[11px] text-[#849188]">Only lifecycle transitions the backend supports. Illegal moves are rejected server-side.</p>
          <label className="mt-4 block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Note</span><textarea value={note} onChange={(e) => setNote(e.target.value)} className="focus-ring h-20 w-full resize-none rounded-lg border border-[#dbe6de] px-3 py-2 text-sm outline-none focus:border-[#18B978]" data-testid="input-complaint-note" /></label>
          <div className="mt-4 grid gap-2">
            {[['ACKNOWLEDGED', 'Acknowledge'], ['UNDER_REVIEW', 'Mark under review'], ['ACTION_TAKEN', 'Record action taken'], ['CLOSED', 'Close complaint']].map(([status, label]) => <button key={status} disabled={saving || complaint.status === status} onClick={() => act(status)} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050] hover:border-[#18B978] disabled:opacity-50" data-testid={`button-complaint-${status.toLowerCase()}`}>{label}</button>)}
          </div>
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Enforcement</h2>
          <p className="mt-1 text-[11px] text-[#849188]">Related violations live in the enforcement queue — the single case-work list.</p>
          <Link href="/inspector/enforcement" className="mt-3 inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#119e67]" data-testid="link-complaint-enforcement">Open enforcement queue</Link>
        </div>
      </div>
    </div>
  </div>;
}

/* --------------------------- Officer suggestions -------------------------- */

const SUGGESTION_STATUSES = ['SUBMITTED', 'UNDER_REVIEW', 'ACKNOWLEDGED', 'ACTIONED', 'CLOSED'];
const SUGGESTION_CATEGORIES = ['Product authenticity', 'Packaging verification', 'Food safety', 'Label transparency', 'Consumer awareness', 'Digital verification', 'Other'];

export function OfficerSuggestionsPage() {
  const auth = useOfficerAuth();
  const [items, setItems] = useState<Suggestion[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');
  const load = () => {
    setLoading(true);
    setError('');
    const params: Record<string, string> = {};
    if (status) params.status = status;
    if (category) params.category = category;
    if (from) params.date_from = from;
    if (to) params.date_to = to;
    if (search.trim()) params.q = search.trim();
    api.officerSuggestions(params, auth)
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [status, category, from, to]);
  return <div>
    <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Knowledge · consumer suggestions</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Consumer Suggestions.</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Ideas submitted by consumers. Review, acknowledge, and track — the original suggestion text is never edited.</p>
    <div className="mt-5 flex flex-col gap-3 lg:flex-row">
      <SearchBox value={search} onChange={setSearch} testId="input-suggestion-search" placeholder="Search title, suggestion ID, description…" />
      <button onClick={load} className="rounded-lg bg-[#18B978] px-4 py-2.5 text-xs font-bold text-white hover:bg-[#119e67]" data-testid="button-suggestion-search">Search</button>
      <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-suggestion-status"><option value="">All statuses</option>{SUGGESTION_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
      <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-suggestion-category"><option value="">All categories</option>{SUGGESTION_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select>
      <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-suggestion-from" />
      <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-suggestion-to" />
    </div>
    {error && <p className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-suggestions-error">{error}</p>}
    <div className="mt-5 overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">
      {loading ? <p className="p-6 text-xs text-[#849188]">Loading suggestions…</p>
        : items.length === 0 ? <div className="p-6 text-center"><p className="text-sm font-bold text-[#30473a]">No data yet</p><p className="mx-auto mt-1 max-w-sm text-xs text-[#849188]">No suggestions match these filters.</p></div>
          : items.map((s) => <div key={s.suggestion_id} className="flex flex-col gap-3 border-b border-[#edf1ee] p-5 last:border-0 md:flex-row md:items-center" data-testid={`suggestion-${String(s.suggestion_id).slice(0, 8)}`}>
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#18a86f]">{String(s.suggestion_id).slice(0, 8)}</span><Pill tone={s.status === 'CLOSED' || s.status === 'ACTIONED' ? 'green' : 'yellow'}>{s.status}</Pill><Pill tone="neutral">{s.category}</Pill></div>
              <p className="mt-2 truncate text-sm font-bold text-[#30473a]">{s.title}</p><p className="mt-1 text-xs text-[#849188]">{s.consumer ? `from ${s.consumer} · ` : ''}submitted {String(s.created_at || '').slice(0, 10)} · updated {String(s.updated_at || '').slice(0, 10)}</p></div>
            <Link href={`/inspector/suggestions/${s.suggestion_id}`} className="inline-flex shrink-0 items-center justify-center gap-1 rounded-lg border border-[#dce7df] px-3 py-2 text-xs font-bold text-[#426050] hover:border-[#18B978] hover:text-[#12885c]" data-testid={`link-suggestion-${String(s.suggestion_id).slice(0, 8)}`}>Open <ChevronRight size={14} /></Link>
          </div>)}
    </div>
  </div>;
}

export function OfficerSuggestionsDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id ?? '';
  const auth = useOfficerAuth();
  const [item, setItem] = useState<Suggestion | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const load = () => {
    setLoading(true);
    setError('');
    api.officerSuggestion(id, auth)
      .then(setItem)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [id]);
  const act = async (toStatus: string) => {
    setSaving(true);
    setError('');
    try {
      const updated = await api.reviewSuggestion(id, toStatus, note, auth);
      setItem(updated);
      setNote('');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading suggestion…</p>;
  if (error && !item) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-suggestion-error">{error}</div>;
  if (!item) return <p className="text-xs text-[#849188]">No data yet.</p>;
  const timeline = item.timeline ?? [];
  return <div>
    <Link href="/inspector/suggestions" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-suggestions"><ArrowLeft size={14} />Back to suggestions</Link>
    <div className="mt-4 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Knowledge · suggestion detail</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{item.title}</h1>
        <p className="mt-2 font-mono text-[11px] text-[#849188]">Suggestion {String(item.suggestion_id).slice(0, 8)} · from {item.consumer || 'a consumer'} · submitted {String(item.created_at || '').slice(0, 10)}</p></div>
      <Pill tone={item.status === 'CLOSED' || item.status === 'ACTIONED' ? 'green' : 'yellow'} testId="suggestion-status">{item.status}</Pill>
    </div>
    {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]">{error}</p>}
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Suggestion</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {[['Category', item.category || '—'], ['Submitted date', String(item.created_at || '').slice(0, 10) || '—'], ['Current status', item.status], ['Last updated', String(item.updated_at || '').slice(0, 10) || '—']].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{String(value)}</p></div>)}
          </div>
          {item.description && <div className="mt-4 rounded-lg bg-[#fbfcfb] p-4"><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">Description (consumer text — never edited)</p><p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-[#586a5f]">{item.description}</p></div>}
          {[['Product / context', item.context], ['Location', item.location]].map(([label, value]) => value ? <p key={label} className="mt-2 text-xs text-[#68766f]"><b>{label}:</b> {String(value)}</p> : null)}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Timeline</h2>
          {timeline.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No timeline events yet.</p>
            : <div className="mt-3 space-y-2">{timeline.map((t, i) => <div key={i} className="flex items-center gap-2 text-xs text-[#586a5f]"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><Check size={11} /></span><span><b>{t.event_type}</b>{t.to_status ? ` → ${t.to_status}` : ''}{t.note ? ` · ${t.note}` : ''}</span><span className="ml-auto font-mono text-[10px] text-[#9aa69f]">{String(t.created_at || '').slice(0, 16)}</span></div>)}</div>}
        </div>
      </div>
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Officer actions</h2>
          <p className="mt-1 text-[11px] text-[#849188]">Only lifecycle transitions the backend supports.</p>
          <label className="mt-4 block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Officer note</span><textarea value={note} onChange={(e) => setNote(e.target.value)} className="focus-ring h-20 w-full resize-none rounded-lg border border-[#dbe6de] px-3 py-2 text-sm outline-none focus:border-[#18B978]" data-testid="input-suggestion-note" /></label>
          <div className="mt-4 grid gap-2">
            {[['UNDER_REVIEW', 'Mark under review'], ['ACKNOWLEDGED', 'Acknowledge'], ['ACTIONED', 'Mark actioned'], ['CLOSED', 'Close']].map(([status, label]) => <button key={status} disabled={saving || item.status === status} onClick={() => act(status)} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050] hover:border-[#18B978] disabled:opacity-50" data-testid={`button-suggestion-${status.toLowerCase()}`}>{label}</button>)}
          </div>
          {item.officer_note && <p className="mt-3 text-xs text-[#68766f]"><b>Latest note:</b> {item.officer_note}</p>}
        </div>
      </div>
    </div>
  </div>;
}

/* --------------------------- Enforcement queue ---------------------------- */

export function EnforcementQueuePage() {
  const auth = useOfficerAuth();
  const [items, setItems] = useState<AnyRow[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const load = () => {
    setLoading(true);
    setError('');
    api.officerQueue(status ? { status } : {}, auth)
      .then((rows) => setItems(rows as AnyRow[]))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [status]);
  const visible = useMemo(() => items.filter((v) => {
    if (!search.trim()) return true;
    const hay = [v.violation_id, v.inspection_id, v.product_id, v.product_name, v.check_id, v.violation_type, v.description]
      .map((x) => String(x ?? '')).join(' ').toLowerCase();
    return hay.includes(search.trim().toLowerCase());
  }), [items, search]);
  return <div>
    <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Inspector · enforcement</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Enforcement Queue.</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Actionable case work from the rule engine — confirm, reject, or request evidence. Decisions persist to the backend.</p>
    <div className="mt-5 flex flex-col gap-3 sm:flex-row">
      <SearchBox value={search} onChange={setSearch} testId="input-enforcement-search" placeholder="Search violation, inspection, product, check…" />
      <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-enforcement-status">
        <option value="">All decisions</option>
        <option value="PENDING">Pending</option>
        <option value="REQUIRES_REVIEW">Requires review</option>
        <option value="CONFIRMED">Confirmed</option>
        <option value="REJECTED">Rejected</option>
      </select>
    </div>
    {error && <p className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-enforcement-error">{error}</p>}
    <div className="mt-5 overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">
      {loading ? <p className="p-6 text-xs text-[#849188]">Loading enforcement queue…</p>
        : visible.length === 0 ? <div className="p-6 text-center"><p className="text-sm font-bold text-[#30473a]">Queue is clear</p><p className="mx-auto mt-1 max-w-sm text-xs text-[#849188]">No cases match. New rule-engine findings appear here.</p></div>
          : visible.map((v) => <div key={v.violation_id} className="flex flex-col gap-3 border-b border-[#edf1ee] p-5 last:border-0 md:flex-row md:items-center" data-testid={`queue-${String(v.violation_id).slice(0, 8)}`}>
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#18a86f]">{String(v.violation_id).slice(0, 8)}</span><Pill tone={v.inspector_status === 'PENDING' || v.inspector_status === 'REQUIRES_REVIEW' ? 'yellow' : v.inspector_status === 'CONFIRMED' ? 'green' : 'neutral'}>{v.inspector_status}</Pill>{v.severity && <Pill tone={v.severity === 'HIGH' ? 'red' : 'neutral'}>{v.severity}</Pill>}</div>
              <p className="mt-2 truncate text-sm font-bold text-[#30473a]">{v.description || v.violation_type}</p><p className="mt-1 text-xs text-[#849188]">{v.product_name || ''}{v.check_id ? ` · ${v.check_id}` : ''}</p></div>
            <Link href={`/inspector/audit/${v.violation_id}`} className="inline-flex shrink-0 items-center justify-center gap-1 rounded-lg border border-[#dce7df] px-3 py-2 text-xs font-bold text-[#426050] hover:border-[#18B978] hover:text-[#12885c]" data-testid={`link-case-${String(v.violation_id).slice(0, 8)}`}>Open case <ChevronRight size={14} /></Link>
          </div>)}
    </div>
  </div>;
}
