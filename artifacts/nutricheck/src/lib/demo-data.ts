/**
 * Clearly-labelled DEMO/SAMPLE data for UI demonstration.
 *
 * Frontend-only: demo mode never writes to Neon, never affects
 * inspections, complaints, enforcement, or rule evaluation. Demo
 * records are fictional examples and MUST always render beside an
 * explicit "Demo data" label — never mixed unlabeled with real data
 * and never presented as verified government/product data.
 */
import type { DeclarationRow } from '@/lib/api';

const DEMO_KEY = 'legalakshi:demo-mode';

export function isDemoMode(): boolean {
  try {
    return localStorage.getItem(DEMO_KEY) === 'on';
  } catch {
    return false;
  }
}

export function setDemoMode(on: boolean): void {
  try {
    localStorage.setItem(DEMO_KEY, on ? 'on' : 'off');
  } catch { /* private mode: demo simply unavailable */ }
}

export type DemoProduct = {
  product_id: string;
  product_name: string;
  brand: string;
  manufacturer: string;
  pack_size: string;
  barcode: string;
  batch_lot: string;
  status: 'VERIFIED' | 'NEEDS_REVIEW' | 'NOT_VERIFIED';
  reason: string;
  inspection_date: string;
  inspection_type: string;
  declarations: DeclarationRow[];
  review?: { reviewed: boolean; action: string; reviewed_by: string; reviewed_on: string | null; note: string } | null;
  nutrients: { name: string; value: string; about: string }[];
};

const VERIFIED_DECLS: DeclarationRow[] = [
  { label: 'Product identity', state: 'Verified' },
  { label: 'Net quantity', state: 'Verified' },
  { label: 'MRP', state: 'Verified' },
  { label: 'Manufacturer', state: 'Verified' },
  { label: 'FSSAI licence', state: 'Verified' },
  { label: 'Date declaration', state: 'Verified' },
  { label: 'Consumer-care details', state: 'Verified' },
  { label: 'Ingredients', state: 'Verified' },
];

export const DEMO_PRODUCTS: DemoProduct[] = [
  {
    product_id: 'demo-verified-atta',
    product_name: 'Demo Whole Wheat Atta (Sample)',
    brand: 'Demo Foods',
    manufacturer: 'Demo Foods Pvt. Ltd.',
    pack_size: '5 kg',
    barcode: '8900000000011',
    batch_lot: 'DEMO-B001',
    status: 'VERIFIED',
    reason: 'Demo record: illustrates a completed inspection. Not a real verification.',
    inspection_date: '2026-08-02',
    inspection_type: 'PHYSICAL',
    declarations: VERIFIED_DECLS,
    review: { reviewed: true, action: 'reviewed', reviewed_by: 'Demo Officer', reviewed_on: '2026-08-03', note: 'Demo review note.' },
    nutrients: [
      { name: 'Serving Size', value: '100 g', about: 'The reference amount the numbers below describe.' },
      { name: 'Energy', value: '340 kcal', about: 'Calories the product provides; higher means more energy per serving.' },
      { name: 'Protein', value: '12 g', about: 'Builds and repairs body tissue; a higher value means more protein per serving.' },
      { name: 'Carbohydrate', value: '72 g', about: "The body's main energy source; includes sugars and starch." },
      { name: 'Total Sugars', value: '2 g', about: 'All sugars in the product, natural plus added.' },
      { name: 'Total Fat', value: '2 g', about: 'All fat in the product per serving.' },
      { name: 'Saturated Fat', value: '0.4 g', about: 'Fat linked to heart health when eaten in excess; compare across brands.' },
      { name: 'Sodium', value: '5 mg', about: 'A mineral in salt; high intake is linked to blood pressure concerns.' },
    ],
  },
  {
    product_id: 'demo-review-oil',
    product_name: 'Demo Mustard Oil (Sample)',
    brand: 'Demo Foods',
    manufacturer: 'Demo Oils Ltd.',
    pack_size: '1 L',
    barcode: '8900000000028',
    batch_lot: 'DEMO-B002',
    status: 'NEEDS_REVIEW',
    reason: 'Demo record: illustrates an inspection awaiting officer review.',
    inspection_date: '2026-09-10',
    inspection_type: 'PHYSICAL',
    declarations: [
      { label: 'Product identity', state: 'Verified' },
      { label: 'Net quantity', state: 'Verified' },
      { label: 'MRP', state: 'Needs review' },
      { label: 'Manufacturer', state: 'Verified' },
      { label: 'FSSAI licence', state: 'Verified' },
      { label: 'Date declaration', state: 'Verified' },
      { label: 'Consumer-care details', state: 'Not verified' },
      { label: 'Ingredients', state: 'Verified' },
    ],
    review: null,
    nutrients: [],
  },
  {
    product_id: 'demo-unverified-chips',
    product_name: 'Demo Banana Chips (Sample)',
    brand: 'Demo Snacks',
    manufacturer: 'Demo Snacks Co.',
    pack_size: '150 g',
    barcode: '8900000000035',
    batch_lot: '—',
    status: 'NOT_VERIFIED',
    reason: 'Demo record: illustrates a product with no inspection on file.',
    inspection_date: '',
    inspection_type: 'PHYSICAL',
    declarations: VERIFIED_DECLS.map((d) => ({ ...d, state: 'Not verified' as const })),
    review: null,
    nutrients: [],
  },
];

export type DemoComplaint = {
  complaint_id: string;
  product_name: string;
  retailer: string;
  city: string;
  severity: string;
  category: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
  timeline: { event_type: string; from_status?: string | null; to_status?: string | null; note?: string; created_at?: string }[];
};

export const DEMO_COMPLAINTS: DemoComplaint[] = [
  {
    complaint_id: 'demo-c-submitted',
    product_name: 'Demo Banana Chips (Sample)',
    retailer: 'Demo Mart', city: 'Bengaluru', severity: 'Medium',
    category: 'Missing or incorrect declaration',
    description: 'Demo complaint: MRP not printed clearly on the pack.',
    status: 'SUBMITTED', created_at: '2026-09-12T10:00:00',
    updated_at: '2026-09-12T10:00:00',
    timeline: [{ event_type: 'CREATED', to_status: 'SUBMITTED', created_at: '2026-09-12T10:00:00' }],
  },
  {
    complaint_id: 'demo-c-review',
    product_name: 'Demo Mustard Oil (Sample)',
    retailer: 'Demo Store', city: 'Pune', severity: 'High',
    category: 'Suspected counterfeit',
    description: 'Demo complaint: seal looked tampered.',
    status: 'UNDER_REVIEW', created_at: '2026-09-08T10:00:00',
    updated_at: '2026-09-09T10:00:00',
    timeline: [
      { event_type: 'CREATED', to_status: 'SUBMITTED', created_at: '2026-09-08T10:00:00' },
      { event_type: 'STATUS_CHANGE', from_status: 'SUBMITTED', to_status: 'ACKNOWLEDGED', created_at: '2026-09-08T15:00:00' },
      { event_type: 'STATUS_CHANGE', from_status: 'ACKNOWLEDGED', to_status: 'UNDER_REVIEW', created_at: '2026-09-09T10:00:00' },
    ],
  },
  {
    complaint_id: 'demo-c-action',
    product_name: 'Demo Whole Wheat Atta (Sample)',
    retailer: 'Demo Bazaar', city: 'Delhi', severity: 'Medium',
    category: 'Incorrect MRP',
    description: 'Demo complaint: shelf price differed from pack MRP.',
    status: 'ACTION_TAKEN', created_at: '2026-08-20T10:00:00',
    updated_at: '2026-08-25T10:00:00',
    timeline: [
      { event_type: 'CREATED', to_status: 'SUBMITTED', created_at: '2026-08-20T10:00:00' },
      { event_type: 'STATUS_CHANGE', from_status: 'SUBMITTED', to_status: 'ACKNOWLEDGED', created_at: '2026-08-21T10:00:00' },
      { event_type: 'STATUS_CHANGE', from_status: 'ACKNOWLEDGED', to_status: 'UNDER_REVIEW', created_at: '2026-08-22T10:00:00' },
      { event_type: 'STATUS_CHANGE', from_status: 'UNDER_REVIEW', to_status: 'ACTION_TAKEN', note: 'Retailer counselled.', created_at: '2026-08-25T10:00:00' },
    ],
  },
];

export function demoProductById(id: string): DemoProduct | null {
  return DEMO_PRODUCTS.find((p) => p.product_id === id) ?? null;
}
