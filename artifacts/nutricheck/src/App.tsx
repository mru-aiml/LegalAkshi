import { type ReactNode, useEffect, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  ClerkProvider,
  Show,
  SignIn,
  SignUp,
  useAuth,
  useClerk,
  useUser,
} from '@clerk/react';
import { publishableKeyFromHost } from '@clerk/react/internal';
import { shadcn } from '@clerk/themes';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Link, Redirect, Route, Switch, Router as WouterRouter, useLocation, useParams } from 'wouter';
import {
  ArrowLeft, ArrowRight, BadgeCheck, BarChart3, Bell, BookOpen, Box, Check, CheckCircle2,
  ChevronRight, CircleAlert, ClipboardCheck, Download, FileCheck2, FileText, Filter, HelpCircle,
  History, Home, Info, Leaf, LifeBuoy, Lightbulb, FlaskConical, ListChecks, LockKeyhole, LogOut, Menu,
  Pencil, Plus, QrCode, ScanLine, Search, Settings, ShieldCheck, SlidersHorizontal,
  Sparkles, Store, Trash2, UploadCloud, UserRound, Users, X, Zap,
} from 'lucide-react';
import {
  type Complaint, type Product, type Rule, type Status, getStored, initialComplaints,
  initialRules, products, setStored,
} from '@/lib/mock-data';
import { LabCheckPage } from '@/pages/lab-check';
import { OfficerScanPage } from '@/pages/officer-scan';
import { AwarenessStrip, DemoBadge, DemoBanner } from '@/components/consumer-bits';
import { isDemoMode, setDemoMode, DEMO_COMPLAINTS, DEMO_PRODUCTS } from '@/lib/demo-data';
import {
  COMPLAINT_CATEGORIES,
  NutritionLandingPage,
  NutritionPage,
  ReportDetailPage,
  SuggestionsPage,
  VerificationDetailsPage,
  VerifyProductPage,
  formatComplaintDescription,
  parseComplaintCategory,
  readLookups,
  useComplaintPrefill,
  type RecentCheck,
} from '@/pages/consumer-verify';
import {
  ComplaintDetailPage,
  EnforcementQueuePage,
  InspectionDetailPage,
  InspectionsPage,
  OfficerSuggestionsDetailPage,
  OfficerSuggestionsPage,
  parseExtraLines,
} from '@/pages/inspector-workspace';
import { PhotoSlot, clearScanPhotos, currentScanPhotos, setScanPhoto, type ScanPhoto } from '@/components/scan';
import { api, setAuthTokenProvider, type BackendNotification, type Complaint as BackendComplaint, type BackendRule, type ConsumerOverview, type ConsumerReport, type OfficerStats } from '@/lib/api';
import {
  canAccess,
  isDevRoleSwitchEnabled,
  setDevRole,
  useWorkspaceRole,
  type WorkspaceRole,
} from '@/lib/auth-role';

const clerkPubKey = publishableKeyFromHost(
  window.location.hostname,
  import.meta.env.VITE_CLERK_PUBLISHABLE_KEY,
);
const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');

function stripBase(path: string) {
  return basePath && path.startsWith(basePath)
    ? path.slice(basePath.length) || '/'
    : path;
}
const getGreeting = (): string => {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
};
if (!clerkPubKey) {
  throw new Error('Missing VITE_CLERK_PUBLISHABLE_KEY in .env file');
}

const clerkAppearance = {
  theme: shadcn,
  cssLayerName: 'clerk',
  options: {
    logoPlacement: 'inside' as const,
    logoLinkUrl: basePath || '/',
    logoImageUrl: `${window.location.origin}${basePath}/logo.svg`,
  },
  variables: {
    colorPrimary: '#18B978',
    colorForeground: '#173A2A',
    colorMutedForeground: '#718078',
    colorDanger: '#C24743',
    colorBackground: '#FFFFFF',
    colorInput: '#FBFCFB',
    colorInputForeground: '#20382B',
    colorNeutral: '#DCE7DF',
    fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    borderRadius: '0.75rem',
  },
  elements: {
    rootBox: 'w-full flex justify-center',
    cardBox: 'bg-white rounded-2xl w-[440px] max-w-full overflow-hidden',
    card: '!shadow-none !border-0 !bg-transparent !rounded-none',
    footer: '!shadow-none !border-0 !bg-transparent !rounded-none',
    headerTitle: 'text-[#173A2A] font-extrabold tracking-[-.04em]',
    headerSubtitle: 'text-[#718078]',
    socialButtonsBlockButtonText: 'text-[#30473A] font-semibold',
    formFieldLabel: 'text-[#42554A] font-bold',
    footerActionLink: 'text-[#12885C] font-bold',
    footerActionText: 'text-[#718078]',
    dividerText: 'text-[#718078]',
    identityPreviewEditButton: 'text-[#12885C]',
    formFieldSuccessText: 'text-[#12885C]',
    alertText: 'text-[#8D3834]',
    logoBox: 'mb-3',
    logoImage: 'max-h-10',
    socialButtonsBlockButton: 'border-[#DCE7DF] bg-white hover:bg-[#F3F8F5]',
    formButtonPrimary: 'bg-[#18B978] hover:bg-[#119E67] text-white shadow-[0_5px_12px_rgba(24,185,120,.18)]',
    formFieldInput: 'border-[#DCE7DF] bg-[#FBFCFB] text-[#20382B]',
    footerAction: 'bg-transparent',
    dividerLine: 'bg-[#E6EEE8]',
    alert: 'bg-[#FCE6E4] border-[#F0C9C6]',
    otpCodeFieldInput: 'border-[#DCE7DF] bg-[#FBFCFB]',
    formFieldRow: 'text-[#42554A]',
    main: 'bg-transparent',
  },
};

const queryClient = new QueryClient();
function Logo({ compact = false }: { compact?: boolean }) {
  return <Link href="/" className={`flex items-center gap-2.5 ${compact ? '' : 'w-fit'}`} data-testid="link-logo">
    <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#18B978] text-white shadow-[0_5px_12px_rgba(24,185,120,.22)]"><ShieldCheck size={19} strokeWidth={2.5} /></span>
    {!compact && <span className="text-[17px] font-extrabold tracking-[-.04em] text-[#17191C]">Legal<span className="text-[#18B978]">Akshi</span></span>}
  </Link>;
}

function Button({ children, variant = 'primary', className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger' }) {
  const styles = {
    primary: 'bg-[#18B978] text-white hover:bg-[#119e67] shadow-[0_5px_12px_rgba(24,185,120,.18)]',
    secondary: 'border border-[#dce5df] bg-white text-[#173a2a] hover:border-[#18B978] hover:text-[#12885c]',
    ghost: 'text-[#607069] hover:bg-[#eef5f0] hover:text-[#173a2a]',
    danger: 'bg-[#d9534f] text-white hover:bg-[#bd4541]',
  };
  return <button className={`focus-ring inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]} ${className}`} {...props}>{children}</button>;
}

function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'green' | 'yellow' | 'red' | 'neutral' }) {
  const tones = { green: 'bg-[#e3f7ed] text-[#08784e]', yellow: 'bg-[#fff4cf] text-[#946b09]', red: 'bg-[#fce6e4] text-[#b43b37]', neutral: 'bg-[#edf1ef] text-[#53625b]' };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${tones[tone]}`}>{children}</span>;
}

function StatusBadge({ status }: { status: Status }) {
  return <Badge tone={status === 'pass' ? 'green' : status === 'review' ? 'yellow' : 'red'}>{status === 'pass' ? <Check size={12} /> : status === 'review' ? <Info size={12} /> : <CircleAlert size={12} />}{status === 'pass' ? 'Compliant' : status === 'review' ? 'Needs review' : 'Violation'}</Badge>;
}

function AppShell({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user } = useUser();
  const { signOut } = useClerk();
  // Workspace role comes from the Clerk session (publicMetadata.role).
  // There is intentionally NO consumer/officer switch button: navigation is
  // role-driven and the backend enforces authorization on every call.
  const { role, source } = useWorkspaceRole();
  // Consumer = verify persisted products, understand nutrition, raise
  // complaints. No label scanning, no Lab check (internal/demo workflow,
  // route still mounted for internal use but unlinked here).
  const consumerNav = [{ href: '/dashboard', label: 'Home', icon: Home }, { href: '/verify-product', label: 'Verify a product', icon: ScanLine }, { href: '/nutrition', label: 'Nutrition', icon: Leaf }, { href: '/complaints', label: 'My complaints', icon: ClipboardCheck }, { href: '/reports', label: 'My reports', icon: FileText }, { href: '/suggestions', label: 'Suggestions', icon: Lightbulb }];
  // Inspector: Inspect -> Review -> Analyze -> Investigate -> Decide ->
  // Report. Existing pages (Scan & Inspect, Rule Library, Reports,
  // complaint queue, audit cases) are reused and reorganized — no
  // duplicates, all routes bookmark-compatible.
  type NavItem = { section?: string; href?: string; label?: string; icon?: typeof Home };
  const typedConsumerNav: NavItem[] = consumerNav;
  const officerNav: NavItem[] = [
    { section: 'Workspace' },
    { href: '/inspector/dashboard', label: 'Overview', icon: Home },
    { href: '/inspector/inspections', label: 'Inspections', icon: Box },
    { href: '/inspector/complaints', label: 'Complaints', icon: ClipboardCheck },
    { href: '/inspector/scan', label: 'Scan & Inspect', icon: ScanLine },
    { href: '/inspector/enforcement', label: 'Enforcement Queue', icon: ShieldCheck },
    { section: 'Knowledge' },
    { href: '/inspector/rules', label: 'Rule Library', icon: BookOpen },
    { href: '/inspector/suggestions', label: 'Consumer Suggestions', icon: Lightbulb },
    { section: 'Output' },
    { href: '/inspector/reports', label: 'Reports', icon: FileText },
  ];
  const nav: NavItem[] = role === 'consumer' ? typedConsumerNav : officerNav;
  const roleTitle = role === 'consumer' ? 'Consumer workspace' : role === 'admin' ? 'Admin workspace' : 'Officer workspace';
  const roleBlurb = role === 'consumer' ? 'Understand what is on your shelf.' : 'Review, verify, and act on reports.';
  const isActive = (href: string) => location === href || (href !== '/dashboard' && location.startsWith(href));
  const displayName = user?.firstName || user?.fullName || (role === 'consumer' ? 'Consumer' : role === 'admin' ? 'Admin' : 'Officer');
  const initials = `${user?.firstName?.[0] || displayName[0] || 'N'}${user?.lastName?.[0] || ''}`.toUpperCase();
  // Fixed/stable officer sidebar: the shell is viewport-height with no
  // document scroll. The sidebar is viewport-height + sticky on desktop
  // (drawer on mobile) with its own internal nav scroll; the main column
  // is the independent scroll container (sticky header inside it). This
  // keeps Profile/Settings/Sign out reachable without scrolling a long
  // analysis page to the bottom. Routing, Clerk and responsive behaviour
  // are unchanged — one sidebar only.
  return <div className="grain flex h-[100dvh] overflow-hidden bg-[#F7F8F6]" data-testid="app-shell">
    <aside data-testid="officer-sidebar" className={`fixed inset-y-0 left-0 z-40 flex h-[100dvh] w-[252px] shrink-0 flex-col border-r border-[#e1e9e3] bg-[#fbfcfa] px-4 py-5 transition-transform duration-300 md:sticky md:top-0 md:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
      <div className="mb-9 flex shrink-0 items-center justify-between px-2"><Logo /><button className="text-[#75847b] md:hidden" onClick={() => setMobileOpen(false)} data-testid="button-close-menu"><X size={20} /></button></div>
      <div className="mb-5 shrink-0 rounded-xl border border-[#d9efe4] bg-[#eaf8f1] p-3.5">
        <div className="mb-1 flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[.14em] text-[#18855b]">Workspace</span><Zap size={14} className="text-[#18B978]" /></div>
        <div className="text-sm font-bold text-[#18392a]">{roleTitle}{source === 'dev-override' ? ' · DEV' : ''}</div>
        <div className="mt-1 text-xs leading-relaxed text-[#60786b]">{roleBlurb}</div>
      </div>
      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto" data-testid="workspace-nav">
        {role === 'consumer' && <p className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[.16em] text-[#93a19a]">Navigate</p>}
        {nav.map((item: NavItem, i: number) => item.section
          ? <p key={`sec-${i}`} className="mb-2 mt-4 px-3 text-[10px] font-bold uppercase tracking-[.16em] text-[#93a19a] first:mt-0">{item.section}</p>
          : <Link key={item.href} href={item.href as string} onClick={() => setMobileOpen(false)} className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors ${isActive(item.href as string) ? 'bg-[#dff5e9] text-[#08784e]' : 'text-[#64736b] hover:bg-[#eef4ef] hover:text-[#243b2f]'}`} data-testid={`link-nav-${(item.label as string).toLowerCase().replaceAll(' ', '-')}`}>{item.icon && <item.icon size={17} strokeWidth={isActive(item.href as string) ? 2.5 : 2} />}<span>{item.label}</span></Link>)}
      </nav>
      <div className="mt-auto shrink-0 space-y-1">
        <Link href={role === 'consumer' ? '/profile' : '/inspector/profile'} className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold text-[#64736b] hover:bg-[#eef4ef]" data-testid="link-profile"><UserRound size={17} />{role === 'consumer' ? 'My profile' : 'Officer profile'}</Link>
        <Link href="/settings" className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold text-[#64736b] hover:bg-[#eef4ef]" data-testid="link-settings"><Settings size={17} />Settings</Link>
        <div className="my-3 h-px bg-[#e6ece7]" />
        {isDevRoleSwitchEnabled() && <DevRoleSwitch current={role} />}
         <button onClick={() => signOut({ redirectUrl: basePath || '/' })} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-semibold text-[#64736b] hover:bg-[#eef4ef]" data-testid="button-logout"><LogOut size={17} />Sign out</button>
      </div>
    </aside>
    {mobileOpen && <button aria-label="Close navigation" className="fixed inset-0 z-30 bg-[#173a2a]/20 md:hidden" onClick={() => setMobileOpen(false)} data-testid="button-overlay-menu" />}
    <main data-testid="main-content" className="h-[100dvh] min-w-0 flex-1 overflow-y-auto">
      {role === 'consumer' && <AwarenessStrip />}
      <header className="sticky top-0 z-20 flex h-[70px] items-center justify-between border-b border-[#e3eae5] bg-[#f7f8f6]/90 px-5 backdrop-blur-md md:px-10">
        <div className="flex items-center gap-3"><button className="rounded-lg p-2 text-[#52665b] hover:bg-white md:hidden" onClick={() => setMobileOpen(true)} data-testid="button-open-menu"><Menu size={20} /></button><div className="text-xs font-medium text-[#87958d]">{roleTitle} <span className="mx-1.5 text-[#cad2cd]">/</span><span className="text-[#3d5146]">{location === '/dashboard' || location === '/inspector/dashboard' ? 'Overview' : 'LegalAkshi'}</span></div></div>
         <div className="flex items-center gap-4"><NotificationsBell /><div className="hidden h-6 w-px bg-[#dfe7e1] sm:block" /><div className="flex items-center gap-2"><span className="grid h-8 w-8 place-items-center rounded-full bg-[#d9f2e5] text-xs font-bold text-[#12885c]">{initials}</span><span className="hidden text-sm font-semibold text-[#283d32] sm:block">{displayName}</span></div></div>
      </header>
      <div className="mx-auto max-w-[1380px] px-5 py-7 md:px-10 md:py-9">{children}</div>
    </main>
  </div>;
}

function notifEnabled(): boolean {
  try {
    return localStorage.getItem('legalakshi:notif-enabled') !== 'off';
  } catch {
    return true;
  }
}

function PageTitle({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div>{eyebrow && <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">{eyebrow}</p>}<h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">{title}</h1>{description && <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">{description}</p>}</div>{action}</div>;
}

/** Live notification bell: inbox from GET /api/v1/notifications (real events
 * only). Unread dot, per-item read, mark-all-read, expandable full list. */
function NotificationsBell() {
  const { role } = useWorkspaceRole();
  const { user } = useUser();
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<BackendNotification[]>([]);
  const auth = api.devHeadersEnabled ? { devRole: role, devUser: user?.id } : undefined;
  const load = () => {
    if (!notifEnabled()) return;
    api.notifications(auth).then(setItems).catch(() => setItems([]));
  };
  useEffect(() => {
    load();
    if (!notifEnabled()) return;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);
  const unread = items.filter(n => !n.read).length;
  const visible = expanded ? items : items.slice(0, 8);
  const markOne = (id: string) => {
    api.markNotificationRead(id, auth)
      .then(() => setItems(old => old.map(n => n.notification_id === id ? { ...n, read: true } : n)))
      .catch(() => undefined);
  };
  const markAll = () => {
    api.markAllNotificationsRead(auth)
      .then(() => setItems(old => old.map(n => ({ ...n, read: true }))))
      .catch(() => undefined);
  };
  return <div className="relative">
    <button className="relative rounded-lg p-2 text-[#607069] hover:bg-white" onClick={() => { setOpen(o => !o); if (!open) load(); }} data-testid="button-notifications" aria-label="Notifications"><Bell size={18} />{unread > 0 && <span className="absolute right-1.5 top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-[#f1ba55] px-0.5 text-[9px] font-bold text-white">{unread > 9 ? '9+' : unread}</span>}</button>
    {open && <div className="absolute right-0 z-50 mt-2 w-[340px] overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-lift" data-testid="panel-notifications">
      <div className="flex items-center justify-between border-b border-[#edf1ee] px-4 py-3"><p className="text-sm font-bold text-[#20382b]">Notifications</p><button onClick={markAll} className="text-[11px] font-bold text-[#12885c] hover:underline" data-testid="button-notif-read-all">Mark all as read</button></div>
      <div className="max-h-[380px] overflow-y-auto">{visible.length === 0 ? <p className="px-4 py-8 text-center text-xs text-[#849188]">No notifications yet — new violations, complaint updates and rule syncs will appear here.</p> : visible.map(n => <button key={n.notification_id} onClick={() => markOne(n.notification_id)} className={`flex w-full items-start gap-3 border-b border-[#f0f3f0] px-4 py-3 text-left last:border-0 hover:bg-[#f4f8f5] ${n.read ? 'opacity-70' : ''}`} data-testid={`notif-${n.notification_id}`}><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${n.read ? 'bg-[#dfe7e1]' : 'bg-[#18B978]'}`} /><div className="min-w-0"><p className="text-xs font-bold text-[#30473a]">{n.title}</p><p className="mt-0.5 truncate text-[11px] text-[#849188]">{n.body}</p><p className="mt-1 font-mono text-[10px] text-[#9aa69f]">{n.type.replaceAll('_', ' ')} · {(n.created_at || '').slice(0, 16)}</p></div></button>)}</div>
      {items.length > 8 && <button onClick={() => setExpanded(e => !e)} className="w-full border-t border-[#edf1ee] py-2.5 text-xs font-bold text-[#12885c] hover:bg-[#f4f8f5]" data-testid="button-notif-view-all">{expanded ? 'Show less' : `View all (${items.length})`}</button>}
    </div>}
  </div>;
}

/** Development-only role override. Rendered solely when VITE_DEMO_ROLE_SWITCH === 'true'. */
function DevRoleSwitch({ current }: { current: WorkspaceRole }) {
  const [, setLocation] = useLocation();
  const pick = (next: WorkspaceRole | null) => {
    setDevRole(next);
    setLocation(next === 'officer' || next === 'admin' ? '/inspector/dashboard' : '/dashboard');
  };
  return <div className="rounded-lg border border-dashed border-[#f1ba55] bg-[#fffaf0] p-2.5" data-testid="dev-role-switch">
    <p className="mb-2 px-1 text-[10px] font-bold uppercase tracking-[.14em] text-[#946b09]">DEV role override</p>
    <div className="flex gap-1">{(['consumer', 'officer', 'admin'] as WorkspaceRole[]).map(r => <button key={r} onClick={() => pick(r)} className={`flex-1 rounded-md px-1 py-1.5 text-[11px] font-bold ${current === r ? 'bg-[#18B978] text-white' : 'bg-white text-[#607069] hover:bg-[#eef5f0]'}`} data-testid={`dev-role-${r}`}>{r}</button>)}</div>
  </div>;
}

function ForbiddenPage({ area }: { area: string }) {
  const [, setLocation] = useLocation();
  const { role } = useWorkspaceRole();
  return <div className="mx-auto max-w-md py-16 text-center">
    <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-[#fce6e4] text-[#b43b37]"><LockKeyhole size={28} /></span>
    <p className="mt-6 font-mono text-xs text-[#b43b37]">403 / {area.toUpperCase()} ONLY</p>
    <h1 className="mt-3 text-2xl font-extrabold tracking-[-.04em] text-[#173a2a]">Not authorized for this workspace.</h1>
    <p className="mt-3 text-sm leading-relaxed text-[#718078]">You are signed in with the <b>{role}</b> role. This area requires <b>{area}</b> access, which is granted by your administrator — not something you can switch to yourself.</p>
    <Button className="mt-6" variant="secondary" onClick={() => setLocation(role === 'consumer' ? '/dashboard' : '/inspector/dashboard')} data-testid="button-forbidden-home">Back to my workspace</Button>
  </div>;
}

/** Route guard: renders children only for roles allowed in the area, else 403. */
function Guard({ area, children }: { area: 'consumer' | 'officer' | 'admin'; children: ReactNode }) {
  const { role } = useWorkspaceRole();
  if (!canAccess(role, area)) return <ForbiddenPage area={area} />;
  return <>{children}</>;
}

function ProductVisual({ kind, large = false }: { kind: string; large?: boolean }) {
  return <div className={`relative overflow-hidden rounded-xl ${large ? 'h-64' : 'h-28'} ${kind === 'oats' ? 'bg-[#f2d471]' : 'bg-[#e9c99b]'}`}>
    <div className="absolute inset-0 opacity-20" style={{ background: 'radial-gradient(circle at 20% 25%, white 0 3%, transparent 4%), radial-gradient(circle at 80% 70%, white 0 2%, transparent 3%)', backgroundSize: '28px 28px' }} />
    <div className={`absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-sm bg-[#fbfaf2] text-center shadow-md ${large ? 'h-44 w-32' : 'h-20 w-16'}`}><Leaf className="mb-1 text-[#1a9b68]" size={large ? 25 : 14} /><span className={`${large ? 'text-sm' : 'text-[7px]'} font-extrabold leading-none text-[#3d493d]`}>{kind === 'oats' ? 'SAFFOLA' : 'AASHIRVAAD'}</span><span className={`${large ? 'text-[9px]' : 'text-[5px]'} mt-1 text-[#6b786f]`}>{kind === 'oats' ? 'MASALA OATS' : 'SELECT ATTA'}</span><div className="mt-2 h-1 w-8 rounded-full bg-[#18B978]" /></div>
  </div>;
}

function escapeReportValue(value: string) {
  return value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character] ?? character);
}

function downloadComplianceReport(product: Product) {
  const declarations = product.declarations.map(item => `
    <tr>
      <td>${escapeReportValue(item.label)}</td>
      <td><strong>${item.status === 'pass' ? 'Compliant' : item.status === 'review' ? 'Needs review' : 'Violation'}</strong></td>
      <td>${escapeReportValue(item.evidence)}</td>
      <td>${escapeReportValue(item.ruleRef)}</td>
    </tr>`).join('');
  const violations = product.violations.length === 0
    ? '<p class="muted">No issues found in the checks performed.</p>'
    : `<ul>${product.violations.map(item => `<li><strong>${escapeReportValue(item.issue)}</strong><br /><span>${escapeReportValue(item.evidence)}</span><br /><small>${escapeReportValue(item.clause)} · ${escapeReportValue(item.requirement)}</small></li>`).join('')}</ul>`;
  const nutrition = product.nutrition.map(item => `<div class="nutrition"><span>${escapeReportValue(item.label)}</span><strong>${escapeReportValue(item.value)}</strong><small>per 100 g</small></div>`).join('');
  const generatedAt = new Date().toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>LegalAkshi report · ${escapeReportValue(product.name)}</title>
  <style>
    :root { color-scheme: light; font-family: Inter, Arial, sans-serif; color: #20382b; background: #f7f8f6; }
    body { margin: 0; padding: 40px 24px; }
    main { max-width: 900px; margin: auto; background: #fff; padding: 42px; border: 1px solid #dfe9e2; border-radius: 18px; }
    header { display: flex; justify-content: space-between; gap: 24px; border-bottom: 2px solid #eaf1eb; padding-bottom: 22px; }
    h1, h2, p { margin-top: 0; } h1 { margin-bottom: 8px; font-size: 30px; letter-spacing: -.04em; } h2 { margin-top: 30px; font-size: 17px; }
    .brand { color: #18b978; font-weight: 800; font-size: 20px; } .meta, .muted, small { color: #718078; }
    .score { min-width: 120px; text-align: right; color: #a27812; font-size: 34px; font-weight: 800; } .score small { display: block; font-size: 12px; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { text-align: left; vertical-align: top; padding: 12px 10px; border-bottom: 1px solid #e8eee9; } th { color: #718078; font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
    li { margin: 0 0 14px; line-height: 1.5; } li span { color: #586a5f; } li small { display: inline-block; margin-top: 4px; }
    .nutrition-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; } .nutrition { border: 1px solid #e2eae4; border-radius: 10px; padding: 14px; } .nutrition span, .nutrition small { display: block; color: #718078; font-size: 12px; } .nutrition strong { display: block; margin: 8px 0 4px; font-size: 20px; }
    footer { margin-top: 34px; padding-top: 16px; border-top: 1px solid #e8eee9; color: #87958c; font-size: 11px; }
    @media (max-width: 650px) { body { padding: 12px; } main { padding: 22px; } header { display: block; } .score { margin-top: 18px; text-align: left; } .nutrition-grid { grid-template-columns: repeat(2, 1fr); } table { display: block; overflow-x: auto; white-space: nowrap; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div><div class="brand">LegalAkshi</div><p class="meta">Compliance report · ${escapeReportValue(product.category)}</p><h1>${escapeReportValue(product.name)}</h1><p class="meta">${escapeReportValue(product.manufacturer)} · Checked ${escapeReportValue(product.scannedAt)}</p></div>
      <div class="score">${product.score}<small>out of 100</small></div>
    </header>
    <h2>Summary</h2>
    <p>${product.status === 'pass' ? 'This label meets the checks performed.' : 'This label is mostly compliant, with findings that deserve attention.'}</p>
    <h2>Findings</h2>
    ${violations}
    <h2>Declarations checked</h2>
    <table><thead><tr><th>Declaration</th><th>Status</th><th>Evidence</th><th>Rule reference</th></tr></thead><tbody>${declarations}</tbody></table>
    <h2>Nutrition</h2>
    <div class="nutrition-grid">${nutrition}</div>
    <footer>Generated by LegalAkshi on ${escapeReportValue(generatedAt)}. This report provides evidence and guidance; final findings are made by the relevant authority.</footer>
  </main>
</body>
</html>`;
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `legalakshi-report-${product.id}.html`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function ProductCard({ product }: { product: Product }) {
  const [, setLocation] = useLocation();
  return <button onClick={() => setLocation(`/analysis/${product.id}`)} className="group w-full rounded-xl border border-[#e2eae4] bg-white p-3 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:border-[#b9e6d0] hover:shadow-md" data-testid={`card-product-${product.id}`}><ProductVisual kind={product.image} /><div className="px-1 pt-3"><div className="flex items-start justify-between gap-2"><div><h3 className="text-sm font-bold text-[#20382b]">{product.name}</h3><p className="mt-1 text-xs text-[#819087]">{product.manufacturer}</p></div><div className={`grid h-10 w-10 shrink-0 place-items-center rounded-full border-[3px] text-xs font-extrabold ${product.status === 'pass' ? 'border-[#8bd8b4] text-[#12885c]' : 'border-[#f1cf71] text-[#a27812]'}`}>{product.score}</div></div><div className="mt-3 flex items-center justify-between"><StatusBadge status={product.status} /><span className="text-[11px] text-[#99a49e]">{product.scannedAt}</span></div></div></button>;
}

function useCurrentUserName(fallback: string) {
  const { user, isSignedIn } = useUser();

  if (isSignedIn && user) {
    return user.firstName || user.fullName || user.username || fallback;
  }

  return 'Mayank';
}

function ConsumerDashboard() {
  // Verification-first consumer home: real backend counts only
  // (GET /api/v1/consumer/overview). No demo metrics, no fabricated
  // verification statistics; backend down → explicit unavailable state.
  const name = useCurrentUserName('there');
  const [overview, setOverview] = useState<ConsumerOverview | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [history, setHistory] = useState<RecentCheck[]>([]);
  const load = () => {
    setLoading(true);
    setError('');
    setHistory(readLookups());
    api.consumerOverview()
      .then(setOverview)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  const statusTone = (s: string) => s === 'VERIFIED' ? 'green' : s === 'NEEDS_REVIEW' ? 'yellow' : 'neutral';
  const statusLabel = (s: string) => s === 'VERIFIED' ? 'Verified' : s === 'NEEDS_REVIEW' ? 'Needs review' : 'Not verified';
  return <><PageTitle eyebrow={new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })} title={`Know what you're buying, ${name}.`} description="Verify products against officer inspections, understand nutrition, and report problems." action={<Link href="/verify-product" className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_5px_12px_rgba(24,185,120,.18)] hover:bg-[#119e67]" data-testid="link-verify-product"><ScanLine size={17} />Verify a product</Link>} />
    {error && <div className="mb-5"><BackendError message={`LegalAkshi verification is temporarily unavailable. ${error}`} onRetry={load} /></div>}
    <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="overview-stats">
      {loading && !overview
        ? <>
          <StatSkeleton label="Products checked" />
          <StatSkeleton label="Verified products" />
          <StatSkeleton label="Complaints raised" />
          <StatSkeleton label="Open complaints" />
        </>
        : overview ? <>
          <Stat label="Products checked" value={String(overview.products_checked)} note="with inspection results" icon={ScanLine} tone="green" />
          <Stat label="Verified products" value={String(overview.verified_products)} note="officer-backed" icon={BadgeCheck} tone="green" />
          <Stat label="Complaints raised" value={String(overview.complaints_raised)} note="by you" icon={ClipboardCheck} tone="blue" />
          <Stat label="Open complaints" value={String(overview.open_complaints)} note="awaiting resolution" icon={CircleAlert} tone="yellow" />
        </>
        : <>
          <Stat label="Products checked" value="—" note="unavailable" icon={ScanLine} tone="yellow" />
          <Stat label="Verified products" value="—" note="unavailable" icon={BadgeCheck} tone="yellow" />
          <Stat label="Complaints raised" value="—" note="unavailable" icon={ClipboardCheck} tone="yellow" />
          <Stat label="Open complaints" value="—" note="unavailable" icon={CircleAlert} tone="yellow" />
        </>}
    </div>
      <section className="mb-8 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-lg bg-[#e0f7eb] text-[#13885c]"><Leaf size={19} /></span><div><h2 className="font-bold text-[#20382b]">Understand nutrition</h2><p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">View nutrition information from verified package data.</p></div></div><Link href="/nutrition" className="mt-4 inline-flex items-center gap-2 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-understand-nutrition">Open nutrition <ArrowRight size={14} /></Link></div>
        <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-lg bg-[#fff4d4] text-[#a27812]"><CircleAlert size={19} /></span><div><h2 className="font-bold text-[#20382b]">Report a problem</h2><p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">Found an issue with a packaged product? Tell LegalAkshi.</p></div></div><Link href="/complaint/new" className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#119e67]" data-testid="link-report-problem-home"><CircleAlert size={16} />Report a problem</Link></div>
        <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-lg bg-[#e5f1f4] text-[#507b8c]"><Lightbulb size={19} /></span><div><h2 className="font-bold text-[#20382b]">Share a suggestion</h2><p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">Ideas for authenticity, verification, and awareness.</p></div></div><Link href="/suggestions" className="mt-4 inline-flex items-center gap-2 text-xs font-bold text-[#12885c] hover:underline" data-testid="link-suggest-home">Suggest an improvement <ArrowRight size={14} /></Link></div>
        <DemoHomeCard />
      </section>
      <section><div className="mb-4 flex items-center justify-between"><div><h2 className="text-lg font-extrabold tracking-[-.03em] text-[#20382b]">Recently checked</h2><p className="mt-1 text-xs text-[#7c8a82]">Products you verified on this device</p></div><Link href="/verify-product" className="flex items-center gap-1 text-xs font-bold text-[#138d60] hover:text-[#0c6f4b]" data-testid="link-verify-more">Verify another <ChevronRight size={14} /></Link></div>
        {isDemoMode()
          ? <div className="space-y-4" data-testid="demo-history">
            <DemoBanner text="Demo mode: sample verification history below. Real checks appear here when demo mode is off." />
            {DEMO_PRODUCTS.map((p) => <Link key={p.product_id} href={`/verify-product/${p.product_id}`} className="group flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-[#cbdad0] bg-white p-4 text-left shadow-soft" data-testid={`demo-history-${p.product_id}`}><div className="min-w-0"><h3 className="truncate text-sm font-bold text-[#20382b]">{p.product_name}</h3><p className="mt-1 text-[11px] text-[#819087]">sample check · {p.inspection_date || 'no inspection'}</p></div><span className="flex shrink-0 items-center gap-2"><Badge tone={statusTone(p.status)}>{statusLabel(p.status)}</Badge><DemoBadge testId={`demo-badge-${p.product_id}`} /></span></Link>)}
          </div>
          : history.length === 0
            ? <EmptyState icon={ScanLine} title="No recent product checks." text="Verify a product by barcode or name — your checks on this device will appear here." />
            : <div className="grid gap-4 md:grid-cols-2">{history.map((r) => <Link key={`${r.product_id}-${r.at}`} href={`/verify-product/${r.product_id}`} className="group w-full rounded-xl border border-[#e2eae4] bg-white p-4 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:border-[#b9e6d0]" data-testid={`card-check-${r.product_id}`}><div className="flex items-center justify-between gap-3"><div className="min-w-0"><h3 className="truncate text-sm font-bold text-[#20382b]">{r.product_name || 'Unnamed product'}</h3><p className="mt-1 text-[11px] text-[#819087]">checked {(r.at || '').slice(0, 10)}</p></div><Badge tone={statusTone(r.status || '')}>{statusLabel(r.status || '')}</Badge></div></Link>)}</div>}
      </section>
  </>;
}

function DemoHomeCard() {
  const [, setLocation] = useLocation();
  const [demo, setDemo] = useState(() => isDemoMode());
  const toggle = () => {
    const next = !demo;
    setDemoMode(next);
    setDemo(next);
    if (next) setLocation('/verify-product');
  };
  return <div className="rounded-2xl border border-dashed border-[#cbdad0] bg-white p-6 shadow-soft"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-lg bg-[#fff4d4] text-[#946b09]"><FlaskConical size={19} /></span><div><h2 className="font-bold text-[#20382b]">Demo data</h2><p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">Explore with clearly-labelled samples — never real data.</p></div></div><button onClick={toggle} className="mt-4 inline-flex items-center gap-2 rounded-lg border border-[#dce7df] px-4 py-2.5 text-xs font-bold text-[#426050] hover:border-[#18B978]" data-testid="button-demo-home">{demo ? 'Exit demo mode' : 'Try demo data'}</button></div>;
}

function StatSkeleton({ label }: { label: string }) {
  return <div className="flex items-center justify-between rounded-xl border border-[#e1eae4] bg-white p-5 shadow-soft" data-testid="overview-skeleton"><div><p className="text-xs font-semibold text-[#7b8981]">{label}</p><div className="mt-2 h-8 w-16 animate-pulse rounded-md bg-[#e8efe9]" /><div className="mt-1 h-3 w-24 animate-pulse rounded bg-[#eef3ef]" /></div><span className="grid h-10 w-10 animate-pulse place-items-center rounded-lg bg-[#e8efe9]" /></div>;
}

function Stat({ label, value, note, icon: Icon, tone }: { label: string; value: string; note: string; icon: typeof ScanLine; tone: string }) {
  return <div className="flex items-center justify-between rounded-xl border border-[#e1eae4] bg-white p-5 shadow-soft"><div><p className="text-xs font-semibold text-[#7b8981]">{label}</p><p className="mt-2 text-2xl font-extrabold tracking-[-.05em] text-[#20382b]">{value}</p><p className={`mt-1 text-[11px] font-semibold ${tone === 'yellow' ? 'text-[#a27812]' : tone === 'blue' ? 'text-[#507b8c]' : 'text-[#138d60]'}`}>{note}</p></div><span className={`grid h-10 w-10 place-items-center rounded-lg ${tone === 'yellow' ? 'bg-[#fff5d8] text-[#b38417]' : tone === 'blue' ? 'bg-[#e5f1f4] text-[#507b8c]' : 'bg-[#e0f7eb] text-[#138d60]'}`}><Icon size={19} /></span></div>;
}

function Landing() {
  return <div className="min-h-[100dvh] overflow-hidden bg-[#F7F8F6] text-[#17191C]"><header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6"><Logo /><div className="hidden items-center gap-8 text-sm font-semibold text-[#617169] md:flex"><a href="#how" className="hover:text-[#18B978]">How it works</a><a href="#officers" className="hover:text-[#18B978]">For officers</a><a href="#trust" className="hover:text-[#18B978]">Our checks</a></div><div className="flex items-center gap-2"><Link href="/login" className="rounded-lg px-3 py-2 text-sm font-semibold text-[#547064] hover:bg-white" data-testid="link-landing-login">Log in</Link><Link href="/signup" className="rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white shadow-[0_5px_12px_rgba(24,185,120,.2)] hover:bg-[#119e67]" data-testid="link-landing-signup">Get started</Link></div></header><main>
    <section className="relative mx-auto grid max-w-6xl items-center gap-12 px-6 pb-20 pt-14 md:grid-cols-[1.03fr_.97fr] md:pb-28 md:pt-20"><div className="absolute left-[-120px] top-16 h-72 w-72 rounded-full bg-[#d8f3e4] blur-3xl" /><div className="relative"><div className="mb-6 inline-flex items-center gap-2 rounded-full border border-[#c8ead8] bg-[#eaf8f1] px-3 py-1.5 text-xs font-bold text-[#11875a]"><span className="h-1.5 w-1.5 rounded-full bg-[#18B978]" />Built for the Indian shelf</div><h1 className="max-w-xl text-[46px] font-extrabold leading-[1.04] tracking-[-.065em] text-[#173a2a] md:text-[68px]">Read the label.<br /><span className="text-[#18B978]">Know your rights.</span></h1><p className="mt-6 max-w-lg text-[17px] leading-relaxed text-[#63756b]">LegalAkshi turns the fine print on packaged food into a clear answer — checked against FSSAI and Legal Metrology rules.</p><div className="mt-8 flex flex-wrap items-center gap-3"><Link href="/signup" className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-5 py-3 text-sm font-bold text-white shadow-[0_6px_16px_rgba(24,185,120,.2)] hover:bg-[#119e67]" data-testid="link-hero-start">Check a label <ArrowRight size={17} /></Link><Link href="/login" className="inline-flex items-center gap-2 rounded-lg border border-[#d9e6dd] bg-white px-5 py-3 text-sm font-bold text-[#2e5140] hover:border-[#18B978]" data-testid="link-hero-demo"><QrCode size={16} />See a sample report</Link></div><div className="mt-9 flex items-center gap-5 text-xs text-[#839289]"><span className="flex items-center gap-1.5"><CheckCircle2 size={15} className="text-[#18B978]" />Free to start</span><span className="flex items-center gap-1.5"><LockKeyhole size={14} className="text-[#18B978]" />Private by design</span></div></div><div className="relative mx-auto w-full max-w-[470px]"><div className="absolute -inset-5 rounded-[28px] bg-[#e4f5eb] rotate-3" /><div className="relative rounded-2xl border border-[#dceae1] bg-white p-4 shadow-lift"><div className="mb-4 flex items-center justify-between border-b border-[#edf1ee] pb-3"><div className="flex items-center gap-2"><span className="grid h-7 w-7 place-items-center rounded-lg bg-[#dff5e9] text-[#18B978]"><ScanLine size={15} /></span><span className="text-xs font-bold text-[#315542]">Label check</span></div><span className="font-mono text-[10px] text-[#9aa69f]">LA / 00082</span></div><ProductVisual kind="atta" large /><div className="mt-4 flex items-center justify-between"><div><p className="text-sm font-bold text-[#20382b]">Aashirvaad Select Atta</p><p className="mt-1 text-xs text-[#829088]">Staples · scanned just now</p></div><div className="grid h-14 w-14 place-items-center rounded-full border-4 border-[#f1cf71] text-lg font-extrabold text-[#a27812]">82</div></div><div className="mt-4 rounded-xl bg-[#fff7df] p-3.5"><div className="flex items-center gap-2 text-xs font-bold text-[#946b09]"><Info size={15} />One thing needs your eye</div><p className="mt-1.5 text-xs leading-relaxed text-[#7b6a3a]">Net quantity is present, but appears on the side seam instead of the principal panel.</p></div><div className="mt-3 flex items-center justify-between border-t border-[#edf1ee] pt-3 text-[11px] font-semibold text-[#138d60]"><span>4 declarations checked</span><span className="flex items-center gap-1">Open report <ArrowRight size={13} /></span></div></div></div></section>
<section id="how" className="border-y border-[#e2ebe5] bg-white"><div className="mx-auto max-w-6xl px-6 py-20"><div className="max-w-xl"><p className="text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">A second pair of eyes</p><h2 className="mt-3 text-3xl font-extrabold tracking-[-.05em] text-[#173a2a] md:text-4xl">Less guessing in the aisle.</h2><p className="mt-3 text-sm leading-relaxed text-[#68786e]">Three calm steps from a package in your hand to an answer you can trust.</p></div><div className="mt-12 grid gap-8 md:grid-cols-3">{[['01','Capture','Point your camera at the front and back of any packaged food label.'],['02','Understand','We read the text and check declarations against the current rules.'],['03','Act','Get a plain-language report, then raise a complaint when something is off.']].map(([num,title,copy]) => <div key={num} className="border-t-2 border-[#bce9d2] pt-5"><span className="font-mono text-xs text-[#18B978]">{num}</span><h3 className="mt-5 text-xl font-bold text-[#20382b]">{title}</h3><p className="mt-2 text-sm leading-relaxed text-[#718078]">{copy}</p></div>)}</div></div></section>
    <section id="officers" className="mx-auto grid max-w-6xl items-center gap-12 px-6 py-20 md:grid-cols-[.8fr_1.2fr]"><div><p className="text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">For the people who enforce</p><h2 className="mt-3 text-3xl font-extrabold tracking-[-.05em] text-[#173a2a] md:text-4xl">From evidence to action, without the paper trail.</h2><p className="mt-4 text-sm leading-relaxed text-[#68786e]">Regulatory teams get a focused queue of verified complaints, rule-level evidence, and an audit trail built for the next step.</p><Link href="/login" className="mt-7 inline-flex items-center gap-2 text-sm font-bold text-[#12885c]" data-testid="link-officer-login">Explore officer workspace <ArrowRight size={16} /></Link></div><div className="rounded-2xl border border-[#dce9e0] bg-white p-5 shadow-soft"><div className="flex items-center justify-between border-b border-[#edf1ee] pb-4"><div className="flex items-center gap-2 text-sm font-bold text-[#20382b]"><ClipboardCheck size={17} className="text-[#18B978]" />Enforcement queue</div><Badge tone="yellow">3 awaiting review</Badge></div>{['Missing FSSAI licence number','Net quantity on side panel','Misleading “natural” claim'].map((item, i) => <div key={item} className="flex items-center justify-between border-b border-[#f0f3f0] py-4 last:border-0"><div className="flex items-center gap-3"><span className={`grid h-8 w-8 place-items-center rounded-lg ${i === 0 ? 'bg-[#fce6e4] text-[#b43b37]' : 'bg-[#fff4d4] text-[#a27812]'}`}><CircleAlert size={15} /></span><div><p className="text-sm font-semibold text-[#30473a]">{item}</p><p className="mt-1 text-[11px] text-[#92a098]">{['Bengaluru · 2 hours ago','Pune · Yesterday','Delhi · 2 days ago'][i]}</p></div></div><ChevronRight size={16} className="text-[#adb8b0]" /></div>)}</div></section>
    <section id="trust" className="bg-[#173a2a] px-6 py-16 text-white"><div className="mx-auto flex max-w-6xl flex-col justify-between gap-8 md:flex-row md:items-end"><div><p className="text-[11px] font-bold uppercase tracking-[.17em] text-[#7fddb0]">The fine print matters</p><h2 className="mt-3 max-w-xl text-3xl font-extrabold tracking-[-.05em] md:text-4xl">Clear enough to use.<br />Serious enough to trust.</h2></div><p className="max-w-sm text-sm leading-relaxed text-[#b8d3c5]">No verdicts hidden behind jargon. Every finding shows the evidence, the requirement, and the rule reference.</p></div></section>
  </main><footer className="mx-auto flex max-w-6xl flex-col justify-between gap-4 px-6 py-8 text-xs text-[#8b9991] sm:flex-row"><Logo compact /><span>© 2024 LegalAkshi · Made for clearer shelves</span></footer></div>;
}

function AuthPage({ mode }: { mode: 'login' | 'signup' }) {
  // No role picker here: the workspace (consumer/officer/admin) comes from
  // the Clerk account role. Nobody can self-assign an officer workspace.
  return <div className="grid min-h-[100dvh] bg-[#F7F8F6] md:grid-cols-[.86fr_1.14fr]">
    <div className="relative hidden overflow-hidden bg-[#173a2a] p-12 text-white md:block">
      <div className="absolute -bottom-28 -left-16 h-80 w-80 rounded-full border-[30px] border-[#18B978]/20" />
      <div className="absolute right-[-80px] top-[-70px] h-72 w-72 rounded-full border-[28px] border-[#f3ca68]/15" />
      <Logo />
      <div className="relative mt-32 max-w-md">
        <span className="font-mono text-xs text-[#84dcb1]">LEGALAKSHI / PRIVATE WORKSPACE</span>
        <h1 className="mt-6 text-5xl font-extrabold leading-[1.06] tracking-[-.06em]">The label<br />shouldn't be<br /><span className="text-[#72dfaa]">a puzzle.</span></h1>
        <p className="mt-7 max-w-sm text-sm leading-relaxed text-[#b8d3c5]">A clear-eyed companion for every packaged-food decision — and a sharper workspace for the people who keep the rules moving.</p>
      </div>
      <div className="absolute bottom-10 left-12 flex items-center gap-2 text-xs text-[#9cbbae]"><LockKeyhole size={14} className="text-[#72dfaa]" />Your scans stay private</div>
    </div>
    <div className="flex items-center justify-center overflow-y-auto p-6 md:p-12">
      <div className="w-full max-w-[470px]">
        <div className="mb-8 inline-flex md:hidden" data-testid="link-auth-logo"><Logo /></div>
        <div className="mb-5">
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">{mode === 'login' ? 'Welcome back' : 'Start with clarity'}</p>
          <h2 className="text-3xl font-extrabold tracking-[-.05em] text-[#173a2a]">{mode === 'login' ? 'Good to see you.' : 'Create your account.'}</h2>
          <p className="mt-2 text-sm text-[#75837b]">{mode === 'login' ? 'Pick up where you left off.' : 'A better way to read what you buy.'}</p>
        </div>
        <div className="mb-5 rounded-lg border border-[#dce7df] bg-white p-3 text-xs leading-relaxed text-[#75837b]" data-testid="text-auth-role-note">
          <LockKeyhole size={13} className="mr-1 inline text-[#18B978]" />
          Officers and admins sign in with their official accounts — workspace access follows the account role.
        </div>
        <div className="rounded-2xl border border-[#dfe9e2] bg-white p-4 shadow-soft md:p-6">
          {mode === 'login'
            ? <SignIn routing="path" path={`${basePath}/sign-in`} signUpUrl={`${basePath}/sign-up`} />
            : <SignUp routing="path" path={`${basePath}/sign-up`} signInUrl={`${basePath}/sign-in`} />}
        </div>
      </div>
    </div>
  </div>;
}

function Field({ label, placeholder, type = 'text', testId, value, onChange }: { label: string; placeholder?: string; type?: string; testId: string; value?: string; onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void }) {
  return <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">{label}</span><input required type={type} placeholder={placeholder} value={value} onChange={onChange} className="focus-ring w-full rounded-lg border border-[#dbe6de] bg-white px-3.5 py-3 text-sm text-[#20382b] outline-none transition focus:border-[#18B978]" data-testid={testId} /></label>;
}

function UploadPage() {
  const [, setLocation] = useLocation();
  const [uploading, setUploading] = useState(false);
  const [photos, setPhotos] = useState<{ front: ScanPhoto | null; back: ScanPhoto | null }>({ front: currentScanPhotos.front, back: currentScanPhotos.back });
  const setPhoto = (side: 'front' | 'back', file: File) => {
    setScanPhoto(side, { name: file.name, url: URL.createObjectURL(file) });
    setPhotos({ ...currentScanPhotos });
  };
  const removePhoto = (side: 'front' | 'back') => {
    setScanPhoto(side, null);
    setPhotos({ ...currentScanPhotos });
  };
  const handleUpload = () => { if (!photos.front || !photos.back) return; setUploading(true); setTimeout(() => setLocation('/extraction'), 900); };
  const handleSample = () => {
    clearScanPhotos();
    setPhotos({ front: null, back: null });
    setUploading(true);
    setTimeout(() => setLocation('/extraction'), 600);
  };
  return <><PageTitle eyebrow="Label scanner" title="Bring both sides closer." description="Add one clear photo of the front and one of the back. We’ll read both sides, then you can review every field before analysis." /><div className="mx-auto max-w-4xl">
    <div className="mb-5 flex items-start gap-3 rounded-xl border border-[#cfeedd] bg-[#eaf8f1] p-4 text-xs leading-relaxed text-[#4c7761]"><ScanLine size={17} className="mt-0.5 shrink-0 text-[#18B978]" /><span><b className="text-[#276047]">Two photos make the check stronger.</b> The front shows the product identity; the back usually carries the declarations, licence, quantity, and nutrition details.</span></div>
    <div className="grid gap-5 md:grid-cols-2"><PhotoSlot title="Front of the product" description="Brand, product name, claims, and principal display panel." photo={photos.front} onChange={file => setPhoto('front', file)} onRemove={() => removePhoto('front')} testId="input-label-photo-front" /><PhotoSlot title="Back of the product" description="Ingredients, nutrition, licence, dates, quantity, and manufacturer details." photo={photos.back} onChange={file => setPhoto('back', file)} onRemove={() => removePhoto('back')} testId="input-label-photo-back" /></div>
    <div className="mt-5 flex flex-col-reverse items-stretch justify-between gap-3 border-t border-[#e4ece6] pt-5 sm:flex-row sm:items-center"><Button variant="secondary" onClick={handleSample} disabled={uploading} data-testid="button-use-sample">{uploading ? 'Preparing sample…' : 'Use sample label'} <ArrowRight size={15} /></Button><Button onClick={handleUpload} disabled={uploading || !photos.front || !photos.back} data-testid="button-start-scan">{uploading ? 'Preparing scan…' : 'Analyze both photos'} <ArrowRight size={16} /></Button></div>
    <p className="mt-3 text-right text-[11px] text-[#8b9890]">{photos.front && photos.back ? 'Both label sides are ready for OCR review.' : 'Add both photos to continue, or use the sample label.'}</p>
  </div></>;
}

function ExtractionPage() {
  const [, setLocation] = useLocation();
  const product = products[0];
  const [fields, setFields] = useState(product.extractedFields);
  const update = (key: string, value: string) => setFields(old => old.map(field => field.key === key ? { ...field, value } : field));
  const photoEntries = [{ key: 'front', label: 'Front of product', photo: currentScanPhotos.front }, { key: 'back', label: 'Back of product', photo: currentScanPhotos.back }];
  return <><PageTitle eyebrow="Step 2 of 3 · OCR review" title="Make it accurate." description="We found these details across both label photos. Correct anything that looks off — your changes become the source of truth for the report." action={<span className="font-mono text-xs text-[#849188]">SCAN / LA-00082</span>} /><div className="grid gap-6 lg:grid-cols-[.8fr_1.2fr]"><div className="rounded-2xl border border-[#dfe9e2] bg-white p-4 shadow-soft"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">{photoEntries.map(entry => <div key={entry.key} className="overflow-hidden rounded-xl border border-[#e3ece5] bg-[#fbfcfb]"><div className="flex h-44 items-center justify-center">{entry.photo ? <img src={entry.photo.url} alt={`${entry.label} preview`} className="h-full w-full object-cover" /> : <ProductVisual kind="atta" large />}</div><div className="flex items-center gap-2 border-t border-[#e3ece5] px-3 py-2 text-xs font-semibold text-[#607269]"><ScanLine size={14} className="text-[#18B978]" />{entry.label}</div></div>)}</div><button className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-[#dce7df] py-2.5 text-xs font-bold text-[#426050] hover:border-[#18B978]" onClick={() => setLocation('/upload')} data-testid="button-replace-photo"><UploadCloud size={15} />Replace label photos</button></div><div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft"><div className="flex items-center justify-between border-b border-[#edf1ee] pb-4"><div><h2 className="font-bold text-[#20382b]">Extracted fields</h2><p className="mt-1 text-xs text-[#849188]">Sample label — demonstration values, not a real OCR result</p></div><Badge tone="yellow"><Info size={12} />Sample data</Badge></div><div className="mt-5 space-y-4">{fields.map(field => <div key={field.key}><div className="mb-1.5 flex items-center justify-between"><label className="text-xs font-bold text-[#42554a]">{field.label}</label><span className={`font-mono text-[10px] ${field.confidence > .9 ? 'text-[#18a86f]' : 'text-[#a27812]'}`}>{Math.round(field.confidence * 100)}% match</span></div><div className="relative"><input value={field.value} onChange={e => update(field.key, e.target.value)} className="focus-ring w-full rounded-lg border border-[#dce7df] bg-[#fbfcfb] px-3 py-2.5 pr-10 text-sm text-[#20382b] outline-none focus:border-[#18B978]" data-testid={`input-extracted-${field.key}`} /><Pencil size={14} className="pointer-events-none absolute right-3 top-3 text-[#a0ada5]" /></div></div>)}</div><div className="mt-6 flex flex-col gap-3 border-t border-[#edf1ee] pt-5"><p className="text-[11px] leading-relaxed text-[#849188]">Percentages above are illustrative sample values. To get a real compliance result, send the reviewed fields to the backend rule engine as a manual declaration.</p><div className="flex items-center justify-between"><span className="text-xs text-[#7c8a82]">Stored locally only</span><div className="flex gap-2"><Button variant="secondary" onClick={() => { setStored('lastExtraction', fields); setLocation(`/analysis/${product.id}`); }} data-testid="button-view-sample-report">Sample report</Button><Button onClick={() => { const byKey = Object.fromEntries(fields.map(f => [f.key, f.value])); const qty = String(byKey.netQuantity || '').split(/\s+/); try { sessionStorage.setItem('legalakshi:lab-draft', JSON.stringify({ product_name: byKey.productName || '', manufacturer: byKey.manufacturer || '', quantity: qty[0] || '', quantity_unit: qty[1] || '', manufacturing_date: byKey.mfgDate || '', mrp: '', consumer_care: '' })); } catch { /* ignore */ } setLocation('/lab-check'); }} data-testid="button-run-analysis">Analyze with backend <ArrowRight size={16} /></Button></div></div></div></div></div></>;
}

function AnalysisPage() {
  const params = useParams<{ id: string }>();
  const [, setLocation] = useLocation();
  const product = products.find(p => p.id === params.id) ?? products[0];
  const [tab, setTab] = useState<'summary' | 'declarations' | 'nutrition'>('summary');
  const [expanded, setExpanded] = useState<string | null>(null);
  const tone = product.status === 'pass' ? 'green' : 'yellow';
  return <><div className="mb-5 flex items-start gap-3 rounded-xl border border-[#f1cf71] bg-[#fff8e1] p-4 text-xs leading-relaxed text-[#7d652c]" data-testid="banner-demo-report"><Info size={17} className="mt-0.5 shrink-0" /><span><b>Sample report — demonstration data.</b> This view shows built-in mock content, not a backend analysis. For real rule-engine results, use Lab check.</span></div><PageTitle eyebrow="Compliance report · 18 June 2024" title={product.name} description={`${product.manufacturer} · ${product.category}`} action={<Button variant="secondary" onClick={() => downloadComplianceReport(product)} data-testid="button-download-report"><Download size={16} />Download report</Button>} /><div className="mb-6 grid gap-5 lg:grid-cols-[.72fr_1.28fr]"><div className={`rounded-2xl p-7 ${product.status === 'pass' ? 'bg-[#e7f8ee]' : 'bg-[#fff5d9]'}`}><div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-[.14em] text-[#6a7f72]">Overall compliance</p><div className={`mt-4 text-6xl font-extrabold tracking-[-.07em] ${product.status === 'pass' ? 'text-[#0d8556]' : 'text-[#a27812]'}`}>{product.score}<span className="text-2xl text-current/50">/100</span></div></div><div className={`grid h-16 w-16 place-items-center rounded-full ${product.status === 'pass' ? 'bg-[#b9e9cf] text-[#0c8556]' : 'bg-[#f8dc89] text-[#9b7314]'}`}><BadgeCheck size={31} /></div></div><div className="mt-7 h-2 overflow-hidden rounded-full bg-white/70"><div className={`h-full rounded-full ${product.status === 'pass' ? 'bg-[#18B978]' : 'bg-[#e3b73f]'}`} style={{ width: `${product.score}%` }} /></div><p className={`mt-4 text-sm font-semibold ${product.status === 'pass' ? 'text-[#176d4b]' : 'text-[#7d652c]'}`}>{product.status === 'pass' ? 'This label meets the checks we ran.' : 'Mostly compliant, with a couple of things worth knowing.'}</p></div><div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft"><div className="flex items-start justify-between"><div><p className="text-xs font-bold uppercase tracking-[.14em] text-[#87958c]">What this means</p><h2 className="mt-3 text-lg font-extrabold tracking-[-.03em] text-[#20382b]">{product.status === 'pass' ? 'A clean label, at a glance.' : 'One finding needs your eye.'}</h2></div><span className="text-[#18B978]"><Sparkles size={20} /></span></div><p className="mt-3 text-sm leading-relaxed text-[#697970]">{product.status === 'pass' ? 'The key declarations we checked are present and legible. Keep this report with your purchase record.' : 'We found a placement issue that may make an important piece of information harder to see. It is not a food safety verdict.'}</p><div className="mt-5 flex gap-2">{product.violations.map(v => <Badge key={v.id} tone={v.severity === 'High' ? 'red' : 'yellow'}>{v.severity} priority</Badge>)}</div></div></div><div className="mb-5 flex gap-1 border-b border-[#dde7df]"><button onClick={() => setTab('summary')} className={`border-b-2 px-3 py-3 text-sm font-bold ${tab === 'summary' ? 'border-[#18B978] text-[#12885c]' : 'border-transparent text-[#87938c]'}`} data-testid="button-tab-summary">Findings {product.violations.length > 0 && <span className="ml-1 rounded-full bg-[#fff0c1] px-1.5 py-0.5 text-[10px] text-[#946b09]">{product.violations.length}</span>}</button><button onClick={() => setTab('declarations')} className={`border-b-2 px-3 py-3 text-sm font-bold ${tab === 'declarations' ? 'border-[#18B978] text-[#12885c]' : 'border-transparent text-[#87938c]'}`} data-testid="button-tab-declarations">Declarations</button><button onClick={() => setTab('nutrition')} className={`border-b-2 px-3 py-3 text-sm font-bold ${tab === 'nutrition' ? 'border-[#18B978] text-[#12885c]' : 'border-transparent text-[#87938c]'}`} data-testid="button-tab-nutrition">Nutrition</button></div>{tab === 'summary' && <div className="space-y-3">{product.violations.length === 0 ? <EmptyState icon={CheckCircle2} title="No issues found" text="All declarations in this check passed." /> : product.violations.map(v => <div key={v.id} className="rounded-xl border border-[#e2eae4] bg-white shadow-soft"><button onClick={() => setExpanded(expanded === v.id ? null : v.id)} className="flex w-full items-center gap-4 p-5 text-left" data-testid={`button-expand-violation-${v.id}`}><span className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${v.severity === 'High' ? 'bg-[#fce6e4] text-[#b43b37]' : 'bg-[#fff4d4] text-[#a27812]'}`}><CircleAlert size={19} /></span><span className="min-w-0 flex-1"><span className="block text-sm font-bold text-[#20382b]">{v.issue}</span><span className="mt-1 block text-xs text-[#849188]">{v.clause} · {v.severity} priority</span></span><ChevronRight size={17} className={`text-[#9ba8a0] transition-transform ${expanded === v.id ? 'rotate-90' : ''}`} /></button>{expanded === v.id && <div className="border-t border-[#edf1ee] bg-[#fbfcfb] px-5 pb-5 pt-4"><div className="grid gap-4 sm:grid-cols-2"><div><p className="mb-1 text-[10px] font-bold uppercase tracking-[.12em] text-[#9aa69f]">Evidence found</p><p className="text-sm leading-relaxed text-[#4b5d52]">{v.evidence}</p></div><div><p className="mb-1 text-[10px] font-bold uppercase tracking-[.12em] text-[#9aa69f]">Requirement</p><p className="text-sm leading-relaxed text-[#4b5d52]">{v.requirement}</p></div></div><div className="mt-4 flex items-center gap-2 text-xs font-semibold text-[#a27812]"><BookOpen size={14} />{v.clause} · Typical penalty {v.penalty}</div></div>}</div>)}<div className="mt-5 flex items-center justify-between rounded-xl border border-[#bee8d1] bg-[#ebf9f1] p-5"><div><p className="text-sm font-bold text-[#1e513a]">Want to report this?</p><p className="mt-1 text-xs text-[#5d806e]">Turn the evidence above into a tracked complaint.</p></div><Button onClick={() => setLocation('/complaint/new')} data-testid="button-start-complaint">Start a complaint <ArrowRight size={15} /></Button></div></div>}{tab === 'declarations' && <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">{product.declarations.map(item => <div key={item.key} className="flex flex-col gap-3 border-b border-[#edf1ee] p-5 last:border-0 sm:flex-row sm:items-center"><span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${item.status === 'pass' ? 'bg-[#e4f7ec] text-[#138d60]' : 'bg-[#fff5d8] text-[#a27812]'}`}>{item.status === 'pass' ? <Check size={17} /> : <Info size={17} />}</span><div className="flex-1"><p className="text-sm font-bold text-[#30473a]">{item.label}</p><p className="mt-1 text-xs text-[#7d8a82]">{item.evidence}</p></div><div className="text-left sm:text-right"><StatusBadge status={item.status} /><p className="mt-1 font-mono text-[10px] text-[#9aa69f]">{item.ruleRef}</p></div></div>)}</div>}{tab === 'nutrition' && <div className="grid gap-4 sm:grid-cols-2">{product.nutrition.map(n => <div key={n.label} className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft"><p className="text-xs text-[#849188]">{n.label}</p><p className="mt-2 text-2xl font-extrabold tracking-[-.04em] text-[#20382b]">{n.value}</p><p className="mt-1 text-[11px] text-[#a0aaa4]">per 100 g</p></div>)}</div>}</>;
}

function ComplaintNew() {
  const [, setLocation] = useLocation();
  const prefill = useComplaintPrefill();
  const [step, setStep] = useState(1);
  const [productName, setProductName] = useState(prefill.product);
  const [category, setCategory] = useState<string>(COMPLAINT_CATEGORIES[0]);
  const [retailer, setRetailer] = useState('');
  const [city, setCity] = useState('');
  const [batchLot, setBatchLot] = useState('');
  const [purchasePlace, setPurchasePlace] = useState('');
  const [purchaseDate, setPurchaseDate] = useState('');
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [trackingId, setTrackingId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  // Complaint backend is unchanged (POST /api/v1/complaints). The schema
  // has no category/batch/purchase columns, so those travel inside the
  // description block + evidence list — never as fabricated backend fields.
  const submit = async () => {
    setSubmitting(true);
    setSubmitError('');
    try {
      const details = [`Batch/lot: ${batchLot || '—'}`, `Purchased at: ${purchasePlace || retailer || '—'}${purchaseDate ? ` on ${purchaseDate}` : ''}`].join('\n');
      const created = await api.createComplaint({
        product_name: productName || 'Unnamed product',
        retailer: purchasePlace || retailer, city, severity: 'Medium',
        description: `${formatComplaintDescription(category, description)}\n${details}`,
      });
      setTrackingId(created.complaint_id);
      setSubmitted(true);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };
  if (submitted) return <div className="mx-auto max-w-xl py-12 text-center"><span className="mx-auto grid h-16 w-16 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><CheckCircle2 size={31} /></span><p className="mt-6 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Complaint submitted</p><h1 className="mt-3 text-3xl font-extrabold tracking-[-.05em] text-[#173a2a]">Your report is in motion.</h1><p className="mt-3 text-sm leading-relaxed text-[#718078]">Officers will review it against the inspection record. You can follow the progress from your complaint history.</p><div className="mt-7 rounded-xl bg-white p-5 shadow-soft"><p className="text-xs text-[#86938c]">Complaint ID: {trackingId}</p><p className="mt-2 font-mono text-xl font-bold text-[#12885c]" data-testid="text-complaint-id">{trackingId}</p></div><div className="mt-6 flex justify-center gap-3"><Button variant="secondary" onClick={() => setLocation('/complaints')} data-testid="button-view-complaint">View complaint</Button><Button onClick={() => setLocation('/verify-product')} data-testid="button-back-product">Back to product</Button></div></div>;
  return <><PageTitle eyebrow={`Complaint wizard · Step ${step} of 3`} title="Make the evidence count." description="A few details help the right team find this product and understand what happened." /><div className="mx-auto max-w-2xl"><div className="mb-8 flex items-center gap-2">{[1,2,3].map(n => <div key={n} className="flex flex-1 items-center gap-2"><span className={`grid h-7 w-7 place-items-center rounded-full text-xs font-bold ${step >= n ? 'bg-[#18B978] text-white' : 'bg-[#e6ede8] text-[#88958d]'}`}>{step > n ? <Check size={14} /> : n}</span><div className={`h-1 flex-1 rounded-full ${step > n ? 'bg-[#18B978]' : 'bg-[#e6ede8]'}`} /></div>)}</div><div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft md:p-8">{step === 1 && <><h2 className="text-lg font-bold text-[#20382b]">What is the problem?</h2><p className="mt-1 text-sm text-[#7c8a82]">Tell us the product and the category — officers route it from there.</p><div className="mt-6 space-y-4"><Field label="Product name" placeholder="As printed on the pack" value={productName} onChange={e => setProductName(e.target.value)} testId="input-complaint-product" /><label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Category</span><select value={category} onChange={e => setCategory(e.target.value)} className="focus-ring w-full rounded-lg border border-[#dbe6de] bg-white px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-complaint-category">{COMPLAINT_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}</select></label></div></>}{step === 2 && <><h2 className="text-lg font-bold text-[#20382b]">Where did you find it?</h2><p className="mt-1 text-sm text-[#7c8a82]">Batch and purchase details help officers trace the exact pack.</p><div className="mt-6 space-y-4"><Field label="Retailer and location" value={retailer} onChange={e => setRetailer(e.target.value)} testId="input-retailer" /><Field label="City" value={city} onChange={e => setCity(e.target.value)} testId="input-city" /><Field label="Batch / lot (if printed on the pack)" value={batchLot} onChange={e => setBatchLot(e.target.value)} testId="input-complaint-batch" /><Field label="Purchase location" value={purchasePlace} onChange={e => setPurchasePlace(e.target.value)} testId="input-complaint-place" /><Field label="Purchase date" type="text" placeholder="YYYY-MM-DD" value={purchaseDate} onChange={e => setPurchaseDate(e.target.value)} testId="input-complaint-date" /></div></>}{step === 3 && <><h2 className="text-lg font-bold text-[#20382b]">Describe and send</h2><p className="mt-1 text-sm text-[#7c8a82]">Review the details before sending your complaint.</p><div className="mt-6 space-y-3 text-sm"><div className="flex justify-between border-b border-[#edf1ee] pb-3"><span className="text-[#87948d]">Product</span><b className="text-right text-[#30473a]">{productName || 'Unnamed product'}</b></div><div className="flex justify-between border-b border-[#edf1ee] pb-3"><span className="text-[#87948d]">Category</span><b className="text-right text-[#30473a]">{category}</b></div><div className="flex justify-between border-b border-[#edf1ee] pb-3"><span className="text-[#87948d]">Where</span><b className="text-right text-[#30473a]">{purchasePlace || retailer || '—'}{city ? `, ${city}` : ''}</b></div><div className="flex justify-between border-b border-[#edf1ee] pb-3"><span className="text-[#87948d]">Priority</span><Badge tone="yellow">Medium</Badge></div></div><label className="mt-5 block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">What happened?</span><textarea value={description} onChange={e => setDescription(e.target.value)} rows={4} placeholder="Describe the problem as you saw it on the pack or shelf." className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-complaint-description" /></label><div className="mt-5 rounded-xl bg-[#fff8df] p-4 text-xs leading-relaxed text-[#78693a]"><Info size={14} className="mr-1 inline" />LegalAkshi provides evidence and routing. Final findings are made by the relevant authority.</div></>}{<div className="mt-8 flex justify-between border-t border-[#edf1ee] pt-5">{step > 1 ? <Button variant="ghost" onClick={() => setStep(step - 1)} data-testid="button-complaint-back"><ArrowLeft size={15} />Back</Button> : <span />}{step < 3 ? <Button onClick={() => setStep(step + 1)} data-testid={`button-complaint-next-${step}`}>Continue <ArrowRight size={15} /></Button> : <Button onClick={submit} disabled={submitting} data-testid="button-submit-complaint">{submitting ? 'Submitting…' : 'Submit complaint'} <Check size={16} /></Button>}{submitError && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-complaint-error">{submitError}</p>}</div>}</div></div></>;
}

function ComplaintsPage() {
  // Consumer complaints from GET /api/v1/complaints (Neon) — status here
  // reflects officer decisions, and survives refresh.
  const [complaints, setComplaints] = useState<BackendComplaint[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    setError('');
    api.listComplaints()
      .then(setComplaints)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, BackendComplaint>>({});
  const openDetail = (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (!details[id]) api.getComplaint(id).then(c => setDetails(d => ({ ...d, [id]: c }))).catch(() => undefined);
  };
  const tone = (s: string) => s === 'CLOSED' || s === 'RESOLVED' ? 'green' : 'yellow';
  const friendlyStatus = (s: string) => ({ SUBMITTED: 'Submitted', ACKNOWLEDGED: 'Submitted', UNDER_REVIEW: 'Under review', INSPECTION_SCHEDULED: 'Under review', INSPECTION_COMPLETED: 'Under review', ACTION_TAKEN: 'Under review', RESOLVED: 'Resolved', CLOSED: 'Rejected/closed' } as Record<string, string>)[s] ?? s;
  return <><PageTitle eyebrow="Your paper trail" title="My complaints" description="Stay in the loop on every report you've raised — statuses below are the actual backend lifecycle." action={<Link href="/complaint/new" className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white" data-testid="link-new-complaint"><Plus size={17} />New complaint</Link>} />
    {error && <div className="mb-5"><BackendError message={error} onRetry={load} /></div>}
    {isDemoMode() && <div className="mb-5"><DemoBanner text="Demo mode: sample complaints below illustrate each lifecycle stage. Real complaints appear above when the backend is reachable." /></div>}
    {isDemoMode() && <div className="mb-6 space-y-4" data-testid="demo-complaints">
      {DEMO_COMPLAINTS.map(c => <div key={c.complaint_id} className="rounded-xl border border-dashed border-[#cbdad0] bg-white p-5 shadow-soft md:p-6" data-testid={`demo-complaint-${c.complaint_id}`}>
        <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#18a86f]">{c.complaint_id}</span><Badge tone={tone(c.status)}>{friendlyStatus(c.status)} · {c.status}</Badge><DemoBadge testId={`demo-badge-${c.complaint_id}`} /></div>
        <h2 className="mt-3 font-bold text-[#20382b]">{c.product_name}</h2>
        <p className="mt-1 text-xs text-[#7f8e85]">{c.category} · {c.retailer} · {c.city}</p>
        <p className="mt-1 font-mono text-[10px] text-[#9aa69f]">filed {c.created_at.slice(0, 10)} · updated {c.updated_at.slice(0, 10)}</p>
        <p className="mt-2 text-xs leading-relaxed text-[#586a5f]">{c.description}</p>
      </div>)}
    </div>}
    {loading ? <p className="text-xs text-[#849188]">Loading complaints from backend…</p>
    : complaints.length === 0 && !isDemoMode() ? <EmptyState icon={ClipboardCheck} title="Nothing here yet" text="When you raise a complaint, its progress will appear here." />
    : <div className="space-y-4">{complaints.map(c => {
      const parsed = parseComplaintCategory(c.description || '');
      const extra = parseExtraLines(parsed.body);
      const detail = details[c.complaint_id];
      const timeline = detail?.timeline ?? c.timeline ?? [];
      return <div key={c.complaint_id} className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft md:p-6" data-testid={`complaint-${c.complaint_id.slice(0, 8)}`}>
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#18a86f]" data-testid="complaint-id">{c.complaint_id.slice(0, 8)}</span><Badge tone={tone(c.status)}><span data-testid="complaint-status">{friendlyStatus(c.status)} · {c.status}</span></Badge></div>
            <h2 className="mt-3 font-bold text-[#20382b]">{c.product_name}</h2>
            <p className="mt-1 text-xs text-[#7f8e85]">{parsed.category ? `${parsed.category} · ` : ''}{c.retailer} · {c.city}</p>
            <p className="mt-1 font-mono text-[10px] text-[#9aa69f]">filed {(c.created_at || '').slice(0, 10)} · updated {((detail?.updated_at ?? c.updated_at) || '').slice(0, 10) || '—'}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2"><Badge tone="yellow">{c.severity} priority</Badge><button onClick={() => openDetail(c.complaint_id)} className="rounded-lg border border-[#dce5df] px-3 py-2 text-xs font-bold text-[#426050] hover:border-[#18B978]" data-testid={`button-complaint-${c.complaint_id.slice(0, 8)}`}>{expanded === c.complaint_id ? 'Hide' : 'Details'}</button></div>
        </div>
        {expanded === c.complaint_id && <div className="mt-5 border-t border-[#edf1ee] pt-5">
          <div className="grid gap-3 text-xs sm:grid-cols-3">
            <div><p className="text-[10px] font-bold uppercase tracking-[.14em] text-[#87958c]">Batch / lot</p><p className="mt-1 font-bold text-[#30473a]">{extra.batch || 'Not available'}</p></div>
            <div><p className="text-[10px] font-bold uppercase tracking-[.14em] text-[#87958c]">Purchase location</p><p className="mt-1 font-bold text-[#30473a]">{extra.place || c.retailer || 'Not available'}</p></div>
            <div><p className="text-[10px] font-bold uppercase tracking-[.14em] text-[#87958c]">Purchase date</p><p className="mt-1 font-bold text-[#30473a]">{extra.date || 'Not available'}</p></div>
          </div>
          {(extra.body || detail?.description) && <p className="mt-3 whitespace-pre-wrap text-xs leading-relaxed text-[#586a5f]">{extra.body || detail?.description}</p>}
          {timeline.length > 0
            ? <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">{timeline.map((t, i) => <div key={`${t.event_type}-${i}`} className="flex flex-1 items-center gap-2"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><Check size={13} /></span><div><p className="text-[11px] font-bold text-[#4e6256]">{t.event_type}{t.to_status ? ` → ${t.to_status}` : ''}</p><p className="mt-0.5 text-[10px] text-[#9aa69f]">{(t.created_at || '').slice(0, 16)}</p></div>{i < timeline.length - 1 && <div className="hidden h-px flex-1 bg-[#dfe9e2] sm:block" />}</div>)}</div>
            : <p className="mt-3 text-[11px] text-[#9aa69f]">No timeline events yet — the report is with the intake team.</p>}
        </div>}
      </div>;
    })}</div>}</>;
}

type ReportRow = {
  inspection_id: string;
  product_id?: string;
  inspection_date?: string;
  business_name?: string;
  product_name: string;
  analyzed: boolean;
  score?: number;
  status?: string;
  verification?: string;
  findings?: { rule_id: string; status: string; requirement: string; explanation: string }[];
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function ReportsPage() {
  // Consumer-scoped: ONLY the caller's own complaint-derived reports from
  // GET /api/v1/consumer/reports (one bounded call). Unrelated officer
  // inspections are never listed here. Related inspections, outcomes and
  // PDF downloads resolve through the consumer's own complaints.
  const [rows, setRows] = useState<ConsumerReport[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const load = () => {
    setLoading(true);
    setError('');
    api.consumerReports()
      .then(res => setRows(res.reports))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  const downloadPdf = async (iid: string) => {
    setDownloading(iid);
    try {
      downloadBlob(await api.reportPdf(iid), `legalakshi-report-${iid.slice(0, 8)}.pdf`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(null);
    }
  };
  const tone = (s?: string) => s === 'VERIFIED' ? 'green' : s === 'NEEDS_REVIEW' ? 'yellow' : 'neutral';
  const friendlyStatus = (s: string) => ({ SUBMITTED: 'Submitted', ACKNOWLEDGED: 'Submitted', UNDER_REVIEW: 'Under review', INSPECTION_SCHEDULED: 'Under review', INSPECTION_COMPLETED: 'Under review', ACTION_TAKEN: 'Under review', RESOLVED: 'Resolved', CLOSED: 'Rejected/closed' } as Record<string, string>)[s] ?? s;
  return <><PageTitle eyebrow="Your records" title="My reports" description="Reports from your own complaints — related inspections, outcomes, and downloads where a final report exists." />
    {error && <div className="mb-5"><BackendError message={error} onRetry={load} /></div>}
    <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft"><div className="hidden grid-cols-[1fr_1fr_130px_110px_150px] gap-4 border-b border-[#edf1ee] bg-[#fbfcfb] px-5 py-3 text-[10px] font-bold uppercase tracking-[.13em] text-[#94a098] md:grid"><span>Product</span><span>Submitted</span><span>Status</span><span>Inspection</span><span>Actions</span></div>
    {loading ? <p className="p-6 text-xs text-[#849188]">Loading your reports…</p>
    : rows.length === 0 ? <div className="p-6"><EmptyState icon={FileCheck2} title="No reports yet" text="Report a problem from any verified product — your complaint reports will appear here with their status and outcome." /></div>
    : rows.map(r => {
      const related = r.related_inspection;
      const category = parseComplaintCategory(r.description || '').category;
      return <div key={r.complaint_id} className="grid gap-3 border-b border-[#edf1ee] px-5 py-4 last:border-0 md:grid-cols-[1fr_1fr_130px_110px_150px] md:items-center md:gap-4" data-testid={`report-${String(r.complaint_id).slice(0, 8)}`}>
        <div className="flex items-center gap-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[#f1e0bc] text-[#8a6a28]"><Box size={16} /></span><div className="min-w-0"><p className="truncate text-sm font-bold text-[#30473a]">{r.product_name}</p><p className="text-[11px] text-[#97a39b]">{category || 'Report'} · {String(r.complaint_id).slice(0, 8)}</p></div></div>
        <span className="font-mono text-[11px] text-[#75847b]">{String(r.date_submitted || '').slice(0, 10)}</span>
        <span><Badge tone={r.status === 'CLOSED' || r.status === 'RESOLVED' ? 'green' : 'yellow'}>{friendlyStatus(r.status)}</Badge></span>
        {related ? <span><Badge tone={tone(related.status)}>{related.status.replaceAll('_', ' ')}</Badge></span> : <span className="text-xs text-[#97a39b]">Submitted — awaiting review</span>}
        <div className="flex flex-wrap items-center gap-3">
          <Link href={`/reports/${r.complaint_id}`} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-report-${String(r.complaint_id).slice(0, 8)}`}>View details</Link>
          {related && <button type="button" onClick={() => setExpanded(expanded === r.complaint_id ? null : r.complaint_id)} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`button-report-more-${String(r.complaint_id).slice(0, 8)}`}>{expanded === r.complaint_id ? 'Less' : 'Outcome'}</button>}
          {related?.report_available && <button type="button" disabled={downloading === related.inspection_id} onClick={() => downloadPdf(related.inspection_id)} className="inline-flex items-center gap-1 text-xs font-bold text-[#426050] hover:text-[#12885c] disabled:opacity-50" data-testid={`button-download-report-${String(r.complaint_id).slice(0, 8)}`}><Download size={14} />{downloading === related.inspection_id ? 'PDF…' : 'Download'}</button>}
        </div>
        {expanded === r.complaint_id && related && <div className="md:col-span-5 rounded-lg bg-[#fbfcfb] p-4 text-xs text-[#68766f]"><p>Outcome: <b>{related.outcome.pass} passed · {related.outcome.fail} issue{related.outcome.fail === 1 ? '' : 's'} · {related.outcome.review} awaiting review</b> · inspected {String(related.inspection_date || '').slice(0, 10)}</p></div>}
      </div>;
    })}
    </div></>;
}

function OfficerReportsPage() {
  // Officer reports: inspection PDFs (backend report service), violation
  // rows linked to their parent inspection PDFs, and complaint summaries.
  // Nothing is generated client-side; empty states when there is no data.
  const auth = useOfficerAuth();
  const [tab, setTab] = useState<'inspections' | 'violations' | 'complaints'>('inspections');
  const [inspections, setInspections] = useState<any[]>([]);
  const [violations, setViolations] = useState<QueueItem[]>([]);
  const [complaints, setComplaints] = useState<BackendComplaint[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([
      api.listInspections().catch(() => []),
      api.officerQueue({}, auth).catch(() => []),
      api.listComplaints().catch(() => []),
    ])
      .then(([insp, viol, comp]) => {
        setInspections(insp as any[]);
        setViolations(viol as QueueItem[]);
        setComplaints(comp);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);
  const downloadPdf = async (iid: string) => {
    setDownloading(iid);
    try {
      downloadBlob(await api.reportPdf(iid), `legalakshi-official-report-${iid.slice(0, 8)}.pdf`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(null);
    }
  };
  return <><PageTitle eyebrow="Inspect, verify, enforce" title="Reports" description="Official reports generated by the backend from persisted analysis data." />
    {error && <div className="mb-5"><BackendError message={error} /></div>}
    <div className="mb-5 flex gap-2">{(['inspections', 'violations', 'complaints'] as const).map(t => <button key={t} onClick={() => setTab(t)} className={`rounded-full px-4 py-2 text-xs font-bold ${tab === t ? 'bg-[#dff5e9] text-[#12885c]' : 'bg-white text-[#7d8b83] hover:bg-[#eef5f0]'}`} data-testid={`button-reports-tab-${t}`}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
    {loading ? <p className="text-xs text-[#849188]">Loading reports from backend…</p>
    : tab === 'inspections' ? <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">{inspections.length === 0 ? <div className="p-6"><EmptyState icon={FileText} title="No inspection reports" text="Run a Scan & Inspect analysis first." /></div> : inspections.map((insp: any) => <div key={insp.inspection_id} className="flex flex-col gap-3 border-b border-[#edf1ee] px-5 py-4 last:border-0 md:flex-row md:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-bold text-[#30473a]">{insp.business_name || 'Inspection'}</p><p className="mt-1 font-mono text-[11px] text-[#97a39b]">{insp.inspection_id.slice(0, 8)} · {(insp.inspection_date || '').slice(0, 10)} · {insp.status}</p></div><button disabled={downloading === insp.inspection_id} onClick={() => downloadPdf(insp.inspection_id)} className="inline-flex items-center gap-1 text-xs font-bold text-[#426050] hover:text-[#12885c] disabled:opacity-50" data-testid={`button-pdf-${insp.inspection_id}`}><Download size={14} />{downloading === insp.inspection_id ? 'PDF…' : 'Official PDF'}</button></div>)}</div>
    : tab === 'violations' ? <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">{violations.length === 0 ? <div className="p-6"><EmptyState icon={ShieldCheck} title="No violation reports" text="Violations raised by the rule engine will appear here." /></div> : violations.map(v => <div key={v.violation_id} className="flex flex-col gap-3 border-b border-[#edf1ee] px-5 py-4 last:border-0 md:flex-row md:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-bold text-[#30473a]">{v.description || v.violation_type}</p><p className="mt-1 text-[11px] text-[#849188]">{v.product_name || ''} · {v.check_id || ''}</p></div><Badge tone={v.inspector_status === 'PENDING' ? 'yellow' : 'green'}>{v.inspector_status}</Badge><button disabled={downloading === v.inspection_id} onClick={() => downloadPdf(v.inspection_id)} className="inline-flex items-center gap-1 text-xs font-bold text-[#426050] hover:text-[#12885c] disabled:opacity-50" data-testid={`button-violation-pdf-${v.violation_id}`}><Download size={14} />Inspection PDF</button></div>)}</div>
    : <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">{complaints.length === 0 ? <div className="p-6"><EmptyState icon={ClipboardCheck} title="No complaint reports" text="Filed consumer complaints will appear here." /></div> : complaints.map(c => <div key={c.complaint_id} className="flex flex-col gap-3 border-b border-[#edf1ee] px-5 py-4 last:border-0 md:flex-row md:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-bold text-[#30473a]">{c.description || c.product_name}</p><p className="mt-1 text-[11px] text-[#849188]">{c.product_name} · {c.retailer} · {c.city}</p></div><Badge tone={c.status === 'CLOSED' ? 'green' : 'yellow'}>{c.status}</Badge><Link href={`/inspector/audit/${c.complaint_id}`} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`link-complaint-${c.complaint_id}`}>Open case</Link></div>)}</div>}
  </>;
}
function ProfilePage() {
  const { user } = useUser();
  const [name, setName] = useState(() => user?.fullName || user?.firstName || '');
  const [email, setEmail] = useState(() => user?.primaryEmailAddress?.emailAddress || '');
  const [city, setCity] = useState('Bengaluru');
  const [language, setLanguage] = useState('English');
  const [saved, setSaved] = useState(false);
  const initials = name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase() || 'NC';
  return <><PageTitle eyebrow="Account" title="Your profile" description="The details attached to your LegalAkshi workspace." action={saved ? <Badge tone="green"><Check size={12} />Saved</Badge> : undefined} /><div className="grid gap-5 lg:grid-cols-[.75fr_1.25fr]"><div className="rounded-2xl border border-[#dfe9e2] bg-[#173a2a] p-7 text-white"><span className="grid h-16 w-16 place-items-center rounded-full bg-[#d8f2e3] text-xl font-extrabold text-[#12885c]">{initials}</span><h2 className="mt-5 text-xl font-bold">{name || 'Your name'}</h2><p className="mt-1 text-sm text-[#a7c6b5]">Consumer account</p><div className="mt-8 border-t border-white/10 pt-5 text-xs text-[#a7c6b5]"><p>Member since</p><p className="mt-1 font-bold text-white">April 2024</p></div></div><div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft"><h2 className="font-bold text-[#20382b]">Personal details</h2><div className="mt-5 grid gap-4 sm:grid-cols-2"><Field label="Full name" value={name} testId="input-profile-name" onChange={e => { setName(e.target.value); setSaved(false); }} /><Field label="Email address" value={email} testId="input-profile-email" onChange={e => { setEmail(e.target.value); setSaved(false); }} /><Field label="City" value={city} testId="input-profile-city" onChange={e => { setCity(e.target.value); setSaved(false); }} /><Field label="Preferred language" value={language} testId="input-profile-language" onChange={e => { setLanguage(e.target.value); setSaved(false); }} /></div><Button className="mt-6" onClick={() => setSaved(true)} data-testid="button-save-profile">Save changes <Check size={15} /></Button></div></div></>;
}

function SettingsPage() {
  const [privacy, setPrivacy] = useState(true);
  const [updates, setUpdates] = useState(true);
  const [notif, setNotif] = useState(() => {
    try {
      return localStorage.getItem('legalakshi:notif-enabled') !== 'off';
    } catch {
      return true;
    }
  });
  const setNotifPref = (v: boolean) => {
    setNotif(v);
    try {
      localStorage.setItem('legalakshi:notif-enabled', v ? 'on' : 'off');
    } catch {
      /* ignore */
    }
  };
  return <><PageTitle eyebrow="Workspace preferences" title="Settings" description="Small choices that make LegalAkshi work better for you." /><div className="max-w-2xl space-y-5"><Setting title="Scan privacy" text="Keep uploaded label photos in your private workspace." on={privacy} setOn={setPrivacy} testId="switch-privacy" /><Setting title="Report updates" text="Get an email when a complaint changes status." on={updates} setOn={setUpdates} testId="switch-updates" /><Setting title="Backend notifications" text="Poll the backend notification inbox (violations, complaint updates, rule syncs) for the bell icon." on={notif} setOn={setNotifPref} testId="switch-notifications" /><div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><div className="flex items-center gap-3"><LifeBuoy size={19} className="text-[#18B978]" /><div><h2 className="font-bold text-[#20382b]">Need a hand?</h2><p className="mt-1 text-xs text-[#7d8b83]">Read how LegalAkshi evaluates a label, or contact our support team.</p></div></div><div className="mt-5 flex gap-3"><Button variant="secondary" onClick={() => undefined} data-testid="button-read-help">Read the guide</Button><Button variant="ghost" onClick={() => undefined} data-testid="button-contact-support">Contact support</Button></div></div></div></>;
}
function Setting({ title, text, on, setOn, testId }: { title: string; text: string; on: boolean; setOn: (v: boolean) => void; testId: string }) { return <div className="flex items-center justify-between gap-5 rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><div><h2 className="font-bold text-[#30473a]">{title}</h2><p className="mt-1 text-xs leading-relaxed text-[#7d8b83]">{text}</p></div><button onClick={() => setOn(!on)} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? 'bg-[#18B978]' : 'bg-[#cbd5cf]'}`} data-testid={testId}><span className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${on ? 'left-6' : 'left-1'}`} /></button></div>; }

type QueueItem = {
  violation_id: string; inspection_id: string; product_name?: string; business_name?: string;
  inspector_status: string; severity?: string; violation_type?: string;
  description?: string; created_at?: string; check_id?: string;
};

function useOfficerAuth() {
  // Backend dev headers only when the explicit demo switch is enabled;
  // production authenticates via the Clerk JWT bound in ApiAuthBinder.
  return api.devHeadersEnabled ? { devRole: 'officer' as const } : undefined;
}

function BackendError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-5 text-xs leading-relaxed text-[#8D3834]" data-testid="text-backend-error">
    <p className="font-bold">Backend unavailable</p><p className="mt-1">{message}</p>
    {onRetry && <button onClick={onRetry} className="mt-3 rounded-lg bg-white px-3 py-2 text-xs font-bold text-[#b43b37]" data-testid="button-retry-backend">Retry</button>}
  </div>;
}

function OfficerDashboard() {
  // Regulatory dashboard from live backend data only:
  // officer/stats (violations, resolution, rules, complaints), officer/queue
  // (pending + priority), inspections (totals), complaints (open).
  const name = useCurrentUserName('Officer');
  const auth = useOfficerAuth();
  // Independent sections: stats, enforcement queue, inspections and
  // complaints each fetch, load and fail on their own — one slow or
  // failing endpoint never blocks the rest of the dashboard.
  const [stats, setStats] = useState<OfficerStats | null>(null);
  const [statsError, setStatsError] = useState('');
  const [statsLoading, setStatsLoading] = useState(true);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [queueError, setQueueError] = useState('');
  const [queueLoading, setQueueLoading] = useState(true);
  const [inspections, setInspections] = useState<any[]>([]);
  const [inspectionsError, setInspectionsError] = useState('');
  const [inspectionsLoading, setInspectionsLoading] = useState(true);
  const [complaints, setComplaints] = useState<BackendComplaint[]>([]);
  const [complaintsError, setComplaintsError] = useState('');
  const [complaintsLoading, setComplaintsLoading] = useState(true);
  const errText = (e: unknown) => e instanceof Error ? e.message : String(e);
  const loadStats = () => {
    setStatsLoading(true);
    setStatsError('');
    api.officerStats(auth).then(setStats).catch(e => setStatsError(errText(e))).finally(() => setStatsLoading(false));
  };
  const loadQueue = () => {
    setQueueLoading(true);
    setQueueError('');
    api.officerQueue({}, auth).then(q => setQueue(q as QueueItem[])).catch(e => setQueueError(errText(e))).finally(() => setQueueLoading(false));
  };
  const loadInspections = () => {
    setInspectionsLoading(true);
    setInspectionsError('');
    api.listInspections().then(i => setInspections(i as any[])).catch(e => setInspectionsError(errText(e))).finally(() => setInspectionsLoading(false));
  };
  const loadComplaints = () => {
    setComplaintsLoading(true);
    setComplaintsError('');
    api.listComplaints().then(setComplaints).catch(e => setComplaintsError(errText(e))).finally(() => setComplaintsLoading(false));
  };
  const load = () => { loadStats(); loadQueue(); loadInspections(); loadComplaints(); };
  useEffect(load, []);
  const needsAttention = queue.filter(c => c.inspector_status === 'REQUIRES_REVIEW').length;
  const priority = queue.filter(c => c.severity === 'HIGH' && c.inspector_status === 'PENDING').slice(0, 4);
  const recent = [
    ...queue.slice(0, 4).map(c => ({ key: c.violation_id, label: c.description || c.violation_type || 'Violation', sub: `${c.product_name || ''} · ${c.inspector_status}`, href: `/inspector/audit/${c.violation_id}` })),
    ...complaints.slice(0, 3).map(c => ({ key: c.complaint_id, label: c.description || c.product_name, sub: `${c.retailer || ''} · ${c.status}`, href: `/inspector/audit/${c.complaint_id}` })),
  ].slice(0, 6);
  const sectionError = (message: string, onRetry: () => void) => <div className="rounded-xl border border-[#f0c9c6] bg-[#fce6e4] p-4 text-xs leading-relaxed text-[#8D3834]"><p className="font-bold">Temporarily unavailable</p><p className="mt-1 break-words">{message}</p><button onClick={onRetry} className="mt-2 rounded-lg bg-white px-3 py-1.5 text-xs font-bold text-[#b43b37]">Retry</button></div>;
  return <><PageTitle eyebrow="Inspector workspace" title={`${getGreeting()}, ${name}.`} description="Review inspections, investigate complaints, and act on compliance findings." action={<Button variant="secondary" onClick={load} data-testid="button-refresh-dashboard"><History size={16} />Refresh</Button>} />
    <div data-testid="dashboard-stats">
      {statsError && <div className="mb-4">{sectionError(`Stats are temporarily unavailable. ${statsError}`, loadStats)}</div>}
      {statsLoading && !stats
        ? <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6"><StatSkeleton label="Inspections" /><StatSkeleton label="Awaiting review" /><StatSkeleton label="Confirmed" /><StatSkeleton label="Needs attention" /><StatSkeleton label="Open complaints" /><StatSkeleton label="Active rules" /></div>
        : <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Inspections" value={inspectionsLoading && inspections.length === 0 ? '…' : String(inspections.length)} note="persisted total" icon={ScanLine} tone="green" />
          <Stat label="Awaiting review" value={stats ? String(stats.awaiting_review) : '—'} note={stats ? `${stats.high_priority} high priority` : 'unavailable'} icon={ClipboardCheck} tone="yellow" />
          <Stat label="Confirmed" value={stats ? String(stats.action_taken) : '—'} note={stats ? `${Math.round(stats.resolution_rate * 100)}% resolution` : 'unavailable'} icon={CheckCircle2} tone="green" />
          <Stat label="Needs attention" value={String(needsAttention)} note="requires review" icon={CircleAlert} tone="yellow" />
          <Stat label="Open complaints" value={stats ? String(stats.open_complaints) : '—'} note={stats ? `${stats.total_complaints} total` : 'unavailable'} icon={FileText} tone="blue" />
          <Stat label="Active rules" value={stats ? String(stats.active_rules) : '—'} note={stats ? 'in force now' : 'unavailable'} icon={BookOpen} tone="blue" />
        </div>}
      {inspectionsError && inspections.length === 0 && <div className="mt-4">{sectionError(`Inspection totals are temporarily unavailable. ${inspectionsError}`, loadInspections)}</div>}
    </div>
      <section className="mt-8 grid gap-5 lg:grid-cols-[1.25fr_.75fr]">
        <div className="space-y-5">
          <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft" data-testid="dashboard-queue">
            <div className="flex items-center justify-between"><div><h2 className="font-bold text-[#20382b]">Priority cases</h2><p className="mt-1 text-xs text-[#85938b]">High-severity pending verifications</p></div><Link href="/inspector/enforcement" className="text-xs font-bold text-[#12885c]" data-testid="link-officer-queue">View queue <ChevronRight size={13} className="inline" /></Link></div>
            {queueLoading && queue.length === 0 ? <p className="py-4 text-center text-xs text-[#849188]">Loading enforcement queue…</p>
              : queueError && queue.length === 0 ? sectionError(`Enforcement queue is temporarily unavailable. ${queueError}`, loadQueue)
                : <div className="mt-5 space-y-1">{priority.length === 0 ? <p className="py-4 text-center text-xs text-[#849188]">No high-priority cases pending.</p> : priority.map(c => <Link href={`/inspector/audit/${c.violation_id}`} key={c.violation_id} className="flex items-center gap-3 rounded-lg p-3 hover:bg-[#f4f8f5]" data-testid={`link-queue-item-${c.violation_id}`}><span className="grid h-9 w-9 place-items-center rounded-lg bg-[#fce6e4] text-[#b43b37]"><CircleAlert size={16} /></span><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-[#30473a]">{c.description || c.violation_type || c.violation_id}</p><p className="mt-1 text-[11px] text-[#92a098]">{c.product_name || ''}{c.business_name ? ` · ${c.business_name}` : ''}</p></div><Badge tone="red">HIGH</Badge></Link>)}</div>}
          </div>
          <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft" data-testid="dashboard-activity">
            <h2 className="font-bold text-[#20382b]">Recent activity</h2>
            <p className="mt-1 text-xs text-[#85938b]">Latest violations and complaints from the backend</p>
            {(queueLoading || complaintsLoading) && recent.length === 0 ? <p className="py-4 text-center text-xs text-[#849188]">Loading activity…</p>
              : <><div className="mt-4 space-y-1">{recent.length === 0 ? <p className="py-4 text-center text-xs text-[#849188]">No activity yet.</p> : recent.map(r => <Link href={r.href} key={r.key} className="flex items-center gap-3 rounded-lg p-2.5 hover:bg-[#f4f8f5]"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#18B978]" /><div className="min-w-0"><p className="truncate text-xs font-bold text-[#30473a]">{r.label}</p><p className="text-[11px] text-[#92a098]">{r.sub}</p></div></Link>)}</div>
              {queueError && <p className="mt-2 text-[11px] text-[#a27812]">Enforcement items unavailable — <button onClick={loadQueue} className="font-bold underline">retry</button>.</p>}
              {complaintsError && <p className="mt-2 text-[11px] text-[#a27812]">Complaint items unavailable — <button onClick={loadComplaints} className="font-bold underline">retry</button>.</p>}</>}
          </div>
        </div>
        <div className="space-y-5">
          <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft">
            <h2 className="font-bold text-[#20382b]">Quick actions</h2>
            <div className="mt-4 grid gap-2">
              <Link href="/inspector/scan" className="flex items-center gap-3 rounded-lg bg-[#18B978] px-4 py-3 text-sm font-bold text-white hover:bg-[#119e67]" data-testid="link-action-scan"><ScanLine size={17} />Scan &amp; Inspect</Link>
              <Link href="/inspector/inspections" className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-action-inspections"><Box size={17} />Review inspections</Link>
              <Link href="/inspector/complaints" className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-action-complaints"><ClipboardCheck size={17} />View complaints</Link>
              <Link href="/inspector/enforcement" className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-action-enforcement"><ShieldCheck size={17} />Open enforcement queue</Link>
              <Link href="/inspector/reports" className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-action-reports"><FileText size={17} />Generate Report</Link>
              <Link href="/inspector/rules" className="flex items-center gap-3 rounded-lg border border-[#dce5df] px-4 py-3 text-sm font-bold text-[#426050] hover:border-[#18B978]" data-testid="link-action-rules"><BookOpen size={17} />Rule Library</Link>
            </div>
          </div>
          <div className="rounded-2xl border border-[#e1eae4] bg-white p-6 shadow-soft" data-testid="dashboard-resolution">
            <h2 className="font-bold text-[#20382b]">Resolution</h2>
            <p className="mt-1 text-xs text-[#85938b]">Computed from persisted verifications</p>
            {statsLoading && !stats ? <p className="py-4 text-center text-xs text-[#849188]">Loading resolution…</p>
              : statsError && !stats ? sectionError(`Resolution is temporarily unavailable. ${statsError}`, loadStats)
                : stats ? <div className="mt-5 space-y-3">
                  <div className="flex items-center justify-between text-sm"><span className="text-[#64736b]">Total violations</span><b className="text-[#20382b]">{stats.total_violations}</b></div>
                  <div className="flex items-center justify-between text-sm"><span className="text-[#64736b]">Resolved</span><b className="text-[#20382b]">{stats.resolved}</b></div>
                  <div className="flex items-center justify-between text-sm"><span className="text-[#64736b]">Avg. response</span><b className="text-[#20382b]">{stats.avg_response_days === null ? '—' : `${stats.avg_response_days} days`}</b></div>
                </div> : null}
          </div>
        </div>
      </section>
  </>;
}

function RulesPage() {
  // Rule library reads PostgreSQL via GET /api/v1/rules. Detail shows the
  // full version lineage (ACTIVE/SUPERSEDED/DRAFT/NOT_YET_IN_FORCE) with
  // provenance from GET /api/v1/rules/{check_id}. Officers REVIEW;
  // only admins can run the authoritative sync (preview → apply).
  const { role } = useWorkspaceRole();
  const auth = useOfficerAuth();
  const [rules, setRules] = useState<BackendRule[]>([]);
  const [query, setQuery] = useState('');
  const [checkType, setCheckType] = useState('');
  const [mandatoryOnly, setMandatoryOnly] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [syncPreview, setSyncPreview] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  useEffect(() => {
    api.rules()
      .then(setRules)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);
  const openDetail = (id: string) => {
    setDetailId(id);
    setDetail(null);
    api.ruleDetail(id)
      .then(setDetail)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  const previewSync = async () => {
    setSyncing(true);
    setError('');
    try {
      setSyncPreview(await api.syncPreview(auth));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };
  const applySync = async () => {
    setSyncing(true);
    try {
      const res = await api.syncApply(auth);
      setSyncPreview(res);
      setRules(await api.rules());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };
  const checkTypes = [...new Set(rules.map(r => r.check_type))].sort();
  const filtered = rules.filter(r =>
    `${r.title} ${r.source_reference}`.toLowerCase().includes(query.toLowerCase()) &&
    (!checkType || r.check_type === checkType) &&
    (!mandatoryOnly || r.mandatory_default));
  const statusTone = (s: string) => s === 'IN_FORCE' ? 'green' : s === 'SUPERSEDED' ? 'neutral' : 'yellow';
  return <><PageTitle eyebrow="Regulatory library" title="Rules & checks" description="The live rule set behind every LegalAkshi report — read from PostgreSQL, never typed in by hand." action={role === 'admin' ? <Button onClick={previewSync} disabled={syncing} data-testid="button-sync-rules"><Download size={16} />{syncing ? 'Working…' : 'Sync authoritative rules'}</Button> : undefined} />
    {error && <div className="mb-5"><BackendError message={error} /></div>}
    {syncPreview && <div className="mb-5 rounded-xl border border-[#cfeedd] bg-[#eaf8f1] p-5 text-xs leading-relaxed text-[#4c7761]" data-testid="panel-sync-preview"><p className="font-bold text-[#276047]">Sync preview (dry run — nothing applied yet)</p><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(syncPreview, null, 2).slice(0, 2000)}</pre><div className="mt-3"><Button onClick={applySync} disabled={syncing} data-testid="button-sync-apply">Apply sync (confirm)</Button></div></div>}
    <div className="mb-5 flex flex-col gap-3 sm:flex-row"><div className="relative flex-1"><Search size={16} className="absolute left-3 top-3 text-[#97a49d]" /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search rules or clauses" className="focus-ring w-full rounded-lg border border-[#dce7df] bg-white py-2.5 pl-9 pr-3 text-sm outline-none" data-testid="input-search-rules" /></div><select value={checkType} onChange={e => setCheckType(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-filter-check-type"><option value="">All check types</option>{checkTypes.map(t => <option key={t} value={t}>{t}</option>)}</select><label className="flex items-center gap-2 rounded-lg border border-[#dce7df] bg-white px-3 text-xs font-bold text-[#426050]"><input type="checkbox" checked={mandatoryOnly} onChange={e => setMandatoryOnly(e.target.checked)} data-testid="input-filter-mandatory" />Mandatory only</label><Button variant="secondary" onClick={() => { setQuery(''); setCheckType(''); setMandatoryOnly(false); }} data-testid="button-filter-rules"><Filter size={15} />Clear filter</Button></div>
    {loading ? <p className="text-xs text-[#849188]">Loading rules from backend…</p> : <div className="space-y-3">{filtered.map(rule => <div key={rule.rule_id} className="rounded-xl border border-[#e2eae4] bg-white p-5 shadow-soft"><div className="flex flex-col justify-between gap-3 md:flex-row md:items-start"><div className="flex gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-[#e2f7eb] text-[#12885c]"><BookOpen size={18} /></span><div><div className="flex flex-wrap items-center gap-2"><h2 className="font-bold text-[#20382b]">{rule.title}</h2><Badge tone="green">{rule.check_type}</Badge></div><p className="mt-1 font-mono text-[11px] text-[#18a86f]">{rule.source_reference} · field: {rule.field}</p><p className="mt-1 text-xs text-[#7d8b83]">{rule.requirement}</p></div></div><Button variant="secondary" onClick={() => openDetail(rule.rule_id)} data-testid={`button-rule-${rule.rule_id}`}>Versions</Button></div>
      {detailId === rule.rule_id && <div className="mt-4 rounded-lg bg-[#fbfcfb] p-4" data-testid={`panel-rule-${rule.rule_id}`}>{detail ? <><p className="text-xs font-bold text-[#42554a]">Version history ({detail.versions?.length ?? 0})</p><div className="mt-2 space-y-2">{(detail.versions ?? []).map((v: any) => <div key={v.rule_version_id} className="flex flex-wrap items-center gap-2 text-xs text-[#586a5f]"><Badge tone={statusTone(v.status)}>{v.status}</Badge><span className="font-mono">{v.effective_from}{v.effective_to ? ` → ${v.effective_to}` : ' → present'}</span><span className="text-[#849188]">{v.requirement}</span></div>)}</div><p className="mt-3 text-xs font-bold text-[#42554a]">Applicability</p><div className="mt-1 space-y-1">{(detail.applicability ?? []).map((a: any, i: number) => <p key={i} className="text-xs text-[#586a5f]"><b>{a.applicable_result}</b> — {a.reason}</p>)}</div></> : <p className="text-xs text-[#849188]">Loading version history…</p>}</div>}
    </div>)}</div>}
  </>;
}

function InspectorComplaints() {
  // Dedicated officer Complaints page (B4) — consumer-submitted complaints
  // from the existing complaint backend. Distinct from the Enforcement
  // Queue (actionable violations); related items link across.
  const FILTERS: { label: string; status: string | null }[] = [
    { label: 'All', status: null },
    { label: 'Submitted', status: 'SUBMITTED' },
    { label: 'Under review', status: 'UNDER_REVIEW' },
    { label: 'Action taken', status: 'ACTION_TAKEN' },
    { label: 'Closed', status: 'CLOSED' },
  ];
  const [filter, setFilter] = useState('All');
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [complaints, setComplaints] = useState<BackendComplaint[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    setError('');
    api.listComplaints()
      .then(setComplaints)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  const active = FILTERS.find(f => f.label === filter);
  const categories = [...new Set(complaints.map(c => parseComplaintCategory(c.description || '').category).filter(Boolean))] as string[];
  const visible = complaints.filter(c => {
    if (active?.status && c.status !== active.status) return false;
    if (category && parseComplaintCategory(c.description || '').category !== category) return false;
    const date = (c.created_at || '').slice(0, 10);
    if (from && date < from) return false;
    if (to && date > to) return false;
    if (search.trim()) {
      const hay = [c.complaint_id, c.product_name, c.description, c.retailer, c.city].join(' ').toLowerCase();
      if (!hay.includes(search.trim().toLowerCase())) return false;
    }
    return true;
  });
  return <><PageTitle eyebrow="Inspector workspace" title="Complaints" description="Consumer-submitted complaints from the existing complaint backend — investigate, then act. Enforcement cases live in the Enforcement Queue." action={<Button variant="secondary" onClick={load} data-testid="button-refresh-queue"><History size={16} />Refresh</Button>} />
    {error && <div className="mb-5"><BackendError message={error} onRetry={load} /></div>}
    <div className="mb-4 flex flex-col gap-3 lg:flex-row">
      <div className="relative flex-1"><Search size={16} className="absolute left-3 top-3 text-[#97a49d]" /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search complaint ID, product, batch/lot…" className="focus-ring w-full rounded-lg border border-[#dce7df] bg-white py-2.5 pl-9 pr-3 text-sm outline-none" data-testid="input-complaint-search" /></div>
      <select value={category} onChange={e => setCategory(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-complaint-category"><option value="">All categories</option>{categories.map(c => <option key={c} value={c}>{c}</option>)}{COMPLAINT_CATEGORIES.filter(c => !categories.includes(c)).map(c => <option key={c} value={c}>{c}</option>)}</select>
      <input type="date" value={from} onChange={e => setFrom(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-complaint-from" />
      <input type="date" value={to} onChange={e => setTo(e.target.value)} className="rounded-lg border border-[#dce7df] bg-white px-3 py-2.5 text-xs font-bold text-[#426050]" data-testid="input-complaint-to" />
    </div>
    <div className="mb-5 flex gap-2 overflow-auto pb-1">{FILTERS.map(f => <button key={f.label} onClick={() => setFilter(f.label)} className={`whitespace-nowrap rounded-full px-3 py-2 text-xs font-bold ${filter === f.label ? 'bg-[#dff5e9] text-[#12885c]' : 'bg-white text-[#7d8b83] hover:bg-[#eef5f0]'}`} data-testid={`button-filter-${f.label.toLowerCase().replaceAll(' ', '-')}`}>{f.label}</button>)}</div>
    <div className="overflow-hidden rounded-xl border border-[#e2eae4] bg-white shadow-soft">{loading ? <p className="p-6 text-xs text-[#849188]">Loading complaints from backend…</p> : visible.length === 0 ? <EmptyState icon={ClipboardCheck} title="No data yet" text="No complaints match these filters." /> : visible.map(c => {
      const parsed = parseComplaintCategory(c.description || '');
      return <div key={c.complaint_id} className="flex flex-col gap-4 border-b border-[#edf1ee] p-5 last:border-0 md:flex-row md:items-center"><div className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${c.severity === 'High' ? 'bg-[#fce6e4] text-[#b43b37]' : 'bg-[#fff4d4] text-[#a27812]'}`}><CircleAlert size={18} /></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#18a86f]">{c.complaint_id.slice(0, 8)}</span><Badge tone={c.status === 'CLOSED' || c.status === 'RESOLVED' ? 'green' : 'yellow'}>{c.status}</Badge>{parsed.category && <Badge tone="neutral">{parsed.category}</Badge>}</div><p className="mt-2 truncate text-sm font-bold text-[#30473a]">{c.product_name}</p><p className="mt-1 text-xs text-[#849188]">{c.retailer} · {c.city}</p><p className="mt-1 font-mono text-[10px] text-[#9aa69f]">filed {(c.created_at || '').slice(0, 10)} · updated {(c.updated_at || '').slice(0, 10) || '—'}</p></div><Link href={`/inspector/complaints/${c.complaint_id}`} className="inline-flex items-center justify-center gap-1 rounded-lg border border-[#dce7df] px-3 py-2 text-xs font-bold text-[#426050] hover:border-[#18B978] hover:text-[#12885c]" data-testid={`link-complaint-${c.complaint_id.slice(0, 8)}`}>Open complaint <ChevronRight size={14} /></Link></div>;
    })}
    </div></>;
}

function AuditPage() {
  // Case detail works for EITHER a consumer complaint (/complaints/{id},
  // with lifecycle timeline) OR an engine violation (/officer/cases/{id},
  // with findings + rule provenance + audit trail). Everything is fetched
  // from the backend; decisions POST to the backend and the page refetches
  // to prove persistence.
  const params = useParams<{ id: string }>();
  const id = params.id ?? '';
  const auth = useOfficerAuth();
  const [mode, setMode] = useState<'complaint' | 'violation' | null>(null);
  const [complaint, setComplaint] = useState<BackendComplaint | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const load = () => {
    setLoading(true);
    setError('');
    api.getComplaint(id)
      .then(c => { setComplaint(c); setDetail(null); setMode('complaint'); })
      .catch(() =>
        api.officerCase(id, auth)
          .then(d => { setDetail(d); setComplaint(null); setMode('violation'); })
          .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e))),
      )
      .finally(() => setLoading(false));
  };
  useEffect(load, [id]);
  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    setSaving(true);
    try {
      if (mode === 'complaint') {
        const map: Record<string, string> = { under_review: 'UNDER_REVIEW', action_taken: 'ACTION_TAKEN', close: 'CLOSED' };
        await api.transitionComplaint(id, map[action] ?? action.toUpperCase(), note, auth);
      } else {
        await api.officerAction(id, { action, notes: note, ...extra }, auth);
      }
      setNote('');
      load(); // refetch persisted state
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  if (loading) return <p className="text-sm font-semibold text-[#426050]">Loading case from backend…</p>;
  if (error || (!complaint && !detail)) return <div><PageTitle eyebrow="Official verification" title="Case not found" description="The backend has no record with this ID." /><BackendError message={error || 'Unknown case ID.'} /></div>;
  const title = complaint ? complaint.product_name : detail?.product?.product_name || detail?.violation?.violation_type || 'Case';
  const sub = complaint
    ? `${complaint.retailer || ''}${complaint.city ? `, ${complaint.city}` : ''}`
    : `${detail?.inspection?.business_name || ''} · ${detail?.product?.category || ''}`;
  const status = complaint ? complaint.status : detail?.violation?.inspector_status;
  const timeline: any[] = complaint ? complaint.timeline ?? [] : detail?.audit ?? [];
  const findings: any[] = detail?.findings ?? [];
  const rules: any[] = detail?.rules ?? [];
  return <><PageTitle eyebrow="Official verification" title={complaint ? complaint.complaint_id.slice(0, 8) : title} description={`${title} · ${sub}`} action={<Badge tone={status === 'CONFIRMED' || status === 'CLOSED' ? 'green' : 'yellow'}>{status}</Badge>} />
    {error && <div className="mb-5"><BackendError message={error} /></div>}
    <div className="grid gap-6 lg:grid-cols-[1.15fr_.85fr]"><div className="space-y-5">
      <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><div className="flex items-center justify-between"><div><h2 className="font-bold text-[#20382b]">Evidence submitted</h2><p className="mt-1 text-xs text-[#849188]">{mode === 'complaint' ? 'Consumer report' : 'Inspection evidence'} · persisted in PostgreSQL</p></div><Badge tone="green"><FileCheck2 size={12} />backend</Badge></div>
        <div className="mt-5 space-y-2">{detail?.evidence && detail.evidence.length > 0
          ? detail.evidence.map((e: any, i: number) => <div key={e.evidence_id || i} className="rounded-lg bg-[#fbfcfb] p-3 text-xs text-[#586a5f]"><span className="font-mono font-bold text-[#18a86f]">{e.evidence_type}</span>{e.description ? ` · ${e.description}` : ''}{e.file_path ? ` · ${e.file_path}` : ''}</div>)
          : <p className="text-xs text-[#849188]">{mode === 'complaint' ? 'No file attachments — complaint text is the evidence.' : 'No inspection evidence rows stored.'}</p>}</div></div>
      <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><div className="flex items-center gap-2"><CircleAlert size={18} className="text-[#d9534f]" /><h2 className="font-bold text-[#20382b]">Reported findings</h2></div><div className="mt-4 space-y-3">
        {mode === 'complaint'
          ? <div className="rounded-lg bg-[#fff7df] p-4"><p className="text-sm font-bold text-[#6e5a27]">{complaint?.description || complaint?.product_name}</p><p className="mt-1 text-xs leading-relaxed text-[#867747]">Severity: {complaint?.severity} · Status: {complaint?.status}</p></div>
          : findings.length === 0
            ? <p className="text-xs text-[#849188]">No compliance findings stored for this case.</p>
            : findings.map((f: any) => {
              const fkey = f.compliance_result_id || f.rule_version_id || f.check_id;
              const open = evidenceFor === fkey;
              return <div key={fkey} className="rounded-lg bg-[#fff7df] p-4" data-testid={`finding-${f.check_id}`}>
                <div className="flex items-center justify-between gap-2"><p className="font-mono text-[11px] font-bold text-[#6e5a27]">{f.check_id}</p><Badge tone={f.result === 'PASS' ? 'green' : f.result === 'FAIL' ? 'red' : 'yellow'}>{f.result}</Badge></div>
                <p className="mt-1 text-sm font-bold text-[#6e5a27]">{f.requirement}</p>
                <p className="mt-1 text-xs leading-relaxed text-[#867747]">Observed: <b>{f.detected_value ?? f.evidence?.detected_value ?? '—'}</b></p>
                <p className="mt-1 text-xs leading-relaxed text-[#867747]">{f.explanation}</p>
                <button onClick={() => setEvidenceFor(open ? null : fkey)} className="mt-2 text-xs font-bold text-[#12885c] hover:underline" data-testid={`button-evidence-${f.check_id}`}>{open ? 'Hide evidence' : 'View evidence'}</button>
                {open && <div className="mt-2 rounded-lg bg-white/70 p-3 text-xs leading-relaxed text-[#6e5a27]" data-testid={`panel-evidence-${f.check_id}`}>
                  <p><b>What was checked:</b> {f.requirement || '—'}</p>
                  <p className="mt-1"><b>Observed value:</b> {f.detected_value ?? f.evidence?.detected_value ?? 'Not available'}</p>
                  <p className="mt-1"><b>Confidence:</b> {f.confidence ?? f.evidence?.confidence ?? 'Not available'}</p>
                  <p className="mt-1"><b>Applicable rule:</b> Rule {f.rule_number || '—'}({f.sub_rule || ''}){f.clause || ''}{f.source_title ? ` · ${f.source_title}` : ''}{f.authenticity_status ? ` · ${f.authenticity_status}` : ''}{f.effective_from ? ` · effective ${String(f.effective_from).slice(0, 10)}` : ''}</p>
                  <p className="mt-1"><b>Officer action/status:</b> {detail?.violation?.inspector_status || 'No decision recorded yet'}</p>
                  <p className="mt-1 text-[#a08c5a]">Source package evidence (OCR values, photos, declarations) is listed under “Evidence submitted” above.</p>
                </div>}
              </div>;
            })}
      </div></div>
      {rules.length > 0 && <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><h2 className="font-bold text-[#20382b]">Applicable rules</h2><div className="mt-3 space-y-2">{rules.map((r: any) => <div key={r.check?.check_id} className="text-xs text-[#586a5f]"><span className="font-mono font-bold text-[#18a86f]">{r.check?.check_id}</span> · {r.check?.title} <span className="text-[#849188]">({r.check?.source_reference})</span></div>)}</div></div>}
      <div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><h2 className="font-bold text-[#20382b]">Audit trail</h2><div className="mt-3 space-y-2">{timeline.length === 0 ? <p className="text-xs text-[#849188]">No audit rows yet.</p> : timeline.map((t: any, i: number) => <div key={i} className="flex items-center gap-2 text-xs text-[#586a5f]"><span className="grid h-5 w-5 place-items-center rounded-full bg-[#dff5e9] text-[#12885c]"><Check size={11} /></span><span><b>{t.event_type || t.action}</b>{t.to_status ? ` → ${t.to_status}` : ''}{t.note ? ` · ${t.note}` : ''}</span><span className="ml-auto font-mono text-[10px] text-[#9aa69f]">{(t.created_at || '').slice(0, 16)}</span></div>)}</div></div>
    </div><div className="space-y-5">{mode === 'violation' && detail?.violation && <div className="rounded-xl border border-[#d9efe4] bg-[#eaf8f1] p-6" data-testid="panel-decision-record"><h2 className="font-bold text-[#20382b]">Decision record</h2><div className="mt-3 space-y-2 text-xs text-[#4c7761]"><div className="flex items-center justify-between"><span>Status</span><Badge tone={detail.violation.inspector_status === 'CONFIRMED' ? 'green' : detail.violation.inspector_status === 'PENDING' ? 'yellow' : 'neutral'}>{detail.violation.inspector_status}</Badge></div><p>Decided by: <b>{detail.violation.inspector_id || 'Not configured'}</b></p><p>Verified: <b>{detail.violation.verification_date || 'Not configured'}</b></p><p>Remarks: <b>{detail.violation.inspector_remarks || 'Not configured'}</b></p></div></div>}<div className="rounded-xl border border-[#e2eae4] bg-white p-6 shadow-soft"><h2 className="font-bold text-[#20382b]">Record your action</h2><p className="mt-1 text-xs text-[#849188]">Writes to Neon and refetches — refresh-safe.</p>
      <label className="mt-4 block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Internal notes</span><textarea value={note} onChange={e => setNote(e.target.value)} className="focus-ring h-20 w-full resize-none rounded-lg border border-[#dbe6de] px-3 py-2 text-sm outline-none focus:border-[#18B978]" data-testid="input-case-note" /></label>
      <div className="mt-4 grid gap-2">{mode === 'complaint'
        ? [['under_review', 'Mark under review'], ['action_taken', 'Record action taken'], ['close', 'Close complaint']].map(([a, label]) => <Button key={a} variant="secondary" disabled={saving} onClick={() => act(a)} data-testid={`button-case-${a}`}>{label}</Button>)
        : [['confirm', 'Confirm violation'], ['reject', 'Reject finding'], ['request_evidence', 'Request evidence'], ['under_review', 'Mark under review']].map(([a, label]) => <Button key={a} variant={a === 'confirm' ? 'primary' : 'secondary'} disabled={saving} onClick={() => act(a)} data-testid={`button-case-${a}`}>{label}</Button>)}
      </div></div></div></div></>;
}

function OfficerProfilePage() {
  // Functional route backed by GET /api/v1/officer/profile: shows the
  // authenticated officer; unavailable fields render "Not configured".
  const auth = useOfficerAuth();
  const { user } = useUser();
  const [profile, setProfile] = useState<any>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api.officerProfile(auth)
      .then(setProfile)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  const field = (label: string, value: unknown) => <div><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">{label}</p><p className="mt-1 text-sm font-bold text-[#20382b]">{value === null || value === undefined || value === '' ? 'Not configured' : String(value)}</p></div>;
  return <><PageTitle eyebrow="Regulatory workspace" title="Officer profile" description="Identity as known by the backend — never self-assigned." />
    {error && <BackendError message={error} />}
    <div className="grid gap-5 lg:grid-cols-[.75fr_1.25fr]">
      <div className="rounded-2xl border border-[#dfe9e2] bg-[#173a2a] p-7 text-white">
        <span className="grid h-16 w-16 place-items-center rounded-full bg-[#d8f2e3] text-xl font-extrabold text-[#12885c]">{(user?.firstName?.[0] || profile?.name?.[0] || 'O').toUpperCase()}</span>
        <h2 className="mt-5 text-xl font-bold">{profile?.name || user?.fullName || 'Officer'}</h2>
        <p className="mt-1 text-sm text-[#a7c6b5]">{profile?.role || 'officer'} · via {profile?.role_source || 'session'}</p>
      </div>
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        {profile ? <div className="grid gap-5 sm:grid-cols-2">
          {field('Name', profile.name || user?.fullName)}
          {field('Officer ID', profile.user_id)}
          {field('Department', profile.department)}
          {field('Designation', profile.designation)}
          {field('Jurisdiction', profile.jurisdiction)}
          {field('Email', profile.email || user?.primaryEmailAddress?.emailAddress)}
          {field('Role', profile.role)}
          {field('Permissions', (profile.permissions || []).join(', '))}
        </div> : <p className="text-xs text-[#849188]">Loading profile…</p>}
        {profile && <p className="mt-5 text-xs text-[#849188]">Verifications recorded: <b>{profile.activity?.verifications_recorded ?? 0}</b>{profile.activity?.last_active ? ` · last active ${profile.activity.last_active}` : ''}</p>}
      </div>
    </div>
    <VisionAiCard auth={auth} /></>;
}

function VisionAiCard({ auth }: { auth?: { devRole?: 'consumer' | 'officer' | 'admin'; devUser?: string } }) {
  // Stage 2C §20: Vision AI diagnostics (status only — configuration
  // editing is never exposed in the browser; the API key never leaves
  // the server).
  const [status, setStatus] = useState<{ status: string; provider: string | null; model: string | null; reason: string } | null>(null);
  const [health, setHealth] = useState<{ status: string; reason?: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    api.visionStatus(auth).then(setStatus).catch(() => setStatus(null));
  }, []);
  const runTest = () => {
    setTesting(true);
    setError('');
    api.visionHealth(auth)
      .then((h) => setHealth(h))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setTesting(false));
  };
  return <div className="mt-5 rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft" data-testid="vision-ai-card">
    <h2 className="font-bold text-[#20382b]">Vision AI</h2>
    <p className="mt-1 text-[11px] text-[#849188]">Extraction assistant status — never a compliance decider, never shown credentials.</p>
    <div className="mt-4 grid gap-4 text-sm sm:grid-cols-3">
      <div><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">Status</p><p className="mt-1 font-bold text-[#20382b]" data-testid="vision-ai-status">{status ? status.status : 'Loading…'}</p></div>
      <div><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">Provider</p><p className="mt-1 font-bold text-[#20382b]" data-testid="vision-ai-provider">{status?.provider ?? '—'}</p></div>
      <div><p className="text-[11px] font-bold uppercase tracking-[.14em] text-[#87958c]">Model</p><p className="mt-1 font-bold text-[#20382b]" data-testid="vision-ai-model">{status?.model ?? '—'}</p></div>
    </div>
    {status?.reason && <p className="mt-3 text-xs text-[#68766f]" data-testid="vision-ai-reason">{status.reason}</p>}
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button onClick={runTest} disabled={testing} className="rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50" data-testid="button-test-vision">{testing ? 'Testing…' : 'Test Vision AI'}</button>
      {health && <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${health.status === 'AVAILABLE' ? 'bg-[#e3f7ed] text-[#08784e]' : 'bg-[#fff4cf] text-[#946b09]'}`} data-testid="vision-ai-health">{health.status}{health.reason ? ` — ${health.reason}` : ''}</span>}
    </div>
    {error && <p className="mt-3 text-xs text-[#8D3834]" data-testid="vision-ai-error">{error}</p>}
  </div>;
}

function EmptyState({ icon: Icon, title, text }: { icon: typeof ClipboardCheck; title: string; text: string }) { return <div className="rounded-xl border border-dashed border-[#cbdad0] bg-white px-6 py-14 text-center"><span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-[#eaf8f1] text-[#18B978]"><Icon size={22} /></span><h2 className="mt-4 font-bold text-[#30473a]">{title}</h2><p className="mx-auto mt-2 max-w-sm text-sm text-[#849188]">{text}</p></div>; }
function NotFoundPage() { return <div className="grid min-h-[100dvh] place-items-center bg-[#F7F8F6] px-6"><div className="max-w-md text-center"><Logo /><div className="mx-auto mt-20 grid h-16 w-16 place-items-center rounded-2xl bg-[#eaf8f1] text-[#18B978]"><Search size={28} /></div><p className="mt-6 font-mono text-xs text-[#18a86f]">404 / NOT IN THE RULEBOOK</p><h1 className="mt-3 text-3xl font-extrabold tracking-[-.05em] text-[#173a2a]">This page took a wrong turn.</h1><p className="mt-3 text-sm leading-relaxed text-[#718078]">The shelf you’re looking for isn’t here. Let’s get you back to a clearer view.</p><Link href="/" className="mt-7 inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-bold text-white" data-testid="link-not-found-home"><ArrowLeft size={16} />Back home</Link></div></div>; }

function HomeRedirect() {
  const { isLoaded, isSignedIn } = useAuth();
  const { role } = useWorkspaceRole();
  if (!isLoaded) return <LoadingScreen />;
  return isSignedIn
    ? <Redirect to={role === 'consumer' ? '/dashboard' : '/inspector/dashboard'} />
    : <Landing />;
}

function LoadingScreen() {
  return <div className="grid min-h-[100dvh] place-items-center bg-[#F7F8F6]"><div className="flex items-center gap-3 text-sm font-semibold text-[#426050]"><span className="h-3 w-3 animate-pulse rounded-full bg-[#18B978]" />Loading your workspace…</div></div>;
}

function ProtectedPage({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth();
  if (!isLoaded) return <LoadingScreen />;
  if (!isSignedIn) return <Redirect to="/sign-in" />;
  return <>{children}</>;
}

function ClerkQueryClientCacheInvalidator() {
  const { addListener } = useClerk();
  const prevUserIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const unsubscribe = addListener(({ user }) => {
      const userId = user?.id ?? null;
      if (prevUserIdRef.current !== undefined && prevUserIdRef.current !== userId) {
        queryClient.clear();
      }
      prevUserIdRef.current = userId;
    });
    return unsubscribe;
  }, [addListener]);
  return null;
}

/** Binds the Clerk session JWT to backend API calls (production auth path). */
function ApiAuthBinder() {
  const { getToken, isSignedIn } = useAuth();
  useEffect(() => {
    setAuthTokenProvider(isSignedIn ? () => getToken().catch(() => null) : null);
    return () => setAuthTokenProvider(null);
  }, [getToken, isSignedIn]);
  return null;
}

function Router() {
  // Role comes from the Clerk session (or explicit dev override); the shell
  // renders navigation accordingly and Guard blocks cross-workspace routes.
  const shell = (node: ReactNode) => <AppShell>{node}</AppShell>;
  const consumer = (node: ReactNode) => <Guard area="consumer">{node}</Guard>;
  const officer = (node: ReactNode) => <Guard area="officer">{node}</Guard>;
  return <RoutedErrorBoundary><Switch>
    <Route path="/" component={HomeRedirect} />
    <Route path="/sign-in/*?">{() => <AuthPage mode="login" />}</Route>
    <Route path="/sign-up/*?">{() => <AuthPage mode="signup" />}</Route>
    <Route path="/login">{() => <Redirect to="/sign-in" />}</Route>
    <Route path="/signup">{() => <Redirect to="/sign-up" />}</Route>
    <Route path="/dashboard">{() => <ProtectedPage>{shell(consumer(<ConsumerDashboard />))}</ProtectedPage>}</Route>
    <Route path="/verify-product">{() => <ProtectedPage>{shell(consumer(<VerifyProductPage />))}</ProtectedPage>}</Route>
    <Route path="/verify-product/:productId">{() => <ProtectedPage>{shell(consumer(<VerificationDetailsPage />))}</ProtectedPage>}</Route>
    <Route path="/nutrition">{() => <ProtectedPage>{shell(consumer(<NutritionLandingPage />))}</ProtectedPage>}</Route>
    <Route path="/nutrition/:productId">{() => <ProtectedPage>{shell(consumer(<NutritionPage />))}</ProtectedPage>}</Route>
    <Route path="/reports/:id">{() => <ProtectedPage>{shell(consumer(<ReportDetailPage />))}</ProtectedPage>}</Route>
    <Route path="/suggestions">{() => <ProtectedPage>{shell(consumer(<SuggestionsPage />))}</ProtectedPage>}</Route>
    <Route path="/upload">{() => <Redirect to="/verify-product" />}</Route>
    <Route path="/extraction">{() => <ProtectedPage>{shell(consumer(<ExtractionPage />))}</ProtectedPage>}</Route>
    <Route path="/analysis/:id">{() => <ProtectedPage>{shell(consumer(<AnalysisPage />))}</ProtectedPage>}</Route>
    {/* Lab check is an internal/demo workflow: route preserved for
        internal use, but unlinked from consumer navigation. */}
    <Route path="/lab-check">{() => <ProtectedPage>{shell(consumer(<LabCheckPage />))}</ProtectedPage>}</Route>
    <Route path="/complaint/new">{() => <ProtectedPage>{shell(consumer(<ComplaintNew />))}</ProtectedPage>}</Route>
    <Route path="/complaints">{() => <ProtectedPage>{shell(consumer(<ComplaintsPage />))}</ProtectedPage>}</Route>
    <Route path="/reports">{() => <ProtectedPage>{shell(consumer(<ReportsPage />))}</ProtectedPage>}</Route>
    <Route path="/profile">{() => <ProtectedPage>{shell(consumer(<ProfilePage />))}</ProtectedPage>}</Route>
    <Route path="/settings">{() => <ProtectedPage>{shell(<SettingsPage />)}</ProtectedPage>}</Route>
    <Route path="/inspector/dashboard">{() => <ProtectedPage>{shell(officer(<OfficerDashboard />))}</ProtectedPage>}</Route>
    <Route path="/inspector/inspections">{() => <ProtectedPage>{shell(officer(<InspectionsPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/inspections/:id">{() => <ProtectedPage>{shell(officer(<InspectionDetailPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/scan">{() => <ProtectedPage>{shell(officer(<OfficerScanPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/reports">{() => <ProtectedPage>{shell(officer(<OfficerReportsPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/rules">{() => <ProtectedPage>{shell(officer(<RulesPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/complaints">{() => <ProtectedPage>{shell(officer(<InspectorComplaints />))}</ProtectedPage>}</Route>
    <Route path="/inspector/complaints/:id">{() => <ProtectedPage>{shell(officer(<ComplaintDetailPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/suggestions">{() => <ProtectedPage>{shell(officer(<OfficerSuggestionsPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/suggestions/:id">{() => <ProtectedPage>{shell(officer(<OfficerSuggestionsDetailPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/enforcement">{() => <ProtectedPage>{shell(officer(<EnforcementQueuePage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/audit/:id">{() => <ProtectedPage>{shell(officer(<AuditPage />))}</ProtectedPage>}</Route>
    <Route path="/inspector/profile">{() => <ProtectedPage>{shell(officer(<OfficerProfilePage />))}</ProtectedPage>}</Route>
    <Route component={NotFoundPage} />
  </Switch></RoutedErrorBoundary>;
}
function RoutedErrorBoundary({ children }: { children: ReactNode }) { const [location] = useLocation(); return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>; }
function ClerkProviderWithRoutes() {
  const [, setLocation] = useLocation();
  return <ClerkProvider
    publishableKey={clerkPubKey}
    appearance={clerkAppearance}
    signInUrl={`${basePath}/sign-in`}
    signUpUrl={`${basePath}/sign-up`}
    localization={{
      signIn: { start: { title: 'Welcome back', subtitle: 'Sign in to access your account' } },
      signUp: { start: { title: 'Create your account', subtitle: 'Get started today' } },
    }}
    routerPush={(to) => setLocation(stripBase(to))}
    routerReplace={(to) => setLocation(stripBase(to), { replace: true })}
  >
    <QueryClientProvider client={queryClient}>
      <ClerkQueryClientCacheInvalidator />
      <ApiAuthBinder />
      <Router />
    </QueryClientProvider>
  </ClerkProvider>;
}
function App() { return <TooltipProvider><WouterRouter base={basePath}><ClerkProviderWithRoutes /></WouterRouter><Toaster /></TooltipProvider>; }
export default App;