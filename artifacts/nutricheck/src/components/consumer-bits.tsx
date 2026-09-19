/**
 * Small consumer-portal pieces: awareness ticker + demo labelling.
 *
 * The awareness strip is static content (no backend needed) so it
 * renders immediately even when verification data is unavailable.
 * Demo badges mark fictional sample records that must never be read
 * as verified government/product data.
 */
import { FlaskConical } from 'lucide-react';

const MESSAGES = [
  'Check the MRP before purchase',
  'Check manufacturing and best-before dates',
  'Verify the FSSAI licence details',
  'Read ingredients and allergen information',
  'Check the vegetarian / non-vegetarian symbol',
  'Report suspicious packaged products',
  'Verify products before you buy',
];

export function AwarenessStrip() {
  // Content duplicated once for a seamless -50% loop; a single region
  // label keeps it accessible without announcing every repetition.
  const items = [...MESSAGES, ...MESSAGES];
  return <div role="region" aria-label="Consumer awareness" data-testid="awareness-strip"
    className="overflow-hidden border-b border-[#e3eae5] bg-[#eaf8f1] py-2">
    <div className="awareness-track px-4" aria-hidden={false}>
      {items.map((m, i) => <span key={i} aria-hidden={i >= MESSAGES.length}
        className="text-[11px] font-semibold text-[#4c7761]">• {m}</span>)}
    </div>
  </div>;
}

export function DemoBadge({ testId }: { testId?: string }) {
  return <span
    className="inline-flex items-center gap-1 rounded-full bg-[#fff4d4] px-2.5 py-1 text-[11px] font-bold text-[#946b09]"
    data-testid={testId ?? 'demo-badge'}>
    <FlaskConical size={12} />Demo data
  </span>;
}

export function DemoBanner({ text }: { text?: string }) {
  return <div className="rounded-xl border border-[#f1cf71] bg-[#fff8e1] p-4 text-xs leading-relaxed text-[#7d652c]"
    data-testid="demo-banner">
    <DemoBadge /> <span className="ml-1">{text ?? 'Sample records for demonstration only — not verified LegalAkshi data.'}</span>
  </div>;
}
