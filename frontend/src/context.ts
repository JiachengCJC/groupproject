import { createContext, useContext } from 'react';
import type { AuthStatus, Config, LocalModels, Sources } from './types';
export interface WorkspaceContextValue {
  auth: AuthStatus;
  config: Config;
  sources: Sources;
  models: LocalModels | null;
  notify: (message: string, error?: boolean) => void;
  refreshSources: () => Promise<void>;
  refreshModels: () => Promise<void>;
  navigate: (view: string, id?: string) => void;
}
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error('Workspace context is unavailable');
  return value;
}
export function usePermissions() {
  const { auth } = useWorkspace();
  const role = auth.user?.role;
  return {
    canWrite: !auth.enabled || role === 'admin' || role === 'analyst',
    canReview: !auth.enabled || role === 'admin' || role === 'reviewer',
    isAdmin: !auth.enabled || role === 'admin',
  };
}
