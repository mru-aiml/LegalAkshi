/**
 * Consumer product verification — the consumer side of the two-sided system.
 *
 * Officer flow (unchanged, /inspector/scan): package photos -> OCR ->
 * review -> rule engine -> officer decision -> persisted inspection.
 * Consumer flow (here): barcode / QR / product-code / name search ->
 * persisted LegalAkshi inspection -> verification status. NO OCR here.
 */
import { useEffect, useState } from 'react';
import { Link, useParams, useSearch } from 'wouter';
import {
  ArrowLeft, ArrowRight, BadgeCheck, CheckCircle2, CircleAlert, Download, FlaskConical, Info,
  Leaf, QrCode, ScanLine, ShieldCheck,
} from 'lucide-react';
import {
  api,
  type ConsumerReportDetail,
  type DeclarationRow,
  type Nutrient,
  type Suggestion,
  type VerificationDetail,
  type VerificationMatch,
  type VerificationStatus,
} from '@/lib/api';
import { DemoBanner, DemoBadge } from '@/components/consumer-bits';
import {
  DEMO_COMPLAINTS,
  DEMO_PRODUCTS,
  demoProductById,
  isDemoMode,
  setDemoMode,
  type DemoProduct,
} from '@/lib/demo-data';

/** Complaint categories (stored as a description prefix — the complaint
 *  backend has no category column and the schema is frozen). */
export const COMPLAINT_CATEGORIES = [
  'Incorrect MRP',
  'Missing/incorrect declaration',
  'Damaged package',
  'Suspected counterfeit',
  'Expired product',
  'Product information mismatch',
  'Other',
] as const;

export function formatComplaintDescription(category: string, body: string): string {
  return `[Category: ${category}] ${(body || '').trim()}`;
}

export function parseComplaintCategory(description: string): { category: string | null; body: string } {
  const m = /^\s*\[Category:\s*([^\]]+)\]\s*/.exec(description || '');
  if (!m) return { category: null, body: (description || '').trim() };
  return { category: m[1].trim(), body: (description || '').slice(m[0].length).trim() };
}

/** Device-local lookup history (A10). Consumer lookups are not persisted
 *  server-side, so history lives on this device only — real entries or
 *  the "No recent product checks." empty state, never fabricated. */
const HISTORY_KEY = 'legalakshi:recent-checks';
export type RecentCheck = { product_id: string; product_name?: string | null; status?: string; at: string };
export function recordLookup(match: VerificationMatch): void {
  try {
    const prev: RecentCheck[] = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    const next = [{ product_id: match.product_id, product_name: match.product_name ?? null, status: match.status, at: new Date().toISOString() },
      ...prev.filter((r) => r.product_id !== match.product_id)].slice(0, 8);
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  } catch { /* private mode: history simply unavailable */ }
}
export function readLookups(): RecentCheck[] {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

export function StatusPill({ status }: { status: VerificationStatus | string }) {
  const tone = status === 'VERIFIED'
    ? 'bg-[#e3f7ed] text-[#08784e]'
    : status === 'NEEDS_REVIEW'
      ? 'bg-[#fff4cf] text-[#946b09]'
      : 'bg-[#edf1ef] text-[#53625b]';
  const label = status === 'VERIFIED' ? 'Verified' : status === 'NEEDS_REVIEW' ? 'Needs review' : 'Not verified';
  const Icon = status === 'VERIFIED' ? BadgeCheck : status === 'NEEDS_REVIEW' ? CircleAlert : Info;
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${tone}`} data-testid={`verify-status-${String(status).toLowerCase()}`}><Icon size={13} />{label}</span>;
}

function MatchCard({ match, demo }: { match: VerificationMatch; demo?: boolean }) {
  return <div className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft" data-testid={`verify-match-${match.product_id}`}>
    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><StatusPill status={match.status} />{demo && <DemoBadge />}</div>
        <h2 className="mt-3 font-bold text-[#20382b]">{match.product_name || 'Unnamed product'}</h2>
        <p className="mt-1 text-xs text-[#7f8e85]">{[match.brand, match.manufacturer].filter(Boolean).join(' · ')}{match.pack_size ? ` · ${match.pack_size}` : ''}</p>
        <p className="mt-1 font-mono text-[10px] text-[#9aa69f]">{match.barcode ? `barcode ${match.barcode} · ` : ''}inspected {(match.inspection_date || '').slice(0, 10) || '—'}</p>
        <p className="mt-2 text-xs leading-relaxed text-[#68766f]">{match.reason}</p>
        {match.status === 'NOT_VERIFIED' && <p className="mt-2 rounded-lg bg-[#f4f7f5] p-3 text-[11px] leading-relaxed text-[#607069]">Not verified does not mean illegal or unsafe — it means LegalAkshi has no completed inspection for this product yet.</p>}
      </div>
      <Link href={`/verify-product/${match.product_id}`} className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#119e67]" data-testid={`link-verification-${match.product_id}`}>View verification <ArrowRight size={15} /></Link>
    </div>
  </div>;
}

function demoMatches(term: string): VerificationMatch[] {
  const needle = term.trim().toLowerCase();
  return DEMO_PRODUCTS.filter((p) =>
    !needle || p.product_name.toLowerCase().includes(needle)
    || p.brand.toLowerCase().includes(needle)
    || p.barcode.replace(/\D/g, '').includes(needle.replace(/\D/g, '')))
    .map((p) => ({
      product_id: p.product_id, product_name: p.product_name,
      brand: p.brand, manufacturer: p.manufacturer, pack_size: p.pack_size,
      barcode: p.barcode, status: p.status, reason: p.reason,
      inspection_date: p.inspection_date, inspection_type: p.inspection_type,
    }));
}

export function VerifyProductPage() {
  const [query, setQuery] = useState('');
  const [searched, setSearched] = useState('');
  const [matches, setMatches] = useState<VerificationMatch[] | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [demo, setDemo] = useState(() => isDemoMode());
  const enableDemo = () => { setDemoMode(true); setDemo(true); setError(''); setMatches(null); };
  const disableDemo = () => { setDemoMode(false); setDemo(false); setMatches(null); };
  const run = async (q?: string) => {
    const term = (q ?? query).trim();
    if (!term) return;
    setWorking(true);
    setError('');
    setSearched(term);
    try {
      if (isDemoMode()) {
        // Explicit demo mode: fictional sample records, clearly labelled.
        setMatches(demoMatches(term));
        return;
      }
      // Barcode / QR / product-code exact forms hit the code path;
      // anything else is a product-name search. No OCR is performed.
      const isCode = /^[0-9A-Za-z\- ]{4,}$/.test(term) && /[0-9]/.test(term);
      const res = isCode
        ? await api.consumerLookup({ barcode: term, product_name: term })
        : await api.consumerLookup({ product_name: term });
      // Code path may miss (e.g. spaces/dashes); fall back to name search.
      const finalMatches = res.matches.length === 0 && isCode
        ? (await api.consumerLookup({ product_name: term })).matches
        : res.matches;
      setMatches(finalMatches);
      finalMatches.forEach(recordLookup);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setMatches([]);
    } finally {
      setWorking(false);
    }
  };
  /** Barcode/QR scan uses the platform's native detector only — no new
   *  dependency. Where it is unavailable, manual lookup stays the path
   *  (never a fragile custom scanner). */
  const scanCode = async () => {
    setError('');
    const Detector = (window as unknown as { BarcodeDetector?: new (opts?: object) => { detect(s: ImageBitmapSource): Promise<{ rawValue: string }[]> } }).BarcodeDetector;
    if (!Detector) {
      setError('Barcode scanning is not available on this device — type the barcode, QR content, or product name instead.');
      return;
    }
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.setAttribute('capture', 'environment');
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const bitmap = await createImageBitmap(file);
        const found = await new Detector({ formats: ['qr_code', 'ean_13', 'ean_8', 'code_128', 'upc_a', 'upc_e'] }).detect(bitmap);
        const value = found?.[0]?.rawValue?.trim();
        if (value) {
          setQuery(value);
          await run(value);
        } else {
          setError('No barcode or QR code was found in that photo — type the code instead.');
        }
      } catch {
        setError('Barcode scanning failed for that photo — type the code instead.');
      }
    };
    input.click();
  };
  return <div>
    <div className="mb-8">
      <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Consumer · product verification</p>
      <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Verify a product.</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Scan the barcode or QR on the pack — or type the product name or code. We check it against persisted LegalAkshi officer inspections. No photos, no label scanning.</p>
    </div>
    <div className="mx-auto max-w-3xl">
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-5 shadow-soft md:p-6">
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Barcode, QR code, product code, or product name</span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1"><QrCode size={16} className="absolute left-3 top-3.5 text-[#97a49d]" /><input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run(); }} placeholder="Scan barcode or search product name..." className="focus-ring w-full rounded-lg border border-[#dce7df] py-3 pl-9 pr-3 text-sm outline-none" data-testid="input-verify-search" /></div>
            <button onClick={() => run()} disabled={working || !query.trim()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#18B978] px-5 py-3 text-sm font-bold text-white hover:bg-[#119e67] disabled:opacity-50" data-testid="button-verify-search"><ScanLine size={16} />{working ? 'Checking…' : 'Verify'}</button>
            <button onClick={scanCode} disabled={working} className="inline-flex items-center justify-center gap-2 rounded-lg border border-[#dce7df] px-5 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978] disabled:opacity-50" data-testid="button-scan-code"><QrCode size={16} />Scan barcode / QR</button>
          </div>
        </label>
        <p className="mt-3 text-[11px] leading-relaxed text-[#849188]">A product is shown as verified only when a persisted officer inspection backs it — never from a scan alone.</p>
      </div>
      {demo && <div className="mt-4"><DemoBanner text="Demo mode is on: results below are fictional samples, not verified inspections." /></div>}
      {error && <div className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs leading-relaxed text-[#8D3834]" data-testid="text-verify-error"><b>LegalAkshi verification is temporarily unavailable.</b> Please try again.{error ? <span className="mt-1 block font-mono text-[10px] opacity-80">{error}</span> : null}{!demo && <span className="mt-2 block"><button onClick={enableDemo} className="rounded-lg bg-white px-3 py-2 text-xs font-bold text-[#b43b37]" data-testid="button-try-demo">Try demo data instead</button></span>}</div>}
      {matches !== null && <div className="mt-5 space-y-4">
        {matches.length === 0
          ? <div className="rounded-xl border border-dashed border-[#cbdad0] bg-white px-6 py-14 text-center" data-testid="verify-no-match"><span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-[#eaf8f1] text-[#18B978]"><Info size={22} /></span><h2 className="mt-4 font-bold text-[#30473a]">No verified LegalAkshi inspection was found{searched ? ` for “${searched}”` : ''}.</h2><p className="mx-auto mt-2 max-w-sm text-sm text-[#849188]">This does not mean the product is illegal or unsafe. You can report a problem and our officers will take it from there.</p><Link href={`/complaint/new?product=${encodeURIComponent(searched)}`} className="mt-5 inline-flex items-center gap-2 rounded-lg border border-[#dce7df] px-4 py-2.5 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="button-report-from-verify-empty">Report a problem</Link></div>
          : matches.map((m) => <MatchCard key={m.product_id} match={m} demo={demo || m.product_id.startsWith('demo-')} />)}
      </div>}
      <div className="mt-6 flex justify-center">
        {demo
          ? <button onClick={disableDemo} className="text-xs font-bold text-[#12885c] hover:underline" data-testid="button-exit-demo">Exit demo mode — back to real data</button>
          : <button onClick={enableDemo} className="inline-flex items-center gap-2 rounded-lg border border-[#dce7df] px-4 py-2.5 text-xs font-bold text-[#426050] hover:border-[#18B978]" data-testid="button-load-demo"><FlaskConical size={15} />Try demo data</button>}
      </div>
      <div className="mt-6 rounded-xl border border-[#e4ece6] bg-white p-5">
        <h2 className="text-sm font-bold text-[#20382b]">Something wrong with a product?</h2>
        <p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">Wrong MRP, missing declaration, damaged pack, suspected counterfeit, expired, or information mismatch — file it in a minute.</p>
        <Link href="/complaint/new" className="mt-3 inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#119e67]" data-testid="button-report-problem"><CircleAlert size={16} />Report a problem</Link>
      </div>
    </div>
  </div>;
}

function DeclarationRowView({ row }: { row: DeclarationRow }) {
  const ok = row.state === 'Verified';
  const warn = row.state === 'Needs review';
  return <div className="flex items-center justify-between gap-3 border-b border-[#f0f3f0] py-3 last:border-0" data-testid={`declaration-${row.label.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`}>
    <span className="text-sm font-semibold text-[#30473a]">{row.label}</span>
    {ok
      ? <span className="inline-flex items-center gap-1 rounded-full bg-[#e3f7ed] px-2.5 py-1 text-[11px] font-bold text-[#08784e]"><CheckCircle2 size={13} />✓ Verified</span>
      : warn
        ? <span className="inline-flex items-center gap-1 rounded-full bg-[#fff4cf] px-2.5 py-1 text-[11px] font-bold text-[#946b09]">⚠ Needs review</span>
        : <span className="inline-flex items-center gap-1 rounded-full bg-[#edf1ef] px-2.5 py-1 text-[11px] font-bold text-[#53625b]">— Not verified</span>}
  </div>;
}

function DemoVerificationView({ demo }: { demo: DemoProduct }) {
  return <div>
    <Link href="/verify-product" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-verify"><ArrowLeft size={14} />Back to verification</Link>
    <div className="mt-4"><DemoBanner text="Demo record: fictional sample, not a verified LegalAkshi inspection." /></div>
    <div className="mt-4 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Product verification</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{demo.product_name}</h1>
        <p className="mt-2 text-sm text-[#68766f]">{[demo.brand, demo.manufacturer].filter(Boolean).join(' · ')}{demo.pack_size ? ` · ${demo.pack_size}` : ''}</p></div>
      <span className="flex flex-wrap items-center gap-2"><StatusPill status={demo.status} /><DemoBadge /></span>
    </div>
    <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[#68766f]">{demo.reason}</p>
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Inspection</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {[['Inspection date', demo.inspection_date || '—'], ['Inspection type', demo.inspection_type], ['Manufacturer', demo.manufacturer], ['Pack size', demo.pack_size], ['Batch / lot', demo.batch_lot]].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{value}</p></div>)}
        </div>
        <h2 className="mt-7 font-bold text-[#20382b]">Declarations checked</h2>
        <div className="mt-3">{demo.declarations.map((d) => <DeclarationRowView key={d.label} row={d} />)}</div>
      </div>
      <div className="space-y-5">
        <div className="rounded-2xl border border-[#e2eae4] bg-white p-6">
          <h2 className="font-bold text-[#20382b]">Next steps</h2>
          <div className="mt-4 grid gap-2">
            <Link href={`/nutrition/${demo.product_id}`} className="flex items-center gap-3 rounded-lg border border-[#dce7df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-nutrition"><Leaf size={17} />Nutritional information</Link>
          </div>
        </div>
      </div>
    </div>
  </div>;
}

export function VerificationDetailsPage() {
  const params = useParams<{ productId: string }>();
  const id = params.productId ?? '';
  const demo = id.startsWith('demo-') ? demoProductById(id) : null;
  const [detail, setDetail] = useState<VerificationDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!demo);
  useEffect(() => {
    if (demo) return;
    setLoading(true);
    api.consumerVerification(id)
      .then(setDetail)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [id, demo]);
  if (demo) return <DemoVerificationView demo={demo} />;
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading verification…</p>;
  if (error || !detail) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-verification-error">{error || 'Verification not found.'}</div>;
  return <div>
    <Link href="/verify-product" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-verify"><ArrowLeft size={14} />Back to verification</Link>
    <div className="mt-4 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div><p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Product verification</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{detail.product_name || 'Unnamed product'}</h1>
        <p className="mt-2 text-sm text-[#68766f]">{[detail.brand, detail.manufacturer].filter(Boolean).join(' · ')}{detail.pack_size ? ` · ${detail.pack_size}` : ''}</p></div>
      <StatusPill status={detail.status} />
    </div>
    <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[#68766f]">{detail.reason}</p>
    {detail.status === 'NOT_VERIFIED' && <p className="mt-3 max-w-2xl rounded-xl bg-[#f4f7f5] p-4 text-xs leading-relaxed text-[#607069]" data-testid="text-not-verified-note">{detail.not_verified_note || 'NOT VERIFIED does not mean illegal or unsafe.'}</p>}
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Inspection</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {[['Inspection date', (detail.inspection_date || '').slice(0, 10) || '—'], ['Inspection type', detail.inspection_type || '—'], ['Manufacturer', detail.manufacturer || '—'], ['Pack size', detail.pack_size || '—'], ['Batch / lot', detail.batch_lot || '—']].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{value}</p></div>)}
        </div>
        <h2 className="mt-7 font-bold text-[#20382b]">Declarations checked</h2>
        <p className="mt-1 text-[11px] text-[#849188]">Only checks backed by persisted inspection evidence. “Not verified” means no evidence — never an assumed pass.</p>
        <div className="mt-3">{detail.declarations.map((d) => <DeclarationRowView key={d.label} row={d} />)}</div>
      </div>
      <div className="space-y-5">
        {detail.review?.reviewed ? <div className="rounded-2xl border border-[#d9efe4] bg-[#eaf8f1] p-6" data-testid="panel-inspector-review"><div className="flex items-center gap-2"><ShieldCheck size={18} className="text-[#12885c]" /><h2 className="font-bold text-[#20382b]">Inspector verification</h2></div><p className="mt-3 text-xs leading-relaxed text-[#4c7761]">{detail.review.note}</p></div>
          : <div className="rounded-2xl border border-[#e2eae4] bg-white p-6"><div className="flex items-center gap-2"><ShieldCheck size={18} className="text-[#97a49d]" /><h2 className="font-bold text-[#20382b]">Inspector verification</h2></div><p className="mt-3 text-xs leading-relaxed text-[#7d8b83]">No separate officer decision is recorded for this product — the checks above stand on the completed inspection.</p></div>}
        <div className="rounded-2xl border border-[#e2eae4] bg-white p-6">
          <h2 className="font-bold text-[#20382b]">Next steps</h2>
          <div className="mt-4 grid gap-2">
            <Link href={`/nutrition/${detail.product_id}`} className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-nutrition"><Leaf size={17} />Nutritional information</Link>
            <Link href={`/complaint/new?product=${encodeURIComponent(detail.product_name || '')}`} className="flex items-center gap-3 rounded-lg bg-[#18B978] px-4 py-3 text-sm font-bold text-white hover:bg-[#119e67]" data-testid="button-report-problem-detail"><CircleAlert size={17} />Report a problem</Link>
          </div>
        </div>
      </div>
    </div>
  </div>;
}

function DemoNutritionView({ demo }: { demo: DemoProduct }) {
  return <div>
    <Link href="/nutrition" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-nutrition"><ArrowLeft size={14} />Back to nutrition</Link>
    <div className="mt-4"><DemoBanner text="Demo record: fictional sample nutrition, not verified product data." /></div>
    <p className="mb-2 mt-4 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Consumer · nutrition</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Nutritional information</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">{demo.product_name}</p>
    {demo.nutrients.length === 0
      ? <div className="mx-auto mt-6 max-w-xl rounded-xl border border-dashed border-[#cbdad0] bg-white px-6 py-14 text-center" data-testid="nutrition-unavailable"><h2 className="mt-4 font-bold text-[#30473a]">Not available yet</h2><p className="mx-auto mt-2 max-w-sm text-sm text-[#849188]">Nutrition information is not available for this product yet.</p></div>
      : <div className="mx-auto mt-6 max-w-2xl space-y-3">{demo.nutrients.map((n) => <div key={n.name} className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft" data-testid={`nutrient-${n.name.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`}><div className="flex items-baseline justify-between gap-3"><p className="text-sm font-bold text-[#20382b]">{n.name}</p><p className="font-mono text-sm font-bold text-[#12885c]">{n.value}</p></div><p className="mt-2 text-xs leading-relaxed text-[#68766f]"><b className="text-[#3d5145]">Understand this: </b>{n.about}</p></div>)}</div>}
  </div>;
}

export function NutritionPage() {
  const params = useParams<{ productId: string }>();
  const id = params.productId ?? '';
  const demo = id.startsWith('demo-') ? demoProductById(id) : null;
  const [data, setData] = useState<{ product_name?: string | null; available: boolean; nutrients: Nutrient[]; note?: string | null } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!demo);
  useEffect(() => {
    if (demo) return;
    setLoading(true);
    api.consumerNutrition(id)
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [id, demo]);
  if (demo) return <DemoNutritionView demo={demo} />;
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading nutrition…</p>;
  if (error) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-nutrition-error">{error}</div>;
  return <div>
    <Link href={`/verify-product/${id}`} className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-verification"><ArrowLeft size={14} />Back to verification</Link>
    <p className="mb-2 mt-4 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Consumer · nutrition</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Nutritional information</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">{data?.product_name || 'This product'}{data?.available ? ' — values below come from the verified inspection, exactly as declared.' : ''}</p>
    {!data?.available
      ? <div className="mx-auto mt-6 max-w-xl rounded-xl border border-dashed border-[#cbdad0] bg-white px-6 py-14 text-center" data-testid="nutrition-unavailable"><span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-[#eaf8f1] text-[#18B978]"><Leaf size={22} /></span><h2 className="mt-4 font-bold text-[#30473a]">Not available yet</h2><p className="mx-auto mt-2 max-w-sm text-sm text-[#849188]">{data?.note || 'Nutrition information has not been verified for this product yet.'}</p></div>
      : <div className="mx-auto mt-6 max-w-2xl space-y-3">{data.nutrients.map((n) => <div key={n.name} className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft" data-testid={`nutrient-${n.name.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`}><div className="flex items-baseline justify-between gap-3"><p className="text-sm font-bold text-[#20382b]">{n.name}</p><p className="font-mono text-sm font-bold text-[#12885c]">{n.value}</p></div>{n.about && <p className="mt-2 text-xs leading-relaxed text-[#68766f]"><b className="text-[#3d5145]">What does this mean? </b>{n.about}</p>}</div>)}</div>}
  </div>;
}

export function useComplaintPrefill(): { product: string } {
  const search = useSearch();
  try {
    return { product: new URLSearchParams(search).get('product') || '' };
  } catch {
    return { product: '' };
  }
}

export function NutritionLandingPage() {
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<VerificationMatch[] | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const run = async () => {
    const term = query.trim();
    if (!term) return;
    setWorking(true);
    setError('');
    try {
      const res = await api.consumerLookup({ product_name: term, barcode: term });
      setMatches(res.matches.length > 0 ? res.matches : (await api.consumerLookup({ product_name: term })).matches);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setMatches([]);
    } finally {
      setWorking(false);
    }
  };
  return <div>
    <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Consumer · nutrition</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Nutrition.</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Nutrition information from verified package data. Search a verified product below, then open its nutrition details. Values appear only where the inspection actually recorded them.</p>
    <div className="mx-auto mt-6 max-w-3xl">
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-5 shadow-soft md:p-6">
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Product name or code</span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run(); }} placeholder="Search product name..." className="focus-ring w-full flex-1 rounded-lg border border-[#dce7df] px-3.5 py-3 text-sm outline-none" data-testid="input-nutrition-search" />
            <button onClick={run} disabled={working || !query.trim()} className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#18B978] px-5 py-3 text-sm font-bold text-white hover:bg-[#119e67] disabled:opacity-50" data-testid="button-nutrition-search"><Leaf size={16} />{working ? 'Searching…' : 'Search'}</button>
          </div>
        </label>
      </div>
      {error && <div className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs leading-relaxed text-[#8D3834]" data-testid="text-nutrition-error"><b>LegalAkshi verification is temporarily unavailable.</b> Please try again.</div>}
      {matches !== null && <div className="mt-5 space-y-4">
        {matches.length === 0
          ? <p className="rounded-xl border border-dashed border-[#cbdad0] bg-white p-6 text-center text-sm text-[#849188]" data-testid="nutrition-no-match">No products found for that search.</p>
          : matches.map((m) => <div key={m.product_id} className="flex flex-col justify-between gap-3 rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft sm:flex-row sm:items-center" data-testid={`nutrition-match-${m.product_id}`}>
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><StatusPill status={m.status} /></div>
              <p className="mt-2 truncate text-sm font-bold text-[#30473a]">{m.product_name || 'Unnamed product'}</p></div>
            <Link href={`/nutrition/${m.product_id}`} className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-[#dce7df] px-4 py-2.5 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid={`link-nutrition-${m.product_id}`}><Leaf size={15} />View nutrition</Link>
          </div>)}
      </div>}
    </div>
  </div>;
}

const SUGGESTION_CATEGORIES = [
  'Product authenticity',
  'Packaging verification',
  'Food safety',
  'Label transparency',
  'Consumer awareness',
  'Digital verification',
  'Other',
] as const;

export function SuggestionsPage() {
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState<string>(SUGGESTION_CATEGORIES[0]);
  const [description, setDescription] = useState('');
  const [context, setContext] = useState('');
  const [location, setLocation] = useState('');
  const [items, setItems] = useState<Suggestion[]>([]);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<Suggestion | null>(null);
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    api.listSuggestions()
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  const submit = async () => {
    if (!title.trim()) {
      setError('Please add a short title for your suggestion.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const created = await api.createSuggestion({ title: title.trim(), category, description: description.trim(), context: context.trim(), location: location.trim() });
      setSubmitted(created);
      setTitle('');
      setDescription('');
      setContext('');
      setLocation('');
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };
  const tone = (s: string) => s === 'CLOSED' || s === 'ACTIONED' || s === 'ACKNOWLEDGED' ? 'green' : 'yellow';
  return <div>
    <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Consumer · suggestions</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Suggestions.</h1>
    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Ideas to improve packaged-food authenticity, verification, awareness, and reporting. Your suggestion has been submitted to LegalAkshi for review.</p>
    <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_1fr]">
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Share a suggestion</h2>
        {submitted && <div className="mt-4 rounded-xl border border-[#cfeedd] bg-[#eaf8f1] p-4 text-xs leading-relaxed text-[#276047]" data-testid="text-suggestion-submitted"><b>Suggestion submitted successfully.</b><p className="mt-1 font-mono">ID: {submitted.suggestion_id} · {String(submitted.created_at).slice(0, 10)} · {submitted.status}</p></div>}
        {error && <p className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-suggestion-error">{error}</p>}
        <div className="mt-4 space-y-4">
          <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Suggestion title</span><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Tamper-proof QR stickers on packs" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none" data-testid="input-suggestion-title" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Category</span><select value={category} onChange={(e) => setCategory(e.target.value)} className="focus-ring w-full rounded-lg border border-[#dbe6de] bg-white px-3.5 py-3 text-sm outline-none" data-testid="input-suggestion-category">{SUGGESTION_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
          <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Description</span><textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={4} placeholder="What should improve, and why?" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none" data-testid="input-suggestion-description" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Product / context (optional)</span><input value={context} onChange={(e) => setContext(e.target.value)} placeholder="e.g. Milk packets" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none" data-testid="input-suggestion-context" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Location / state (optional)</span><input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. Karnataka" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none" data-testid="input-suggestion-location" /></label>
          <button onClick={submit} disabled={submitting} className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#119e67] disabled:opacity-50" data-testid="button-submit-suggestion">{submitting ? 'Submitting…' : 'Submit suggestion'}</button>
        </div>
      </div>
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">My suggestions</h2>
        {loading ? <p className="mt-3 text-xs text-[#849188]">Loading your suggestions…</p>
          : items.length === 0 ? <p className="mt-3 text-xs text-[#849188]">No suggestions yet — yours will appear here with their status.</p>
            : <div className="mt-3 space-y-3">{items.map((s) => <div key={s.suggestion_id} className="rounded-xl border border-[#edf1ee] p-4" data-testid={`suggestion-${String(s.suggestion_id).slice(0, 8)}`}>
              <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-bold text-[#30473a]">{s.title}</p><span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-bold ${tone(s.status) === 'green' ? 'bg-[#e3f7ed] text-[#08784e]' : 'bg-[#fff4cf] text-[#946b09]'}`}>{s.status}</span></div>
              <p className="mt-1 text-[11px] text-[#849188]">{s.category} · {String(s.created_at).slice(0, 10)}</p>
              {s.description && <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-[#586a5f]">{s.description}</p>}
            </div>)}</div>}
      </div>
    </div>
  </div>;
}

export function ReportDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id ?? '';
  const [report, setReport] = useState<ConsumerReportDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    setLoading(true);
    api.consumerReport(id)
      .then(setReport)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [id]);
  const downloadPdf = async (inspectionId: string) => {
    setDownloading(true);
    try {
      const blob = await api.reportPdf(inspectionId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `legalakshi-report-${inspectionId.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading report…</p>;
  if (error || !report) return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs text-[#8D3834]" data-testid="text-report-error">{error || 'Report not found.'}</div>;
  const related = report.related_inspection;
  const timeline = report.timeline ?? [];
  const parsedCategory = parseComplaintCategory(report.description || '').category;
  return <div>
    <Link href="/reports" className="inline-flex items-center gap-1 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-back-reports"><ArrowLeft size={14} />Back to my reports</Link>
    <p className="mb-2 mt-4 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Report details</p>
    <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{report.product_name || 'Unnamed product'}</h1>
    <p className="mt-2 font-mono text-[11px] text-[#849188]">Report {String(report.complaint_id).slice(0, 8)}</p>
    {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]">{error}</p>}
    <div className="mt-6 grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Complaint submitted</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {[['Product', report.product_name || '—'], ['Category', parsedCategory || '—'], ['Current status', report.status], ['Submitted date', String(report.date_submitted || '').slice(0, 10) || '—']].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{String(value)}</p></div>)}
          </div>
          {report.description && <p className="mt-4 whitespace-pre-wrap text-xs leading-relaxed text-[#586a5f]">{report.description}</p>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Related inspection</h2>
          {!related
            ? <p className="mt-2 rounded-lg bg-[#f4f7f5] p-4 text-xs leading-relaxed text-[#607069]" data-testid="text-report-pending">Your report is still under review.</p>
            : <div className="mt-3">
              <div className="grid gap-4 sm:grid-cols-2">
                {[['Inspection status', related.status.replaceAll('_', ' ')], ['Inspection date', String(related.inspection_date || '').slice(0, 10) || '—'], ['Decision state', `${related.outcome.fail} issue${related.outcome.fail === 1 ? '' : 's'} confirmed · ${related.outcome.review} awaiting review`]].map(([label, value]) => <div key={label}><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{String(value)}</p></div>)}
              </div>
              {report.declarations.length > 0 && <div className="mt-4">{report.declarations.map((d) => <DeclarationRowView key={d.label} row={{ label: d.label, state: d.state as DeclarationRow['state'] }} />)}</div>}
            </div>}
        </div>
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Status timeline</h2>
          {timeline.length === 0 ? <p className="mt-2 text-xs text-[#849188]">No timeline events yet.</p>
            : <div className="mt-3 space-y-2">{timeline.map((t, i) => <div key={i} className="flex items-center gap-2 text-xs text-[#586a5f]"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><CheckCircle2 size={11} /></span><span><b>{t.event_type}</b>{t.to_status ? ` → ${t.to_status}` : ''}{t.note ? ` · ${t.note}` : ''}</span><span className="ml-auto font-mono text-[10px] text-[#9aa69f]">{String(t.created_at || '').slice(0, 16)}</span></div>)}</div>}
        </div>
      </div>
      <div className="space-y-5">
        <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft">
          <h2 className="font-bold text-[#20382b]">Final report</h2>
          {related?.report_available
            ? <><p className="mt-2 text-xs leading-relaxed text-[#7d8b83]">The official inspection report is available for download.</p><button onClick={() => downloadPdf(related.inspection_id)} disabled={downloading} className="mt-4 inline-flex items-center gap-2 rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050] disabled:opacity-50" data-testid="button-download-final-report"><Download size={16} />{downloading ? 'Preparing…' : 'Download report'}</button></>
            : <p className="mt-2 text-xs leading-relaxed text-[#7d8b83]">No final report yet — download appears here once the inspection is analysed.</p>}
        </div>
      </div>
    </div>
  </div>;
}
