import { useEffect, useRef, useState } from 'react';
import { createProject, listDataFiles, listProjects } from '../lib/api';
import type { Project } from '../lib/api';

type AppKind = 'recon' | 'finmod';
const pending = new Map<AppKind, Promise<Project>>();

async function ensureProject(kind: AppKind, defaultModel: string): Promise<Project> {
  const name = `__app_${kind}__`;
  const candidates = (await listProjects(true)).filter(project => project.name === name);
  if (candidates.length) {
    // Preserve the existing workspace containing the most imported files.
    const ranked = await Promise.all(candidates.map(async project => {
      try { return { project, count: (await listDataFiles(project.id)).files.length }; }
      catch { return { project, count: -1 }; }
    }));
    return ranked.reduce((best, item) => item.count > best.count ? item : best).project;
  }
  return createProject({ name, default_model: defaultModel || undefined,
    description: kind === 'recon' ? '数据核对与校验集成应用（隐藏）' : '财务建模与分析集成应用（隐藏）' });
}

export function useHiddenProject(kind: AppKind, defaultModel: string) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');
  const initialModel = useRef(defaultModel);
  useEffect(() => {
    let cancelled = false;
    let task = pending.get(kind);
    if (!task) {
      task = ensureProject(kind, initialModel.current);
      pending.set(kind, task);
      void task.finally(() => pending.delete(kind)).catch(() => {});
    }
    task.then(value => { if (!cancelled) setProject(value); }, cause => {
      if (!cancelled) setError(`应用数据区初始化失败: ${(cause as Error).message}`);
    });
    return () => { cancelled = true; };
  }, [kind]);
  return { project, setProject, error };
}
