/**
 * Workspace role resolution — single source for frontend role display.
 *
 * Authority order (backend remains the final authorization authority; the
 * frontend role only controls navigation rendering):
 *  1. Clerk session: `publicMetadata.role` (`officer` | `admin`), else `consumer`.
 *  2. Explicit local dev override, ONLY when VITE_DEMO_ROLE_SWITCH === 'true':
 *     sessionStorage 'legalakshi:dev-role'. Disabled by default and never
 *     shown in the normal UI.
 *  3. Default: `consumer`.
 *
 * localStorage is NEVER consulted: the old `nutricheck:role` key is ignored.
 */
import { useUser } from '@clerk/react';

export type WorkspaceRole = 'consumer' | 'officer' | 'admin';

const DEV_SWITCH_ENABLED =
  (import.meta.env.VITE_DEMO_ROLE_SWITCH as string | undefined) === 'true';
const DEV_ROLE_KEY = 'legalakshi:dev-role';

export function isDevRoleSwitchEnabled(): boolean {
  return DEV_SWITCH_ENABLED;
}

function roleFromClerk(publicMetadata: unknown): WorkspaceRole | null {
  if (publicMetadata && typeof publicMetadata === 'object') {
    const r = (publicMetadata as Record<string, unknown>).role;
    if (r === 'officer' || r === 'admin') return r;
  }
  return null;
}

export function resolveRole(
  clerkRole: WorkspaceRole | null,
  devRole: string | null,
): WorkspaceRole {
  if (clerkRole) return clerkRole;
  if (
    DEV_SWITCH_ENABLED &&
    (devRole === 'consumer' || devRole === 'officer' || devRole === 'admin')
  ) {
    return devRole;
  }
  return 'consumer';
}

/** React hook: current workspace role for navigation rendering. */
export function useWorkspaceRole(): {
  role: WorkspaceRole;
  source: 'clerk' | 'dev-override' | 'default';
} {
  const { user, isLoaded } = useUser();
  if (!isLoaded) return { role: 'consumer', source: 'default' };
  const clerkRole = roleFromClerk(user?.publicMetadata);
  if (clerkRole) return { role: clerkRole, source: 'clerk' };
  if (DEV_SWITCH_ENABLED) {
    try {
      const dev = sessionStorage.getItem(DEV_ROLE_KEY);
      if (dev === 'officer' || dev === 'admin' || dev === 'consumer') {
        return { role: dev, source: 'dev-override' };
      }
    } catch {
      /* storage unavailable */
    }
  }
  return { role: 'consumer', source: 'default' };
}

export function setDevRole(role: WorkspaceRole | null): void {
  try {
    if (role) sessionStorage.setItem(DEV_ROLE_KEY, role);
    else sessionStorage.removeItem(DEV_ROLE_KEY);
  } catch {
    /* storage unavailable */
  }
}

export function canAccess(role: WorkspaceRole, area: 'consumer' | 'officer' | 'admin'): boolean {
  if (area === 'consumer') return role === 'consumer' || role === 'admin';
  if (area === 'officer') return role === 'officer' || role === 'admin';
  return role === 'admin';
}
