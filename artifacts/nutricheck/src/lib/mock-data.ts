export type Status = 'pass' | 'review' | 'fail';
export type Product = {
  id: string; name: string; manufacturer: string; category: string; image: string;
  score: number; status: Status; primaryViolation: string; scannedAt: string;
  declarations: Declaration[]; violations: Violation[]; nutrition: { label: string; value: string }[];
  extractedFields: ExtractedField[];
};
export type Declaration = { key: string; label: string; status: Status; evidence: string; requirement: string; ruleRef: string };
export type Violation = { id: string; issue: string; clause: string; severity: 'High' | 'Medium' | 'Low'; evidence: string; requirement: string; penalty: string; deduction: number };
export type ExtractedField = { key: string; label: string; value: string; confidence: number; editable: boolean };
export type Complaint = { id: string; trackingId: string; productName: string; retailer: string; city: string; submittedAt: string; severity: 'High' | 'Medium' | 'Low'; violations: string[]; status: 'Submitted' | 'Under review' | 'Action taken' | 'Closed'; evidence: string[]; timeline: { label: string; date: string; done: boolean }[] };
export type Rule = { id: string; title: string; body: string; clause: string; reference: string; description: string; deduction: number; effectiveDate: string; status: 'Active' | 'Draft' | 'Inactive' };

export const products: Product[] = [
  {
    id: 'prod-atta-01', name: 'Aashirvaad Select Atta', manufacturer: 'ITC Limited', category: 'Staples',
    image: 'atta', score: 82, status: 'review', primaryViolation: 'Net quantity statement is hard to locate',
    scannedAt: 'Today, 10:42 AM',
    declarations: [
      { key: 'name', label: 'Name & address of manufacturer', status: 'pass', evidence: 'ITC Limited, Virginia House, Kolkata', requirement: 'Name and complete address must be declared', ruleRef: 'LMPC Rule 6(1)(a)' },
      { key: 'quantity', label: 'Net quantity', status: 'review', evidence: '“5 kg” found on front panel', requirement: 'Net quantity must use standard unit and be prominent', ruleRef: 'LMPC Rule 6(1)(e)' },
      { key: 'mfd', label: 'Month & year of manufacture', status: 'pass', evidence: 'MFD: 05/2024', requirement: 'Month and year of manufacture or packing', ruleRef: 'FSSAI Reg. 2.2.2' },
      { key: 'fssai', label: 'FSSAI licence number', status: 'pass', evidence: '10012011000123', requirement: '14-digit FSSAI licence number', ruleRef: 'FSSAI Reg. 2.2.1' },
    ],
    violations: [
      { id: 'v-qty', issue: 'Net quantity is not in the principal display panel', clause: 'Rule 6(1)(e)', severity: 'Medium', evidence: 'Quantity appears on the side seam, 4 cm below the brand lockup.', requirement: 'Net quantity must be displayed on the principal display panel.', penalty: '₹5,000–₹25,000', deduction: 10 },
      { id: 'v-veg', issue: 'Vegetarian symbol has low contrast', clause: 'FSSAI Reg. 2.2.2(4)', severity: 'Low', evidence: 'Green symbol measures approximately 2 mm and blends with the background.', requirement: 'Vegetarian declaration must be clear and legible.', penalty: 'Advisory', deduction: 8 },
    ],
    nutrition: [{ label: 'Energy', value: '341 kcal' }, { label: 'Protein', value: '12.1 g' }, { label: 'Carbohydrate', value: '72.3 g' }, { label: 'Total fat', value: '1.7 g' }],
    extractedFields: [
      { key: 'productName', label: 'Product name', value: 'Aashirvaad Select Atta', confidence: 0.98, editable: true },
      { key: 'manufacturer', label: 'Manufacturer', value: 'ITC Limited', confidence: 0.96, editable: true },
      { key: 'netQuantity', label: 'Net quantity', value: '5 kg', confidence: 0.91, editable: true },
      { key: 'batch', label: 'Batch / lot number', value: 'AS240514B', confidence: 0.78, editable: true },
      { key: 'fssai', label: 'FSSAI licence number', value: '10012011000123', confidence: 0.99, editable: true },
      { key: 'mfgDate', label: 'Manufacturing date', value: '05/2024', confidence: 0.94, editable: true },
    ],
  },
  {
    id: 'prod-oats-02', name: 'Saffola Masala Oats', manufacturer: 'Marico Limited', category: 'Breakfast',
    image: 'oats', score: 94, status: 'pass', primaryViolation: '', scannedAt: 'Yesterday, 4:16 PM',
    declarations: [
      { key: 'name', label: 'Name & address of manufacturer', status: 'pass', evidence: 'Marico Limited, Mumbai', requirement: 'Name and complete address must be declared', ruleRef: 'LMPC Rule 6(1)(a)' },
      { key: 'quantity', label: 'Net quantity', status: 'pass', evidence: '40 g', requirement: 'Net quantity must use standard unit and be prominent', ruleRef: 'LMPC Rule 6(1)(e)' },
      { key: 'mfd', label: 'Month & year of manufacture', status: 'pass', evidence: 'MFD: 06/2024', requirement: 'Month and year of manufacture or packing', ruleRef: 'FSSAI Reg. 2.2.2' },
      { key: 'fssai', label: 'FSSAI licence number', status: 'pass', evidence: '10012022000317', requirement: '14-digit FSSAI licence number', ruleRef: 'FSSAI Reg. 2.2.1' },
    ],
    violations: [], nutrition: [{ label: 'Energy', value: '384 kcal' }, { label: 'Protein', value: '10.2 g' }, { label: 'Total fat', value: '7.1 g' }, { label: 'Sodium', value: '920 mg' }],
    extractedFields: [{ key: 'productName', label: 'Product name', value: 'Saffola Masala Oats', confidence: .99, editable: true }, { key: 'manufacturer', label: 'Manufacturer', value: 'Marico Limited', confidence: .98, editable: true }, { key: 'netQuantity', label: 'Net quantity', value: '40 g', confidence: .99, editable: true }, { key: 'batch', label: 'Batch / lot number', value: 'MO2406A', confidence: .86, editable: true }],
  },
];

export const initialComplaints: Complaint[] = [
  { id: 'comp-01', trackingId: 'LA-24-0618-482', productName: 'Aashirvaad Select Atta', retailer: 'Reliance Smart, Koramangala', city: 'Bengaluru', submittedAt: '18 Jun 2024', severity: 'Medium', violations: ['Net quantity is not in the principal display panel'], status: 'Under review', evidence: ['label-front.jpg', 'label-side.jpg'], timeline: [{ label: 'Complaint submitted', date: '18 Jun, 10:48 AM', done: true }, { label: 'Acknowledged by department', date: '18 Jun, 3:12 PM', done: true }, { label: 'Inspection scheduled', date: 'Expected by 22 Jun', done: false }] },
];

export const initialRules: Rule[] = [
  { id: 'rule-01', title: 'Net quantity on principal display panel', body: 'Every package shall bear a declaration of the net quantity of the commodity in the package.', clause: 'Rule 6(1)(e)', reference: 'Legal Metrology (Packaged Commodities) Rules, 2011', description: 'Checks placement, standard units, and minimum type size of net quantity.', deduction: 10, effectiveDate: '01 Apr 2011', status: 'Active' },
  { id: 'rule-02', title: 'FSSAI licence number', body: 'The FSSAI logo and licence number shall be displayed on the label of a pre-packaged food.', clause: 'Reg. 2.2.1', reference: 'Food Safety and Standards (Packaging and Labelling) Regulations', description: 'Validates the presence and 14-digit format of the food licence.', deduction: 15, effectiveDate: '01 Jul 2022', status: 'Active' },
  { id: 'rule-03', title: 'Vegetarian / non-vegetarian symbol', body: 'The symbol shall be displayed in close proximity to the name or brand name of the food.', clause: 'Reg. 2.2.2(4)', reference: 'FSSAI Labelling and Display Regulations, 2020', description: 'Checks contrast, size, and placement of dietary symbols.', deduction: 8, effectiveDate: '17 Nov 2020', status: 'Draft' },
];

export function getStored<T>(key: string, fallback: T): T {
  try { const stored = localStorage.getItem(`nutricheck:${key}`); return stored ? JSON.parse(stored) as T : fallback; } catch { return fallback; }
}
export function setStored<T>(key: string, value: T) { localStorage.setItem(`nutricheck:${key}`, JSON.stringify(value)); }