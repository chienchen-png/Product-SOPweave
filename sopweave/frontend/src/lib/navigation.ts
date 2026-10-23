export type View = 'chat' | 'ai-config' | 'dashboard' | 'app-sales-check' | 'app-finmod' | 'sops';

export function viewPath(view: View, projectId?: string): string {
  switch (view) {
    case 'chat': return projectId ? `/projects/${encodeURIComponent(projectId)}` : '/projects';
    case 'ai-config': return '/settings/models';
    case 'app-sales-check': return '/apps/reconcile';
    case 'app-finmod': return '/apps/financial-modeling';
    case 'sops': return '/sops';
    default: return '/';
  }
}

export function readRoute(path: string): { view: View; projectId: string } {
  if (path.startsWith('/projects/')) {
    try { return { view: 'chat', projectId: decodeURIComponent(path.slice(10)) }; }
    catch { return { view: 'chat', projectId: '' }; }
  }
  const view: View = path === '/projects' ? 'chat'
    : path === '/settings/models' || path === '/settings/agent' ? 'ai-config'
    : path === '/apps/reconcile' ? 'app-sales-check'
    : path === '/apps/financial-modeling' ? 'app-finmod'
    : path === '/sops' ? 'sops' : 'dashboard';
  return { view, projectId: '' };
}
